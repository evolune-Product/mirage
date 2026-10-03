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
    r"\b(i|we)\s+(will|am\s+going\s+to|'ll)\s+(kill|murder|shoot|stab|hurt|beat)\b",
    r"\b(make|build|making|building)\s+(a\s+)?(bomb|explosive|weapon)s?\b",
    r"\b(kill|murder|hurt|harm)\s+(you|him|her|them|people|everyone)\b",
    r"\b(otp|one[- ]time\s+password|cvv|pin\s+number)\b.{0,40}\b(share|send|tell|give|read)\b",
    r"\b(share|send|tell|give|read)\b.{0,40}\b(otp|one[- ]time\s+password|cvv|pin\s+number)\b",
    r"\b(this\s+is|i\s+am)\s+(your\s+)?(bank|police|tax\s+officer)\b.{0,60}\b(transfer|pay|send)\b",
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
    model = os.getenv("MIRAGE_MODERATION_OLLAMA_MODEL", "qwen3:8b")  # "" disables; 1B models misclassify too often
    if model and not reasons:
        classifier = f"blocklist+ollama/{model}"
        try:
            r = httpx.post(os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/generate", timeout=20, json={
                "model": model, "stream": False,
                "options": {"temperature": 0, "num_predict": 400}, "think": False,
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
        self._win: dict = {}
        self._lock = threading.Lock()
        self._calls = 0

    def check_ex(self, key: str, limit: int, window: float = 60.0, now_ts: float | None = None) -> tuple[bool, float]:
        """-> (allowed, retry_after_seconds). Records the hit only when allowed."""
        t = now_ts if now_ts is not None else time.monotonic()
        with self._lock:
            self._calls += 1
            if self._calls % 2000 == 0:
                self._prune(t)
            q = self._hits[key]
            self._win[key] = window
            while q and q[0] <= t - window:
                q.popleft()
            if len(q) >= limit:
                return False, max(q[0] + window - t, 0.0)
            q.append(t)
            return True, 0.0

    def check(self, key: str, limit: int, window: float = 60.0, now_ts: float | None = None) -> bool:
        return self.check_ex(key, limit, window, now_ts)[0]

    def _prune(self, t: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= t - self._win.get(k, 60.0)]:
            self._hits.pop(k, None)
            self._win.pop(k, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear(); self._win.clear()


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


# ---------- consent phrase matching (ASR-tolerant) ----------
CODE_WORDS = ["amber", "river", "stone", "cedar", "lunar", "pixel", "ember", "north"]


def norm_tokens(t: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).split()


def phrase_code(phrase: str) -> list[str]:
    m = re.search(r"code is ([a-z]+)\W+([a-z]+)\W+([a-z]+)", phrase.lower())
    return list(m.groups()) if m else []


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def _word_matches(tok: str, want: str) -> bool:
    if tok == want:
        return True
    if tok in CODE_WORDS:  # an exact *other* code word is never a typo of this one
        return False
    r = _ratio(tok, want)
    return r >= 0.8 and r > max((_ratio(tok, w) for w in CODE_WORDS if w != want), default=0)


def code_words_present(phrase: str, transcript: str) -> bool:
    """All three random code words must appear, in order. Tolerates one-character ASR slips but never
    accepts a different code word (amber vs ember are told apart)."""
    want = phrase_code(phrase)
    if not want:
        return False
    toks = norm_tokens(transcript)
    pos = 0
    for w in want:
        for i in range(pos, len(toks)):
            if _word_matches(toks[i], w):
                pos = i + 1
                break
        else:
            return _code_in_compact(want, transcript)
    return True


def _code_in_compact(want: list[str], transcript: str) -> bool:
    """ASR sometimes glues spoken words together ('cederemberpixel'): exact substrings, in order, after 'code'."""
    c = "".join(norm_tokens(transcript))
    i = c.rfind("code")
    c = c[i + 4:] if i >= 0 else c
    pos = 0
    for w in want:
        j = c.find(w, pos)
        if j < 0:
            return False
        pos = j + len(w)
    return True


def phrase_score(phrase: str, transcript: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, norm_tokens(phrase), norm_tokens(transcript)).ratio()
