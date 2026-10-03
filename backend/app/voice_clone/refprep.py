"""Reference-clip preparation for voice cloning: decode, light denoise, trim silence, pick the best ~15-20 s of speech.

Pure numpy + ffmpeg (no torch), so it runs in the API/worker process. The quality of a zero-shot clone depends far more
on a clean, continuous, single-speaker reference than on its length, so we pick the *cleanest contiguous run of phrases*
rather than the first N seconds of the training video."""
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

SR = 24000  # cloning models condition on 16 or 24 kHz; keep 24 kHz and let the engine resample
FRAME = int(0.030 * SR)
TARGET_S = 17.0
MIN_S = 8.0
MAX_S = 20.0
GAP_PAD_S = 0.12  # silence kept around each phrase
MAX_GAP_S = 0.45  # longer internal pauses are shortened to this


class ReferenceError(ValueError):
    pass


@dataclass
class RefStats:
    seconds: float
    source_seconds: float
    speech_fraction: float
    snr_db: float
    clipped_fraction: float
    start_s: float
    end_s: float
    denoised: bool

    def asdict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def decode(path: Path, sr: int = SR, denoise: bool = True, max_seconds: int = 600) -> np.ndarray:
    """Any audio/video file -> mono float32. Light denoise: 70 Hz high-pass + ffmpeg afftdn (gentle; heavy denoising
    leaves artefacts that cloning models faithfully copy)."""
    filt = "highpass=f=70,afftdn=nr=10:nf=-35" if denoise else "highpass=f=70"
    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-af", filt,
           "-t", str(max_seconds), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0 and denoise:  # ffmpeg build without afftdn: fall back to high-pass only
        return decode(path, sr, denoise=False, max_seconds=max_seconds)
    if r.returncode != 0:
        raise ReferenceError("could not decode audio: " + r.stderr.decode(errors="ignore")[-200:])
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def frame_db(x: np.ndarray) -> np.ndarray:
    n = len(x) // FRAME
    if n == 0:
        return np.zeros(0)
    fr = x[: n * FRAME].reshape(n, FRAME)
    return 10 * np.log10((fr ** 2).mean(axis=1) + 1e-10)


def speech_mask(db: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Frame-level speech mask from an adaptive threshold between the noise floor and the loud speech level.
    Returns (mask, noise_db, speech_db)."""
    if len(db) == 0:
        return np.zeros(0, bool), -100.0, -100.0
    noise, loud = float(np.percentile(db, 10)), float(np.percentile(db, 95))
    if loud - noise < 12:  # nearly flat signal: no usable speech/noise separation
        return np.zeros(len(db), bool), noise, loud
    thr = noise + 0.35 * (loud - noise)
    m = db > thr
    # close tiny holes (< 120 ms) and drop blips (< 120 ms)
    k = 4
    out = m.copy()
    i = 0
    while i < len(m):
        j = i
        while j < len(m) and m[j] == m[i]:
            j += 1
        if (j - i) < k and 0 < i and j < len(m):
            out[i:j] = not m[i]
        i = j
    return out, noise, loud


def phrases(mask: np.ndarray, min_pause_s: float = 0.40) -> list[tuple[int, int]]:
    """(start_frame, end_frame) runs of speech separated by pauses >= min_pause_s."""
    gap_frames = int(min_pause_s * SR / FRAME)
    runs, i, n = [], 0, len(mask)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j, last = i, i
        while j < n and (mask[j] or j - last <= gap_frames):
            if mask[j]:
                last = j
            j += 1
        runs.append((i, last + 1))
        i = last + 1
    return runs


def pick_best(x: np.ndarray, denoised: bool = True) -> tuple[np.ndarray, RefStats]:
    db = frame_db(x)
    mask, noise_db, loud_db = speech_mask(db)
    ph = [p for p in phrases(mask) if (p[1] - p[0]) * FRAME / SR >= 0.5]
    if not ph or mask.sum() * FRAME / SR < 3.0:
        raise ReferenceError("not enough clear speech in the recording to build a voice reference")
    fps = SR / FRAME
    best, best_score = None, -1e9
    for a in range(len(ph)):
        for b in range(a, len(ph)):
            span = (ph[b][1] - ph[a][0]) / fps
            speech = float(mask[ph[a][0]: ph[b][1]].sum()) / fps
            if span > MAX_S + 4:  # raw span; gaps get squeezed later
                break
            if speech < MIN_S and b < len(ph) - 1:
                continue
            seg = x[ph[a][0] * FRAME: ph[b][1] * FRAME]
            clip = float((np.abs(seg) > 0.98).mean())
            seg_db = db[ph[a][0]: ph[b][1]]
            snr = float(np.percentile(seg_db[mask[ph[a][0]: ph[b][1]]], 50) - noise_db) if mask[ph[a][0]: ph[b][1]].any() else 0
            dens = speech / max(span, 1e-3)
            score = min(snr, 40) + 20 * dens - 300 * clip - 1.5 * abs(min(speech, MAX_S) - TARGET_S)
            if speech < MIN_S:
                score -= 10 * (MIN_S - speech)
            if score > best_score:
                best, best_score = (a, b), score
    a, b = best
    pad = int(GAP_PAD_S * SR)
    parts = []
    for k in range(a, b + 1):
        s, e = ph[k]
        parts.append(x[max(0, s * FRAME - pad): min(len(x), e * FRAME + pad)])
        if k < b:  # keep a natural, capped pause between phrases
            gap = ph[k + 1][0] - ph[k][1]
            parts.append(np.zeros(int(min(gap * FRAME / SR, MAX_GAP_S) * SR) , np.float32))
    seg = np.concatenate(parts)
    if len(seg) > MAX_S * SR:  # hard cap: cut at the last pause before MAX_S
        seg = seg[: int(MAX_S * SR)]
    peak = float(np.abs(seg).max()) or 1.0
    if peak < 0.5 or peak > 0.95:  # normalise gently to -3 dBFS-ish peak
        seg = seg * (0.89 / peak)
    sm = mask[ph[a][0]: ph[b][1]]
    seg_db = db[ph[a][0]: ph[b][1]]
    stats = RefStats(
        seconds=len(seg) / SR, source_seconds=len(x) / SR, speech_fraction=float(sm.mean()),
        snr_db=float(np.percentile(seg_db[sm], 50) - noise_db) if sm.any() else 0.0,
        clipped_fraction=float((np.abs(seg) > 0.98).mean()), start_s=ph[a][0] * FRAME / SR, end_s=ph[b][1] * FRAME / SR,
        denoised=denoised)
    if stats.seconds < 4.0:
        raise ReferenceError("clean speech in the recording is too short (< 4 s) to clone a voice")
    return seg.astype(np.float32), stats


def write_wav(path: Path, x: np.ndarray, sr: int = SR) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    import wave

    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0, sr


def prepare_reference(src: Path, out_wav: Path, denoise: bool = True) -> RefStats:
    x = decode(src, SR, denoise=denoise)
    seg, st = pick_best(x, denoise)
    write_wav(out_wav, seg)
    return st
