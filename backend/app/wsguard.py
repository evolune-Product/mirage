"""WebSocket abuse controls: single-use tickets, connection-rate limits, per-account concurrent-session caps and per-connection
message/byte budgets. All state is in-process (per worker, resets on restart), like the HTTP rate limiter.

Env (limit/window, e.g. VOCALFACE_WS_CONNECT_IP=60/60):
  VOCALFACE_WS_CONNECT_IP       connection attempts per client IP            (default 120/60)
  VOCALFACE_WS_CONNECT_ACCOUNT  authenticated connection attempts per account (default 60/60)
  VOCALFACE_WS_AUTH_FAIL_IP     failed authentications per IP                 (default 15/60)
  VOCALFACE_WS_MAX_SESSIONS     concurrent live conversations per account     (default 5; 0 = unlimited)
  VOCALFACE_WS_MSG_RATE         binary frames/s sustained "rate/burst"        (default 200/400)
  VOCALFACE_WS_TEXT_RATE        text frames/s sustained "rate/burst"          (default 40/80)
  VOCALFACE_WS_BYTES_RATE       bytes/s sustained "rate/burst"                (default 2000000/4000000)
  VOCALFACE_WS_MAX_BIN / VOCALFACE_WS_MAX_TEXT  largest single frame            (default 65536 / 1000000)
  VOCALFACE_WS_TICKET_TTL       ticket lifetime seconds                        (default 30)
"""
import os
import secrets
import threading
import time
from collections import Counter

from .safety import limiter

CLOSE_RATE = 4429        # connection/message rate or concurrent-session cap (terminal for the bundled client)
CLOSE_TOO_BIG = 1009     # standard "message too big"


def _pair(name: str, default: tuple[float, float]) -> tuple[float, float]:
    raw = os.getenv(name, "")
    try:
        a, b = raw.split("/")
        return float(a), float(b)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


# ---------------- connection-level limits ----------------
def connect_allowed(ip: str) -> bool:
    lim, win = _pair("VOCALFACE_WS_CONNECT_IP", (120, 60))
    return limiter.check(f"ws:ip:{ip}", int(lim), win)


def account_connect_allowed(account_id: str) -> bool:
    lim, win = _pair("VOCALFACE_WS_CONNECT_ACCOUNT", (60, 60))
    return limiter.check(f"ws:acc:{account_id}", int(lim), win)


def auth_failure_allowed(ip: str) -> bool:
    """Record a failed authentication; False once the IP has burned its budget (brute-forcing keys/tickets)."""
    lim, win = _pair("VOCALFACE_WS_AUTH_FAIL_IP", (15, 60))
    return limiter.check(f"ws:fail:{ip}", int(lim), win)


# ---------------- concurrent sessions per account ----------------
_lock = threading.Lock()
_live: dict[str, Counter] = {}  # account id -> {conversation id: live sockets}; a reconnect to the same conversation is not a new session


def max_sessions() -> int:
    return _int("VOCALFACE_WS_MAX_SESSIONS", 5)


def acquire_session(account_id: str, cid: str) -> bool:
    cap = max_sessions()
    with _lock:
        c = _live.setdefault(account_id, Counter())
        if cap > 0 and cid not in c and len(c) >= cap:
            return False
        c[cid] += 1
        return True


def release_session(account_id: str, cid: str) -> None:
    with _lock:
        c = _live.get(account_id)
        if not c:
            return
        c[cid] -= 1
        if c[cid] <= 0:
            del c[cid]
        if not c:
            _live.pop(account_id, None)


def live_sessions(account_id: str) -> int:
    with _lock:
        return len(_live.get(account_id, ()))


# ---------------- single-use WebSocket tickets ----------------
_tickets: dict[str, tuple[str, str, float]] = {}  # ticket -> (account id, conversation id, expiry monotonic)
MAX_OUTSTANDING = 50


def ticket_ttl() -> int:
    return _int("VOCALFACE_WS_TICKET_TTL", 30)


def mint_ticket(account_id: str, cid: str) -> str | None:
    """None when the account already holds too many unused tickets (bounds memory, blunts abuse)."""
    now = time.monotonic()
    with _lock:
        for k in [k for k, v in _tickets.items() if v[2] <= now]:
            del _tickets[k]
        if sum(1 for v in _tickets.values() if v[0] == account_id) >= MAX_OUTSTANDING:
            return None
        t = "wt_" + secrets.token_urlsafe(24)
        _tickets[t] = (account_id, cid, now + ticket_ttl())
        return t


def redeem_ticket(ticket: str, cid: str) -> str | None:
    """-> account id, consuming the ticket. A ticket for a different conversation is consumed too (no probing)."""
    with _lock:
        v = _tickets.pop(ticket, None)
    if v is None or v[2] <= time.monotonic() or v[1] != cid:
        return None
    return v[0]


def reset() -> None:
    with _lock:
        _tickets.clear(); _live.clear()


# ---------------- per-connection message budget ----------------
class _Bucket:
    def __init__(self, rate: float, burst: float, clock):
        self.rate, self.burst, self.clock = rate, burst, clock
        self.level, self.t = burst, clock()

    def take(self, n: float = 1.0) -> bool:
        now = self.clock()
        self.level = min(self.burst, self.level + (now - self.t) * self.rate)
        self.t = now
        if self.level < n:
            return False
        self.level -= n
        return True


class MsgGuard:
    """One per socket. check(msg) -> None when ok, else (close_code, reason). msg is an ASGI websocket.receive dict."""

    def __init__(self, clock=time.monotonic):
        r, b = _pair("VOCALFACE_WS_MSG_RATE", (200, 400))
        tr, tb = _pair("VOCALFACE_WS_TEXT_RATE", (40, 80))
        br, bb = _pair("VOCALFACE_WS_BYTES_RATE", (2_000_000, 4_000_000))
        self.bin, self.text, self.bytes = _Bucket(r, b, clock), _Bucket(tr, tb, clock), _Bucket(br, bb, clock)
        self.max_bin, self.max_text = _int("VOCALFACE_WS_MAX_BIN", 65536), _int("VOCALFACE_WS_MAX_TEXT", 1_000_000)

    def check(self, msg: dict):
        b, t = msg.get("bytes"), msg.get("text")
        if b is not None:
            if len(b) > self.max_bin:
                return CLOSE_TOO_BIG, "binary frame too large"
            if not self.bin.take() or not self.bytes.take(len(b)):
                return CLOSE_RATE, "message rate limit exceeded"
        elif t is not None:
            if len(t) > self.max_text:
                return CLOSE_TOO_BIG, "text frame too large"
            if not self.text.take() or not self.bytes.take(len(t)):
                return CLOSE_RATE, "message rate limit exceeded"
        return None
