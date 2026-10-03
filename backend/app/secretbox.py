"""Reversible encryption for stored secrets (custom-LLM api keys, tool secrets).

Key: MIRAGE_SECRET_KEY env, else a random key persisted in $MIRAGE_DATA/secret.key (0600).
Uses Fernet (cryptography) when installed; otherwise an encrypt-then-MAC construction built from
stdlib HMAC-SHA256 (CTR-style keystream + MAC). Ciphertexts are tagged ("f1:" / "h1:") so both decode.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

_key_cache: bytes | None = None


def _master() -> bytes:
    global _key_cache
    if _key_cache:
        return _key_cache
    env = os.environ.get("MIRAGE_SECRET_KEY")
    if env:
        _key_cache = hashlib.sha256(env.encode()).digest()
        return _key_cache
    data_dir = Path(os.environ.get("MIRAGE_DATA", Path(__file__).resolve().parents[1] / "data"))
    p = data_dir / "secret.key"
    if p.exists():
        raw = p.read_bytes()
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
        raw = secrets.token_bytes(32)
        p.write_bytes(raw)
        try:
            p.chmod(0o600)
        except OSError:
            pass
    _key_cache = hashlib.sha256(raw).digest()
    return _key_cache


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out, ctr = b"", 0
    while len(out) < n:
        out += hmac.new(key, nonce + ctr.to_bytes(8, "big"), hashlib.sha256).digest()
        ctr += 1
    return out[:n]


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    try:
        from cryptography.fernet import Fernet

        return "f1:" + Fernet(base64.urlsafe_b64encode(_master())).encrypt(plain.encode()).decode()
    except ImportError:
        pass
    k = _master()
    nonce = secrets.token_bytes(16)
    data = plain.encode()
    ct = bytes(a ^ b for a, b in zip(data, _stream(k + b"enc", nonce, len(data))))
    tag = hmac.new(k + b"mac", nonce + ct, hashlib.sha256).digest()
    return "h1:" + base64.urlsafe_b64encode(nonce + tag + ct).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    if token.startswith("f1:"):
        from cryptography.fernet import Fernet

        return Fernet(base64.urlsafe_b64encode(_master())).decrypt(token[3:].encode()).decode()
    if token.startswith("h1:"):
        raw = base64.urlsafe_b64decode(token[3:])
        nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
        k = _master()
        if not hmac.compare_digest(tag, hmac.new(k + b"mac", nonce + ct, hashlib.sha256).digest()):
            raise ValueError("secret failed integrity check")
        return bytes(a ^ b for a, b in zip(ct, _stream(k + b"enc", nonce, len(ct)))).decode()
    raise ValueError("unknown secret format")
