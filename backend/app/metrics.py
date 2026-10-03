"""Observability: Prometheus metrics (no extra dependency), structured JSON logs with request ids, deep health.

    install(app)                 one call in main.py: request-id + metrics + access-log middleware, /metrics, /health/deep
    GET /metrics                 Prometheus text format. Protected by MIRAGE_METRICS_TOKEN (Authorization: Bearer <t> or ?token=<t>).
                                 No token configured: open in dev, disabled (403) in production.
    GET /health/deep             db + ollama + lipsync + worker heartbeat (503 only when the DATABASE is down; others report "degraded")

Logging: MIRAGE_LOG_FORMAT=json|text (default json in production, text in dev). Every record carries `request_id`
(also returned as the X-Request-ID response header; an inbound X-Request-ID is honoured when it looks sane). Messages are
passed through settings.redact(); the access log records method, route template, status and latency only: never query
strings (the playground passes api_key there), bodies, headers, or client IPs.
"""

import contextvars
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from . import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_RID_OK = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")


# ======================= minimal Prometheus registry =======================
def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(names: tuple, values: tuple, extra: str = "") -> str:
    parts = [f'{n}="{_esc(v)}"' for n, v in zip(names, values)] + ([extra] if extra else [])
    return "{" + ",".join(parts) + "}" if parts else ""


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, help_: str, labelnames: tuple = ()):
        self.name, self.help, self.labelnames = name, help_, tuple(labelnames)
        self.lock = threading.Lock()
        self.vals: dict[tuple, object] = {}

    def _key(self, labels: dict) -> tuple:
        return tuple(str(labels.get(n, "")) for n in self.labelnames)

    def header(self) -> list[str]:
        return [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]


class Counter(_Metric):
    kind = "counter"

    def inc(self, n: float = 1, **labels):
        k = self._key(labels)
        with self.lock:
            self.vals[k] = self.vals.get(k, 0) + n

    def render(self) -> list[str]:
        with self.lock:
            return self.header() + [f"{self.name}{_labels(self.labelnames, k)} {v:g}" for k, v in sorted(self.vals.items())]


class Gauge(_Metric):
    kind = "gauge"

    def set(self, v: float, **labels):
        with self.lock:
            self.vals[self._key(labels)] = v

    def inc(self, n: float = 1, **labels):
        k = self._key(labels)
        with self.lock:
            self.vals[k] = self.vals.get(k, 0) + n

    def dec(self, n: float = 1, **labels):
        self.inc(-n, **labels)

    def render(self) -> list[str]:
        with self.lock:
            return self.header() + [f"{self.name}{_labels(self.labelnames, k)} {v:g}" for k, v in sorted(self.vals.items())]


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name, help_, labelnames=(), buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)):
        super().__init__(name, help_, labelnames)
        self.buckets = tuple(sorted(buckets))

    def observe(self, v: float, **labels):
        k = self._key(labels)
        with self.lock:
            st = self.vals.get(k)
            if st is None:
                st = self.vals[k] = {"b": [0] * len(self.buckets), "sum": 0.0, "count": 0}
            for i, ub in enumerate(self.buckets):
                if v <= ub:
                    st["b"][i] += 1
            st["sum"] += v
            st["count"] += 1

    def render(self) -> list[str]:
        out = self.header()
        with self.lock:
            for k, st in sorted(self.vals.items()):
                for ub, c in zip(self.buckets, st["b"]):
                    le = 'le="%g"' % ub
                    out.append(f"{self.name}_bucket{_labels(self.labelnames, k, le)} {c}")
                inf = 'le="+Inf"'
                out.append(f"{self.name}_bucket{_labels(self.labelnames, k, inf)} {st['count']}")
                out.append(f"{self.name}_sum{_labels(self.labelnames, k)} {st['sum']:g}")
                out.append(f"{self.name}_count{_labels(self.labelnames, k)} {st['count']}")
        return out


HTTP_REQUESTS = Counter("mirage_http_requests_total", "HTTP requests by method, route template and status", ("method", "route", "status"))
HTTP_LATENCY = Histogram("mirage_http_request_duration_seconds", "HTTP request latency", ("method", "route"))
WS_ACTIVE = Gauge("mirage_websocket_connections", "Open websocket connections (live conversation streams)")
FIRST_AUDIO = Histogram("mirage_first_audio_seconds", "End of user speech to first agent audio (in-process observations; see observe_first_audio)",
                        (), (0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10))
WEBHOOK_FAIL = Counter("mirage_webhook_delivery_failures_total", "Webhook delivery attempts that failed (in-process observations)")
_HEARTBEAT_FILE = ".worker_heartbeat"


def observe_first_audio(ms: float) -> None:
    """Optional in-process hook (conversation runtime may call it); the DB-derived summary below works without it."""
    FIRST_AUDIO.observe(ms / 1000.0)


def worker_heartbeat() -> None:
    """Called by the worker loop (jobs.run_once) each poll: touches <data>/.worker_heartbeat. Never raises."""
    try:
        p = settings.data_dir() / _HEARTBEAT_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(time.time()))
    except Exception:  # noqa: BLE001
        pass


def worker_heartbeat_age() -> float | None:
    try:
        return max(time.time() - float((settings.data_dir() / _HEARTBEAT_FILE).read_text().strip()), 0.0)
    except Exception:  # noqa: BLE001
        return None


def _utc(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def db_snapshot() -> dict:
    """Scrape-time aggregates straight from the database (correct across multiple API processes)."""
    from sqlalchemy import func
    from sqlmodel import Session, select

    from . import db
    from .db import Conversation, Replica, Video

    out = {"conversations_active": 0, "queue_replicas": 0, "queue_videos": 0, "accounts": 0, "webhook": {}, "first_audio_ms": []}
    with Session(db.engine) as s:
        out["conversations_active"] = s.exec(select(func.count()).select_from(Conversation).where(Conversation.status == "active")).one()
        out["queue_replicas"] = s.exec(select(func.count()).select_from(Replica).where(Replica.status == "training")).one()
        out["queue_videos"] = s.exec(select(func.count()).select_from(Video).where(Video.status == "queued")).one()
        out["videos_rendering"] = s.exec(select(func.count()).select_from(Video).where(Video.status == "rendering")).one()
        out["accounts"] = s.exec(select(func.count()).select_from(db.Account)).one()
        try:
            from .models_features import TranscriptTurn, WebhookDelivery

            for st, n in s.exec(select(WebhookDelivery.status, func.count()).group_by(WebhookDelivery.status)).all():
                out["webhook"][st] = n
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            out["first_audio_ms"] = sorted(s.exec(select(TranscriptTurn.first_audio_ms).where(
                TranscriptTurn.first_audio_ms.is_not(None), TranscriptTurn.created_at > since)).all()) \
                if hasattr(TranscriptTurn, "created_at") else []
        except Exception:  # noqa: BLE001 - optional tables
            pass
    return out


def _q(sorted_vals: list, q: float) -> float:
    return float(sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * q))])


def render_metrics() -> str:
    lines: list[str] = []
    for m in (HTTP_REQUESTS, HTTP_LATENCY, WS_ACTIVE, FIRST_AUDIO, WEBHOOK_FAIL):
        lines += m.render()
    try:
        snap = db_snapshot()
    except Exception as e:  # noqa: BLE001
        snap = None
        lines.append(f"# db snapshot failed: {type(e).__name__}")
    if snap:
        def g(name, help_, val, labels=""):
            lines.extend([f"# HELP {name} {help_}", f"# TYPE {name} gauge", f"{name}{labels} {val}"])
        g("mirage_conversations_active", "Conversations with status=active", snap["conversations_active"])
        lines += ["# HELP mirage_worker_queue_depth Jobs waiting for a worker", "# TYPE mirage_worker_queue_depth gauge",
                  f'mirage_worker_queue_depth{{kind="replica"}} {snap["queue_replicas"]}',
                  f'mirage_worker_queue_depth{{kind="video"}} {snap["queue_videos"]}']
        g("mirage_videos_rendering", "Videos currently rendering", snap["videos_rendering"])
        g("mirage_accounts", "Total accounts", snap["accounts"])
        lines += ["# HELP mirage_webhook_deliveries Webhook deliveries by status (failed = retries exhausted)", "# TYPE mirage_webhook_deliveries gauge"]
        lines += [f'mirage_webhook_deliveries{{status="{_esc(k)}"}} {v}' for k, v in sorted(snap["webhook"].items())]
        fa = snap["first_audio_ms"]
        lines += ["# HELP mirage_first_audio_window_ms First-audio latency over the last hour, from stored transcript turns", "# TYPE mirage_first_audio_window_ms summary"]
        if fa:
            lines += [f'mirage_first_audio_window_ms{{quantile="0.5"}} {_q(fa, 0.5):g}', f'mirage_first_audio_window_ms{{quantile="0.95"}} {_q(fa, 0.95):g}']
        lines += [f"mirage_first_audio_window_ms_count {len(fa)}", f"mirage_first_audio_window_ms_sum {sum(fa):g}"]
    age = worker_heartbeat_age()
    lines += ["# HELP mirage_worker_heartbeat_age_seconds Seconds since a worker last polled (-1 = never seen)", "# TYPE mirage_worker_heartbeat_age_seconds gauge",
              f"mirage_worker_heartbeat_age_seconds {age if age is not None else -1:g}"]
    return "\n".join(lines) + "\n"


# ======================= deep health =======================
def _probe(url: str, timeout: float = 1.5) -> dict:
    import httpx

    t0 = time.time()
    try:
        r = httpx.get(url, timeout=timeout)
        return {"status": "ok" if r.status_code < 500 else "down", "http": r.status_code, "ms": round((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"status": "down", "error": type(e).__name__}


def deep_health() -> tuple[dict, bool]:
    """-> (report, db_ok). Only the database is critical; everything else degrades."""
    checks: dict = {}
    try:
        from sqlalchemy import text

        from . import db

        t0 = time.time()
        with db.engine.connect() as c:
            c.execute(text("select 1"))
        checks["db"] = {"status": "ok", "ms": round((time.time() - t0) * 1000), "dialect": db.engine.dialect.name}
    except Exception as e:  # noqa: BLE001
        checks["db"] = {"status": "down", "error": type(e).__name__}
    try:
        from . import migrate

        st = migrate.status()
        checks["migrations"] = {"status": "ok" if st["state"] in ("up-to-date", "unstamped") else "behind", **st}
    except Exception as e:  # noqa: BLE001
        checks["migrations"] = {"status": "unknown", "error": type(e).__name__}
    ollama = os.getenv("OLLAMA_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
    checks["ollama"] = _probe(ollama + "/api/tags")
    checks["lipsync"] = _probe(os.getenv("MIRAGE_LIPSYNC_URL", "http://localhost:8100").rstrip("/") + "/health")
    age = worker_heartbeat_age()
    checks["worker"] = {"status": "ok" if age is not None and age < 30 else ("stale" if age is not None else "never_seen"),
                        "heartbeat_age_s": None if age is None else round(age, 1)}
    from . import storage

    try:
        st = storage.get_storage()
        checks["storage"] = {"status": "ok", "backend": st.name}
    except Exception as e:  # noqa: BLE001
        checks["storage"] = {"status": "down", "error": str(e)[:120]}
    db_ok = checks["db"]["status"] == "ok"
    degraded = [k for k, v in checks.items() if v["status"] not in ("ok",) and k != "migrations"]
    return {"ok": db_ok, "status": "ok" if db_ok and not degraded else ("down" if not db_ok else "degraded"),
            "degraded": degraded, "checks": checks}, db_ok


# ======================= logging =======================
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
             "level": record.levelname.lower(), "logger": record.name, "msg": settings.redact(record.getMessage()),
             "request_id": getattr(record, "request_id", None) or request_id_var.get()}
        for k in ("method", "route", "status", "ms", "key_id", "event"):
            if hasattr(record, k):
                d[k] = getattr(record, k)
        if record.exc_info:
            d["exc"] = settings.redact(self.formatException(record.exc_info))[-1500:]
        return json.dumps(d, separators=(",", ":"), default=str)


class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get()
        return True


def log_format() -> str:
    f = os.getenv("MIRAGE_LOG_FORMAT", "").strip().lower()
    return f if f in ("json", "text") else ("json" if settings.is_production() else "text")


def configure_logging() -> None:
    """Idempotent. JSON mode replaces the root + uvicorn handlers' formatters; text mode only adds request ids to records."""
    fmt = log_format()
    root = logging.getLogger()
    if fmt == "json":
        for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "mirage"):
            lg = logging.getLogger(name)
            if name == "uvicorn.access":
                lg.handlers, lg.propagate = [], False  # our access log replaces it (uvicorn's contains query strings)
                continue
            if name and not lg.handlers:
                continue
            for h in lg.handlers:
                h.setFormatter(JsonFormatter())
        if not root.handlers:
            h = logging.StreamHandler()
            h.setFormatter(JsonFormatter())
            root.addHandler(h)
        if root.level in (logging.NOTSET, logging.WARNING):
            root.setLevel(logging.INFO)
    for h in root.handlers:
        if not any(isinstance(f, _RequestIdFilter) for f in h.filters):
            h.addFilter(_RequestIdFilter())


access_log = logging.getLogger("mirage.access")


# ======================= middleware =======================
def _hdr(scope, name: bytes) -> str:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return ""


def route_template(scope) -> str:
    """'/v1/personas/{pid}' for a matched route. Starlette reports the route path WITHOUT the include_router prefix,
    so re-attach the prefix by finding where the template's regex matches inside the concrete path."""
    route = scope.get("route")
    path = scope.get("path", "")
    tpl = getattr(route, "path", None)
    if tpl is None:
        return "/static" if path.startswith("/static/") else ("unmatched" if scope["type"] == "http" else path)
    rx = getattr(route, "path_regex", None)
    if rx is not None:
        idx = [0] + [i for i, ch in enumerate(path) if ch == "/" and i > 0]
        for i in idx:
            if rx.fullmatch(path[i:]):
                return path[:i] + tpl
    return tpl


class Observability:
    """Pure ASGI: request id, metrics, one structured access-log line per request. Install OUTSIDE CORS so 413/429 are counted."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        inbound = _hdr(scope, b"x-request-id")
        rid = inbound if _RID_OK.match(inbound) else uuid.uuid4().hex[:16]
        tok = request_id_var.set(rid)
        t0 = time.perf_counter()
        status = {"v": 500}
        is_ws = scope["type"] == "websocket"
        if is_ws:
            WS_ACTIVE.inc()

        async def wrapped_send(msg):
            if msg["type"] == "http.response.start":
                status["v"] = msg["status"]
                msg = {**msg, "headers": [*msg["headers"], (b"x-request-id", rid.encode())]}
            elif msg["type"] == "websocket.accept":
                msg = {**msg, "headers": [*msg.get("headers", []), (b"x-request-id", rid.encode())]}
                status["v"] = 101
            elif msg["type"] == "websocket.close" and status["v"] == 500:
                status["v"] = 403
            await send(msg)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            dt = time.perf_counter() - t0
            route = route_template(scope)
            method = scope.get("method", "WS")
            if is_ws:
                WS_ACTIVE.dec()
            if route != "/metrics":
                HTTP_REQUESTS.inc(method=method, route=route, status=status["v"])
                HTTP_LATENCY.observe(dt, method=method, route=route)
                if True:
                    key = _hdr(scope, b"x-api-key")
                    extra = {"method": method, "route": route, "status": status["v"], "ms": round(dt * 1000, 1), "event": "request"}
                    if key:
                        import hashlib

                        extra["key_id"] = hashlib.sha256(key.encode()).hexdigest()[:8]  # correlates a caller without logging the key
                    access_log.log(logging.WARNING if status["v"] >= 500 else logging.INFO, f"{method} {route} {status['v']}", extra=extra)
            request_id_var.reset(tok)


def _token_ok(request) -> bool:
    tok = os.getenv("MIRAGE_METRICS_TOKEN", "")
    if not tok:
        return not settings.is_production()
    import hmac

    auth = request.headers.get("authorization", "")
    given = auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
    return hmac.compare_digest(given, tok)


def install(app) -> None:
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse, PlainTextResponse

    configure_logging()
    r = APIRouter()

    @r.get("/metrics", include_in_schema=False)
    def metrics_endpoint(request: Request):
        if not os.getenv("MIRAGE_METRICS_TOKEN") and settings.is_production():
            return PlainTextResponse("metrics disabled: set MIRAGE_METRICS_TOKEN\n", status_code=403)
        if not _token_ok(request):
            return PlainTextResponse("unauthorized\n", status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8", headers={"Cache-Control": "no-store"})

    @r.get("/health/deep", include_in_schema=False)
    def health_deep():
        rep, ok = deep_health()
        return JSONResponse(rep, status_code=200 if ok else 503, headers={"Cache-Control": "no-store"})

    app.include_router(r)
    app.add_middleware(Observability)  # added last = outermost
