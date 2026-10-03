"""Pure-ASGI hardening middleware: rate limits, request size limits, security headers, signed file URLs.

install(app) must be called BEFORE CORSMiddleware is added, so CORS stays outermost and 413/429
responses still carry CORS headers the browser can read.
"""
import hashlib
import json
import os
import re

from . import settings, signing
from .safety import limiter

# name -> (limit, window_seconds). Override with MIRAGE_RL_<NAME>="limit/window", e.g. MIRAGE_RL_VIDEO=5/60
DEFAULTS = {
    "IP": (600, 60),          # every request, per client IP (DoS guard)
    "KEY": (240, 60),         # every authenticated request, per API key
    "SIGNUP": (5, 600) if settings.is_production() else (60, 60),
    "VIDEO": (10, 60),        # POST /v1/videos, /v1/video-jobs
    "CHECKOUT": (10, 60),
    "CONVERSATION": (20, 60),
    "REPLICA": (20, 60),
    "MODERATION": (30, 60),
    "WEBHOOK": (120, 60),
    "TICKET": (60, 60),       # POST /v1/realtime/ticket
}

# (method, path regex, rule name, scope) ; scope "key" = per api key (falls back to ip), "ip" = per IP
ROUTES = [
    ("POST", re.compile(r"^/v1/signup$"), "SIGNUP", "ip"),
    ("POST", re.compile(r"^/v1/(videos|video-jobs)$"), "VIDEO", "key"),
    ("POST", re.compile(r"^/v1/billing/checkout$"), "CHECKOUT", "key"),
    ("POST", re.compile(r"^/v1/conversations$"), "CONVERSATION", "key"),
    ("POST", re.compile(r"^/v1/replicas$"), "REPLICA", "key"),
    ("POST", re.compile(r"^/v1/moderation/check$"), "MODERATION", "key"),
    ("POST", re.compile(r"^/v1/realtime/ticket$"), "TICKET", "key"),
    ("POST", re.compile(r"^/v1/billing/webhooks/"), "WEBHOOK", "ip"),
]
NO_CSP = ("/docs", "/redoc", "/openapi.json", "/v1/playground", "/static")


def rule(name: str) -> tuple[int, int]:
    raw = os.getenv(f"MIRAGE_RL_{name}", "")
    try:
        lim, win = raw.split("/")
        return int(lim), int(win)
    except ValueError:
        return DEFAULTS[name]


def _hdr(scope, name: bytes) -> str:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return ""


def client_ip(scope) -> str:
    if settings.trust_proxy():
        xff = _hdr(scope, b"x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    c = scope.get("client")
    return c[0] if c else "unknown"


async def _json_response(send, status: int, body: dict, extra: list | None = None):
    raw = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())] + (extra or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": raw})


def _sign_urls(obj):
    """Replace server-emitted /v1/files/... values (output_url, face_url) with signed URLs."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("output_url", "face_url") and isinstance(v, str) and signing.valid_file_path(v.split("?", 1)[0]):
                obj[k] = signing.sign_path(v)
            else:
                _sign_urls(v)
    elif isinstance(obj, list):
        for v in obj:
            _sign_urls(v)


class Hardening:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method, path = scope["method"], scope["path"]
        if method == "OPTIONS":  # CORS preflight: let CORS middleware answer
            return await self.app(scope, receive, send)

        if settings.ratelimit_enabled():
            blocked = self._rate_limit(scope, method, path)
            if blocked:
                retry = max(int(blocked) + 1, 1)
                return await _json_response(send, 429, {"detail": "rate limit exceeded", "retry_after": retry},
                                            [(b"retry-after", str(retry).encode())])

        ctype = _hdr(scope, b"content-type").lower()
        limit = settings.max_upload_bytes() if ctype.startswith("multipart/") else settings.max_body_bytes()
        cl = _hdr(scope, b"content-length")
        if cl.isdigit() and int(cl) > limit:
            return await _json_response(send, 413, {"detail": f"request body too large (max {limit} bytes)"})
        seen = 0
        too_big = False

        async def limited_receive():
            nonlocal seen, too_big
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    too_big = True
                    return {"type": "http.disconnect"}
            return msg

        sign_json = path.startswith("/v1/") and not path.startswith("/v1/files/")
        state = {"buf": [], "start": None, "rewrite": False, "started": False}
        sec = self._security_headers(path)

        async def wrapped_send(msg):
            if msg["type"] == "http.response.start":
                state["started"] = True
                hdrs = [(k, v) for k, v in msg["headers"] if k.lower() not in {h for h, _ in sec} or k.lower() == b"cache-control"]
                names = {k.lower() for k, _ in hdrs}
                hdrs += [(k, v) for k, v in sec if k not in names]
                msg = {**msg, "headers": hdrs}
                is_json = any(k.lower() == b"content-type" and v.startswith(b"application/json") for k, v in hdrs)
                if sign_json and is_json and msg["status"] == 200:
                    state["start"], state["rewrite"] = msg, True
                    return
            elif msg["type"] == "http.response.body" and state["rewrite"]:
                state["buf"].append(msg.get("body", b""))
                if msg.get("more_body"):
                    return
                body = b"".join(state["buf"])
                if b"/v1/files/" in body:
                    try:
                        data = json.loads(body)
                        _sign_urls(data)
                        body = json.dumps(data, separators=(",", ":")).encode()
                    except Exception:
                        pass
                start = state["start"]
                start["headers"] = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"] + \
                                   [(b"content-length", str(len(body)).encode())]
                await send(start)
                return await send({"type": "http.response.body", "body": body})
            await send(msg)

        try:
            await self.app(scope, limited_receive, wrapped_send)
        except Exception:
            if not too_big:
                raise
        if too_big and not state["started"]:  # streamed body exceeded the limit (no/lying content-length)
            await _json_response(send, 413, {"detail": f"request body too large (max {limit} bytes)"})

    # -- helpers --
    def _rate_limit(self, scope, method: str, path: str):
        ip = client_ip(scope)
        lim, win = rule("IP")
        ok, retry = limiter.check_ex(f"rl:ip:{ip}", lim, win)
        if not ok:
            return retry
        key = _hdr(scope, b"x-api-key")
        kid = hashlib.sha256(key.encode()).hexdigest()[:16] if key else ""
        if kid:
            lim, win = rule("KEY")
            ok, retry = limiter.check_ex(f"rl:key:{kid}", lim, win)
            if not ok:
                return retry
        for m, rx, name, sc in ROUTES:
            if m == method and rx.match(path):
                lim, win = rule(name)
                who = kid if (sc == "key" and kid) else f"ip:{ip}"
                ok, retry = limiter.check_ex(f"rl:{name}:{who}", lim, win)
                if not ok:
                    return retry
        return None

    @staticmethod
    def _security_headers(path: str):
        h = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
             (b"cross-origin-opener-policy", b"same-origin")]
        if not path.startswith(("/v1/playground", "/widget/frame/")):  # the embed widgets iframe these (widget_api sets frame-ancestors)
            h.append((b"x-frame-options", b"DENY"))
        if path.startswith("/v1/") and not path.startswith(NO_CSP):
            h.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
            if not path.startswith("/v1/files/"):
                h.append((b"cache-control", b"no-store"))
        if settings.is_production():
            h.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        return h


_SCRUB_HINTS = ("mk_", "sk_", "whsec_", "rzp_", "api_key", "apikey", "api-key", "token", "ticket", "sig=", "secret", "password", "Bearer", "bearer")
_factory_wrapped = False


def install_log_scrubbing() -> None:
    """Every log record (any module, uvicorn's access log included, text or JSON mode) is scrubbed of API keys,
    signatures, tokens and Bearer values at creation time. Exceptions are scrubbed too. Idempotent."""
    global _factory_wrapped
    if _factory_wrapped:
        return
    _factory_wrapped = True
    import logging

    orig = logging.getLogRecordFactory()

    def factory(*a, **kw):
        rec = orig(*a, **kw)
        try:
            text = rec.getMessage()  # format first: scrubbing the template alone would eat the %s placeholders
            if any(h in text for h in _SCRUB_HINTS):
                rec.msg, rec.args = settings.redact(text), None
        except Exception:  # noqa: BLE001  (logging must never break the app)
            pass
        return rec

    logging.setLogRecordFactory(factory)
    fe = logging.Formatter.formatException
    logging.Formatter.formatException = lambda self, ei: settings.redact(fe(self, ei))  # type: ignore[method-assign]


def install(app) -> None:
    install_log_scrubbing()
    errs = settings.validate_production()
    if errs:
        raise RuntimeError("unsafe production configuration: " + "; ".join(errs))
    app.add_middleware(Hardening)
