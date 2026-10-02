"""Moderation, rate limiting, audit helpers, replica-consent gate."""
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from sqlmodel import Session, select

from .models_billing import AuditLog, ConsentRecord

# ---------- moderation ----------
DEFAULT_BLOCKLIST = [
    r"\bchild\s+(porn|sexual)", r"\bcsam\b", r"\bkill\s+yourself\b",
    r"\bhow\s+to\s+(make|build)\s+a\s+(bomb|nerve\s+agent)\b",
    r"\bsend\s+(me\s+)?your\s+(password|otp|cvv)\b",
    r"\bi\s+am\s+(the\s+)?(president|ceo)\b.{0,40}\bwire\b",
]


@dataclass
class ModerationResult:
    allowed: bool
    reasons: list
    classifier: str = "blocklist"


def _blocklist():
    extra = [w.strip() for w in os.getenv("MIRAGE_BLOCKLIST", "").split(",") if w.strip()]
    return [re.compile(p, re.I) for p in DEFAULT_BLOCKLIST] + [re.compile(re.escape(w), re.I) for w in extra]


def moderate(text: str) -> ModerationResult:
    reasons = [f"blocklist:{p.pattern}" for p in _blocklist() if p.search(text)]
    classifier = "blocklist"
    model = os.getenv("MIRAGE_MODERATION_OLLAMA_MODEL")
    if model and not reasons:
        classifier = f"blocklist+ollama/{model}"
        try:
            r = httpx.post(os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/generate", timeout=20, json={
                "model": model, "stream": False,
                "prompt": "You are a content safety classifier. Answer with exactly SAFE or UNSAFE "
                          "(UNSAFE = sexual content involving minors, violence incitement, fraud/impersonation "
                          "scams, harassment).\nText:\n" + text[:4000] + "\nAnswer:"})
            ans = r.json().get("response", "").strip().upper()
            if ans.startswith("UNSAFE"):
                reasons.append("ollama:unsafe")
        except Exception:
            classifier += "(unavailable, blocklist only)"
    return ModerationResult(not reasons, reasons, classifier)


def moderate_or_raise(text: str) -> None:
    res = moderate(text)
    if not res.allowed:
        raise HTTPException(422, {"error": "content rejected by moderation", "reasons": res.reasons})


# ---------- rate limiting (in-process sliding window) ----------
class RateLimiter:
    def __init__(self):
        self._hits: dict = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float = 60.0, now_ts: float | None = None) -> bool:
        t = now_ts if now_ts is not None else time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= t - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(t)
            return True


limiter = RateLimiter()


def enforce_rate_limit(api_key: str, limit: int = 60, window: float = 60.0, bucket: str = "default") -> None:
    """Call inside a route/dependency. Single-process only; use Redis for multi-worker."""
    if not limiter.check(f"{bucket}:{api_key}", limit, window):
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(int(window))})


# ---------- audit ----------
def audit(session: Session, account_id: str, action: str, target: str = "", detail: str = "") -> None:
    session.add(AuditLog(account_id=account_id, action=action, target=target, detail=detail[:2000]))
    session.commit()


# ---------- consent gate ----------
class ConsentRequired(HTTPException):
    def __init__(self, replica_id: str):
        super().__init__(403, f"replica {replica_id} has no valid consent record; record consent first")


def has_consent(session: Session, replica_id: str) -> bool:
    return session.exec(select(ConsentRecord).where(
        ConsentRecord.replica_id == replica_id, ConsentRecord.revoked == False)).first() is not None  # noqa: E712


def require_consent(replica_id: str, session: Session | None = None) -> None:
    """Raise ConsentRequired unless a non-revoked consent record exists.
    Worker owner: call before setting Replica.status = 'ready'."""
    if session is not None:
        ok = has_consent(session, replica_id)
    else:
        from . import db
        with Session(db.engine) as s:
            ok = has_consent(s, replica_id)
    if not ok:
        raise ConsentRequired(replica_id)
