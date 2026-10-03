"""Server-side verification of a spoken consent recording: ASR + speaker match against the training video."""
import hashlib
import os
import shutil
import threading
from pathlib import Path

import numpy as np

from . import netguard, settings, voiceprint

MAX_TRAIN_BYTES = int(os.getenv("MIRAGE_MAX_TRAIN_VIDEO_BYTES", str(300 * 1024 * 1024)))
_whisper = None
_wlock = threading.Lock()


def consent_dir(rid: str) -> Path:
    d = settings.data_dir() / "consent" / rid
    d.mkdir(parents=True, exist_ok=True)
    return d


def transcribe(wav: np.ndarray) -> str:
    global _whisper
    with _wlock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel(os.getenv("MIRAGE_CONSENT_WHISPER", "base.en"), device="cpu", compute_type="int8")
    segs, _ = _whisper.transcribe(wav, language="en", vad_filter=True)
    return " ".join(s.text.strip() for s in segs).strip()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_train_video(url: str, dest: Path) -> None:
    """http(s): SSRF-guarded streaming download (netguard: resolve-and-pin, every redirect hop checked, size capped).
    Local paths: dev only."""
    if url.startswith(("http://", "https://")):
        try:
            netguard.download(url, dest, MAX_TRAIN_BYTES, timeout=120)
        except netguard.UnsafeURL as e:
            raise ValueError(f"training video URL refused: {e}") from e
    elif not settings.is_production():  # local paths only for dev (jobs.fetch_video accepts them too)
        src = Path(url[7:] if url.startswith("file://") else url).expanduser()
        if not src.is_file():
            raise ValueError("training video not found")
        shutil.copyfile(src, dest)
    else:
        raise ValueError("only http(s) training video URLs are accepted in production")


def train_voiceprint(rid: str, url: str) -> np.ndarray:
    """Embedding of the voice in the replica's training video (cached per replica+url)."""
    d = consent_dir(rid)
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    cache = d / f"train_{key}.npy"
    if cache.exists():
        return np.load(cache)
    tmp = d / f"train_{key}.src"
    try:
        fetch_train_video(url, tmp)
        e = voiceprint.embed(voiceprint.decode_audio(tmp, 60))
    finally:
        tmp.unlink(missing_ok=True)
    np.save(cache, e)
    return e
