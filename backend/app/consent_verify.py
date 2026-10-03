"""Server-side verification of a spoken consent recording: ASR + speaker match against the training video."""
import hashlib
import ipaddress
import os
import shutil
import socket
import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np

from . import settings, voiceprint

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


def _check_url_safe(url: str) -> None:
    """SSRF guard for server-side fetches (production only: dev needs localhost video servers)."""
    if not settings.is_production() or os.getenv("MIRAGE_ALLOW_PRIVATE_URLS") == "1":
        return
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise ValueError("training video host does not resolve")
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("training video URL points at a private address")


def fetch_train_video(url: str, dest: Path) -> None:
    if url.startswith(("http://", "https://")):
        got = 0
        for _hop in range(4):  # follow redirects manually so every hop passes the SSRF check
            _check_url_safe(url)
            with httpx.stream("GET", url, follow_redirects=False, timeout=120) as r:
                if r.is_redirect:
                    url = str(r.url.join(r.headers.get("location", "")))
                    continue
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes():
                        got += len(chunk)
                        if got > MAX_TRAIN_BYTES:
                            raise ValueError("training video too large")
                        f.write(chunk)
                return
        raise ValueError("too many redirects")
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
