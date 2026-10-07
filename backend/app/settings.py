"""Central env-driven configuration for safety/infra. Values are read lazily (each call) so tests and
operators can change the environment without re-importing. Secrets are never logged: use `redact()`.

VOCALFACE_ENV=production switches the safe defaults on (no unsigned files, no typed-phrase consent, ...).
"""
import os
import secrets
from pathlib import Path


def _flag(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def is_production() -> bool:
    return os.getenv("VOCALFACE_ENV", "dev").strip().lower() in ("prod", "production")


def data_dir() -> Path:
    from . import jobs
    return Path(jobs.DATA_DIR)


# ---- signing secret ----
_dev_secret: bytes | None = None


def secret_key() -> bytes:
    """HMAC key for signed file URLs. Production requires VOCALFACE_SECRET_KEY (>=16 chars); dev falls back to a
    random key persisted in <data>/.secret_key so URLs survive restarts."""
    k = os.getenv("VOCALFACE_SECRET_KEY", "")
    if k:
        if is_production() and len(k) < 16:
            raise RuntimeError("VOCALFACE_SECRET_KEY must be at least 16 characters in production")
        return k.encode()
    if is_production():
        raise RuntimeError("VOCALFACE_SECRET_KEY is required when VOCALFACE_ENV=production")
    global _dev_secret
    if _dev_secret is None:
        p = data_dir() / ".secret_key"
        try:
            if p.exists():
                _dev_secret = p.read_bytes().strip()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                _dev_secret = secrets.token_hex(32).encode()
                p.write_bytes(_dev_secret)
                os.chmod(p, 0o600)
        except OSError:
            _dev_secret = secrets.token_hex(32).encode()
    return _dev_secret


def validate_production() -> list[str]:
    """Problems that must stop a production boot. Empty list = ok."""
    if not is_production():
        return []
    errs = []
    try:
        secret_key()
    except RuntimeError as e:
        errs.append(str(e))
    if os.getenv("VOCALFACE_CORS_ORIGINS", "").strip() in ("", "*"):
        errs.append("VOCALFACE_CORS_ORIGINS must list your dashboard origin(s), not '*' or empty")
    return errs


# ---- feature flags (dev-friendly default, locked down in production) ----
def allow_public_files() -> bool:
    """Unsigned /v1/files/* (legacy, guessable-id). Default on in dev, off in production."""
    return _flag("VOCALFACE_ALLOW_PUBLIC_FILES", not is_production())


def allow_typed_consent() -> bool:
    """Old typed-phrase consent path (POST /consent without audio verification)."""
    return _flag("VOCALFACE_ALLOW_TYPED_CONSENT", not is_production())


def allow_key_in_url() -> bool:
    """Legacy `?api_key=` on the WebSocket URL (leaks into proxy logs/history). Off unless VOCALFACE_ALLOW_KEY_IN_URL=1; clients use
    POST /v1/realtime/ticket instead."""
    return _flag("VOCALFACE_ALLOW_KEY_IN_URL", False)


def signed_url_ttl() -> int:
    return _int("VOCALFACE_SIGNED_URL_TTL", 3600)


def voice_match_mode() -> str:
    """enforce | warn | off. Default: enforce in production, warn in dev."""
    m = os.getenv("VOCALFACE_CONSENT_VOICE_MATCH", "").strip().lower()
    if m in ("enforce", "warn", "off"):
        return m
    return "enforce" if is_production() else "warn"


def voice_match_threshold() -> float:
    try:
        return float(os.getenv("VOCALFACE_VOICE_MATCH_THRESHOLD", "0.50"))
    except ValueError:
        return 0.50


def ratelimit_enabled() -> bool:
    return _flag("VOCALFACE_RATE_LIMIT", True)


def trust_proxy() -> bool:
    return _flag("VOCALFACE_TRUST_PROXY", False)


def max_body_bytes() -> int:
    return _int("VOCALFACE_MAX_BODY_BYTES", 2 * 1024 * 1024)


def max_upload_bytes() -> int:
    return _int("VOCALFACE_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)


def redact(s: str) -> str:
    """Mask api keys / secrets in anything that may be logged."""
    import re
    s = re.sub(r"(mk_|sk_|whsec_|rzp_)[A-Za-z0-9_\-]{6,}", lambda m: m.group(1) + "***", s or "")
    # key-like query/header values whatever their prefix: ?api_key=..., ?token=..., ?sig=... (signed URL), Bearer ...
    s = re.sub(r"(?i)\b(api_key|apikey|x-api-key|token|ticket|sig|secret|password)([=:]\s*)[^\s&\"',;}]+", lambda m: m.group(1) + m.group(2) + "***", s)
    return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]{8,}", "Bearer ***", s)
