"""Cheap AUDIO-DRIVEN mouth motion for LivePortrait (numpy + pickle only, no extra models).

LivePortrait itself is not audio-driven: it transfers motion from a driving video/template.
Here we build a synthetic motion template whose mouth keypoint expression is interpolated
between a 'closed' and an 'open' pose (taken from LivePortrait's shipped open_lip.pkl)
by the speech loudness envelope. This is an amplitude-driven jaw flap, NOT phoneme-accurate
lip-sync (no visemes: 'm' vs 'a' look alike, only open/close timing follows the audio).
It looks acceptable for short talking-head clips and is far better than a fixed idle loop.
Real lip-sync would need Wav2Lip / MuseTalk / SadTalker / Hallo (GPU, not on this M1).
"""
from __future__ import annotations

import pickle
import wave
from pathlib import Path

import numpy as np

MOUTH_IDX = [6, 12, 14, 17, 19, 20]  # LivePortrait implicit-keypoint indices that move with the lips
DEFAULT_BASE = Path(__file__).parent / "LivePortrait/assets/examples/driving/open_lip.pkl"


def read_wav_mono(path: str) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dt = {1: np.uint8, 2: np.int16, 4: np.int32}[sw]
    x = np.frombuffer(raw, dtype=dt).astype(np.float32)
    if sw == 1:
        x = x - 128
    x /= float(np.iinfo(dt).max if sw > 1 else 127)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def envelope(x: np.ndarray, sr: int, fps: float, smooth: int = 3) -> np.ndarray:
    """Per-video-frame mouth openness in [0,1]."""
    n = max(int(np.ceil(len(x) / sr * fps)), 1)
    hop = sr / fps
    rms = np.array([np.sqrt(np.mean(x[int(i * hop):int((i + 1) * hop)] ** 2) + 1e-12) if int(i * hop) < len(x) else 0.0
                    for i in range(n)])
    ref = np.percentile(rms, 95) if rms.max() > 0 else 1.0
    env = np.clip(rms / max(ref, 1e-6), 0, 1)
    env[env < 0.08] = 0.0  # noise gate -> closed mouth in pauses
    env = env ** 0.8
    if smooth > 1:
        k = np.hanning(smooth + 2)[1:-1]
        env = np.convolve(env, k / k.sum(), mode="same")
    return np.clip(env, 0, 1)


def make_template(wav_path: str, out_pkl: str, fps: float = 12.0, base_pkl: str | Path = DEFAULT_BASE, max_open: float = 0.9) -> dict:
    base = pickle.load(open(base_pkl, "rb"))
    exps = np.stack([m["exp"] for m in base["motion"]])  # (n,1,21,3)
    mouth = exps[:, 0, MOUTH_IDX, :].reshape(len(exps), -1)
    d = np.linalg.norm(mouth[:, None] - mouth[None], axis=-1)
    ic, io = np.unravel_index(np.argmax(d), d.shape)
    closed, opened = exps[ic], exps[io]
    x, sr = read_wav_mono(wav_path)
    env = envelope(x, sr, fps)
    env[0] = 0.0  # frame 0 is the reference for relative motion -> must be the neutral pose
    ref = base["motion"][ic]
    motion = []
    for a in env:
        e = closed.copy()
        e[0, MOUTH_IDX, :] = closed[0, MOUTH_IDX, :] + a * max_open * (opened[0, MOUTH_IDX, :] - closed[0, MOUTH_IDX, :])
        motion.append({"scale": ref["scale"], "R": ref["R"], "exp": e.astype(np.float32), "t": ref["t"]})
    tpl = {"n_frames": len(motion), "output_fps": int(round(fps)), "motion": motion,
           # required by LivePortrait's loader; only used when eye/lip retargeting flags are on (they are off)
           "c_eyes_lst": [np.zeros((1, 2), np.float32)] * len(motion), "c_lip_lst": [np.zeros((1, 1), np.float32)] * len(motion)}
    pickle.dump(tpl, open(out_pkl, "wb"))
    return {"frames": len(motion), "fps": fps, "mean_open": float(env.mean()), "closed_idx": int(ic), "open_idx": int(io)}
