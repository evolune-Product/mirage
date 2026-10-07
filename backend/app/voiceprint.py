"""Speaker verification with the WeSpeaker ResNet34 ONNX model (numpy Kaldi-style fbank + onnxruntime).

Model file: models/wespeaker_resnet34_lm.onnx (26 MB, Apache-2.0, `Wespeaker/wespeaker-voxceleb-resnet34-LM`
on Hugging Face). If it is missing the functions raise VoiceModelUnavailable; callers decide the policy.
"""
import os
import subprocess
import threading
from pathlib import Path

import numpy as np

MODEL_PATH = Path(os.getenv("VOCALFACE_SPEAKER_MODEL", "")) if os.getenv("VOCALFACE_SPEAKER_MODEL") else \
    Path(__file__).resolve().parents[2] / "models" / "wespeaker_resnet34_lm.onnx"
SR = 16000


class VoiceModelUnavailable(RuntimeError):
    pass


_sess = None
_lock = threading.Lock()


def _session():
    global _sess
    with _lock:
        if _sess is None:
            if not MODEL_PATH.exists():
                raise VoiceModelUnavailable(f"speaker model not found at {MODEL_PATH}")
            import onnxruntime as ort
            _sess = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
        return _sess


def available() -> bool:
    return MODEL_PATH.exists()


def decode_audio(path: Path, max_seconds: int = 30) -> np.ndarray:
    """Any audio/video container -> mono float32 16 kHz via ffmpeg."""
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
                        "-t", str(max_seconds), "-f", "s16le", "-"], capture_output=True, timeout=300)
    if r.returncode != 0:
        raise ValueError("could not decode audio: " + r.stderr.decode(errors="ignore")[-200:])
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _mel_banks(n_mels=80, n_fft=512, low=20.0, high=SR / 2):
    mel = lambda f: 1127.0 * np.log(1.0 + f / 700.0)
    pts = np.linspace(mel(low), mel(high), n_mels + 2)
    bin_hz = SR / n_fft
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    freqs = mel(np.arange(n_fft // 2 + 1) * bin_hz)
    for m in range(n_mels):
        l, c, r = pts[m], pts[m + 1], pts[m + 2]
        up = (freqs - l) / (c - l)
        down = (r - freqs) / (r - c)
        fb[m] = np.maximum(0, np.minimum(up, down))
    return fb


_FB = None


def fbank(wav: np.ndarray) -> np.ndarray:
    """Kaldi-compatible 80-dim log-mel fbank (povey window, preemph 0.97, no dither) + mean normalisation."""
    global _FB
    if _FB is None:
        _FB = _mel_banks()
    x = wav * 32768.0
    flen, fshift = 400, 160
    if len(x) < flen:
        raise ValueError("audio too short")
    n = 1 + (len(x) - flen) // fshift
    idx = np.arange(flen)[None, :] + fshift * np.arange(n)[:, None]
    fr = x[idx].astype(np.float32)
    fr = fr - fr.mean(axis=1, keepdims=True)
    fr = np.concatenate([fr[:, :1] * (1 - 0.97), fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1)
    win = np.power(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(flen) / (flen - 1)), 0.85).astype(np.float32)
    fr = fr * win
    spec = np.abs(np.fft.rfft(fr, n=512, axis=1)) ** 2
    mel = np.log(np.maximum(spec @ _FB.T, np.finfo(np.float32).eps))
    return (mel - mel.mean(axis=0, keepdims=True)).astype(np.float32)


def speech_only(wav: np.ndarray, frame=480, rel_db=-35.0) -> np.ndarray:
    """Crude energy gate: drop frames far below the loudest ones (silence, room tone)."""
    n = len(wav) // frame
    if n == 0:
        return wav
    fr = wav[: n * frame].reshape(n, frame)
    e = 10 * np.log10((fr ** 2).mean(axis=1) + 1e-10)
    keep = e > (e.max() + rel_db)
    return fr[keep].reshape(-1) if keep.any() else wav[:0]


def embed(wav: np.ndarray) -> np.ndarray:
    """-> L2-normalised 256-d speaker embedding. Raises ValueError if <1.5 s of speech."""
    wav = speech_only(wav)
    if len(wav) < SR * 1.5:
        raise ValueError("not enough speech to compute a voiceprint")
    wav = wav[: SR * 20]
    f = fbank(wav)[None]
    e = _session().run(None, {"feats": f})[0][0]
    return e / (np.linalg.norm(e) + 1e-9)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def compare_files(a: Path, b: Path) -> float:
    return similarity(embed(decode_audio(a)), embed(decode_audio(b)))
