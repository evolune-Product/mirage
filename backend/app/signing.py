"""HMAC-signed, expiring URLs for replica face / rendered video files."""
import hashlib
import hmac
import re
import time
from urllib.parse import quote

from . import settings

_FILE_RE = re.compile(r"^/v1/files/(videos/[a-z]+_[0-9a-f]+\.mp4|replicas/[a-z]+_[0-9a-f]+/face\.png"
                      r"|creative/replicas/[a-z]+_[0-9a-f]+/idle\.mp4|creative/videos/[a-z]+_[0-9a-f]+/(thumbnail\.jpg|captions\.srt))$")


def valid_file_path(path: str) -> bool:
    return bool(_FILE_RE.match(path))


def _mac(path: str, exp: int) -> str:
    return hmac.new(settings.secret_key(), f"{path}|{exp}".encode(), hashlib.sha256).hexdigest()[:43]


def sign_path(path: str, ttl: int | None = None, now: float | None = None) -> str:
    """'/v1/files/videos/v_1.mp4' -> '/v1/files/videos/v_1.mp4?exp=...&sig=...'"""
    base = path.split("?", 1)[0]
    exp = int((now if now is not None else time.time()) + (ttl if ttl is not None else settings.signed_url_ttl()))
    return f"{base}?exp={exp}&sig={quote(_mac(base, exp))}"


def verify(path: str, exp: str | None, sig: str | None, now: float | None = None) -> bool:
    try:
        e = int(exp or "")
    except ValueError:
        return False
    if e < (now if now is not None else time.time()) or not sig:
        return False
    return hmac.compare_digest(_mac(path, e), sig)
