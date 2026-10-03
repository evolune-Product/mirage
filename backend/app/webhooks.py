"""Per-account webhooks: HMAC-signed deliveries from a DB-backed queue with retry/backoff and a delivery log.

emit(account_id, event, data)  -> inserts one WebhookDelivery per matching endpoint (cheap, sync, safe to call anywhere,
                                  including the worker process; never raises).
deliver_due(...)               -> sends due deliveries; run by the API process loop (`run_loop`) or any process.

Signature header (Stripe style):  Mirage-Signature: t=<unix>,v1=<hex hmac_sha256(secret, f"{t}.{body}")>
Other headers: Mirage-Event, Mirage-Delivery-Id, Mirage-Event-Id, User-Agent: Mirage-Webhooks/1.
A delivery succeeds on any 2xx. Retries after 10s, 1m, 5m, 30m, 2h (6 attempts total), then status=failed.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import httpx
from sqlalchemy import update
from sqlmodel import Session, SQLModel, select

from . import db
from .models_features import WebhookDelivery, WebhookEndpoint

EVENTS = [
    "conversation.started", "conversation.ended", "transcript.ready", "objective.completed",
    "tool.called", "guardrail.triggered", "replica.ready", "replica.error", "video.ready", "video.error",
    "video_batch.completed", "lead.captured", "lead.updated",
]
BACKOFF_S = [10, 60, 300, 1800, 7200]  # delay before attempt 2..6
MAX_ATTEMPTS = len(BACKOFF_S) + 1
_ensured: set[int] = set()


def ensure(engine=None) -> None:
    engine = engine or db.engine
    if id(engine) not in _ensured:
        SQLModel.metadata.create_all(engine)
        _ensured.add(id(engine))


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(24)


def sign(secret: str, body: str, ts: Optional[int] = None) -> str:
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify(secret: str, body: str, header: str, tolerance_s: int = 300) -> bool:
    """Receiver-side helper (also used by tests and exported in the SDKs)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
        expected = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, parts["v1"]) and abs(time.time() - ts) <= tolerance_s
    except Exception:
        return False


def validate_url(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must be http(s)")
    from . import netguard  # one guard for every server-side fetch (default ON in production)

    netguard.check_url(url)


def emit(account_id: str, event: str, data: dict, session: Optional[Session] = None) -> int:
    """Queue `event` for every active endpoint of the account that subscribes to it. Returns #deliveries queued."""
    try:
        ensure()
        own = session is None
        s = session or Session(db.engine)
        try:
            eps = s.exec(select(WebhookEndpoint).where(WebhookEndpoint.account_id == account_id,
                                                       WebhookEndpoint.active == True)).all()  # noqa: E712
            event_id = "evt_" + secrets.token_hex(8)
            n = 0
            for ep in eps:
                subs = {e.strip() for e in ep.events.split(",") if e.strip()}
                if "*" not in subs and event not in subs:
                    continue
                body = json.dumps({"id": event_id, "type": event, "created_at": datetime.now(timezone.utc).isoformat(),
                                   "data": data}, default=str, separators=(",", ":"))
                s.add(WebhookDelivery(account_id=account_id, endpoint_id=ep.id, event=event, event_id=event_id, payload=body))
                n += 1
            s.commit()
            return n
        finally:
            if own:
                s.close()
    except Exception:  # webhooks must never break the main flow
        return 0


Sender = Callable[[str, dict, str], "tuple[int, str]"]


def http_send(url: str, headers: dict, body: str) -> tuple[int, str]:
    from . import netguard  # SSRF guard: private/metadata targets refused, connection pinned to the validated IP

    r = netguard.post(url, content=body, headers=headers, timeout=10)
    return r.status_code, r.text[:300]


def deliver_one(s: Session, d: WebhookDelivery, send=http_send) -> None:
    ep = s.get(WebhookEndpoint, d.endpoint_id)
    d.attempts += 1
    if ep is None or not ep.active:
        d.status, d.last_error = "failed", "endpoint removed or disabled"
        s.add(d); s.commit()
        return
    headers = {"content-type": "application/json", "user-agent": "Mirage-Webhooks/1", "Mirage-Event": d.event,
               "Mirage-Delivery-Id": d.id, "Mirage-Event-Id": d.event_id, "Mirage-Signature": sign(ep.secret, d.payload)}
    try:
        code, text = send(ep.url, headers, d.payload)
        d.last_status_code = code
        ok = 200 <= code < 300
        d.last_error = None if ok else f"HTTP {code}: {text}"[:300]
    except Exception as e:  # noqa: BLE001
        ok, d.last_status_code, d.last_error = False, None, f"{type(e).__name__}: {e}"[:300]
    if ok:
        d.status, d.delivered_at = "delivered", datetime.now(timezone.utc)
    elif d.attempts >= MAX_ATTEMPTS:
        d.status = "failed"
    else:
        d.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=BACKOFF_S[d.attempts - 1])
    s.add(d); s.commit()


def deliver_due(send=http_send, limit: int = 50, now: Optional[datetime] = None) -> int:
    ensure()
    now = now or datetime.now(timezone.utc)
    n = 0
    with Session(db.engine) as s:
        due = s.exec(select(WebhookDelivery).where(WebhookDelivery.status == "pending",
                                                   WebhookDelivery.next_attempt_at <= now)
                     .order_by(WebhookDelivery.next_attempt_at).limit(limit)).all()
        for d in due:
            # lease: atomically push next_attempt_at out so a second worker/process cannot send the same delivery
            lease = now + timedelta(seconds=60)
            res = s.exec(update(WebhookDelivery).where(WebhookDelivery.id == d.id, WebhookDelivery.status == "pending",
                                                       WebhookDelivery.attempts == d.attempts).values(
                next_attempt_at=lease, attempts=d.attempts + 1))
            s.commit()
            if res.rowcount != 1:
                continue
            s.refresh(d)
            d.attempts -= 1  # deliver_one increments again
            deliver_one(s, d, send)
            n += 1
    return n


async def run_loop(interval: float = 2.0) -> None:
    """Background task for the API process."""
    while True:
        try:
            await asyncio.to_thread(deliver_due)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(interval)
