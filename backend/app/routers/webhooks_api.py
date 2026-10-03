import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import db, webhooks
from ..auth import current_account
from ..db import Account, get_session
from ..models_features import WebhookDelivery, WebhookEndpoint

router = APIRouter()


class EndpointIn(BaseModel):
    url: str
    events: list[str] = ["*"]
    description: str = ""
    active: bool = True


class EndpointPatch(BaseModel):
    url: str | None = None
    events: list[str] | None = None
    description: str | None = None
    active: bool | None = None


def _check(url: str | None, events: list[str] | None):
    if url is not None:
        try:
            webhooks.validate_url(url)
        except ValueError as e:
            raise HTTPException(422, str(e))
    for e in events or []:
        if e != "*" and e not in webhooks.EVENTS:
            raise HTTPException(422, f"unknown event '{e}'; valid: {webhooks.EVENTS}")


def _out(ep: WebhookEndpoint, secret: bool = False) -> dict:
    d = {"id": ep.id, "url": ep.url, "events": ep.events.split(","), "active": ep.active,
         "description": ep.description, "created_at": ep.created_at}
    if secret:
        d["secret"] = ep.secret  # shown only at creation / rotation
    return d


def _own(s: Session, wid: str, acc: Account) -> WebhookEndpoint:
    ep = s.get(WebhookEndpoint, wid)
    if not ep or ep.account_id != acc.id:
        raise HTTPException(404, "webhook not found")
    return ep


@router.get("/webhooks/events")
def list_events():
    return {"events": webhooks.EVENTS}


@router.post("/webhooks")
def create_endpoint(body: EndpointIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    webhooks.ensure(db.engine)
    _check(body.url, body.events)
    ep = WebhookEndpoint(account_id=acc.id, url=body.url, secret=webhooks.new_secret(),
                         events=",".join(body.events) or "*", description=body.description, active=body.active)
    s.add(ep); s.commit(); s.refresh(ep)
    return _out(ep, secret=True)


@router.get("/webhooks")
def list_endpoints(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    webhooks.ensure(db.engine)
    return [_out(e) for e in s.exec(select(WebhookEndpoint).where(WebhookEndpoint.account_id == acc.id)).all()]


@router.patch("/webhooks/{wid}")
def update_endpoint(wid: str, body: EndpointPatch, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    ep = _own(s, wid, acc)
    _check(body.url, body.events)
    if body.url is not None: ep.url = body.url
    if body.events is not None: ep.events = ",".join(body.events) or "*"
    if body.description is not None: ep.description = body.description
    if body.active is not None: ep.active = body.active
    s.add(ep); s.commit(); s.refresh(ep)
    return _out(ep)


@router.post("/webhooks/{wid}/rotate-secret")
def rotate_secret(wid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    ep = _own(s, wid, acc)
    ep.secret = webhooks.new_secret()
    s.add(ep); s.commit(); s.refresh(ep)
    return _out(ep, secret=True)


@router.delete("/webhooks/{wid}")
def delete_endpoint(wid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    ep = _own(s, wid, acc)
    s.delete(ep); s.commit()
    return {"deleted": wid}


@router.post("/webhooks/{wid}/test")
def send_test(wid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Queues a `webhook.test` delivery to just this endpoint and attempts it immediately."""
    import secrets as _sec
    from datetime import datetime, timezone
    ep = _own(s, wid, acc)
    body = json.dumps({"id": "evt_" + _sec.token_hex(8), "type": "webhook.test",
                       "created_at": datetime.now(timezone.utc).isoformat(), "data": {"ok": True}}, separators=(",", ":"))
    d = WebhookDelivery(account_id=acc.id, endpoint_id=ep.id, event="webhook.test", payload=body)
    s.add(d); s.commit(); s.refresh(d)
    webhooks.deliver_one(s, d)
    s.refresh(d)
    return _delivery(d)


def _delivery(d: WebhookDelivery) -> dict:
    return {"id": d.id, "endpoint_id": d.endpoint_id, "event": d.event, "status": d.status, "attempts": d.attempts,
            "last_status_code": d.last_status_code, "last_error": d.last_error, "next_attempt_at": d.next_attempt_at,
            "created_at": d.created_at, "delivered_at": d.delivered_at}


@router.get("/webhooks/{wid}/deliveries")
def deliveries(wid: str, limit: int = 50, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _own(s, wid, acc)
    rows = s.exec(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == wid)
                  .order_by(WebhookDelivery.created_at.desc()).limit(min(limit, 200))).all()
    return [_delivery(d) for d in rows]


@router.post("/webhooks/deliveries/{did}/retry")
def retry(did: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    from datetime import datetime, timezone
    d = s.get(WebhookDelivery, did)
    if not d or d.account_id != acc.id:
        raise HTTPException(404, "delivery not found")
    d.status, d.attempts, d.next_attempt_at = "pending", 0, datetime.now(timezone.utc)
    s.add(d); s.commit()
    webhooks.deliver_one(s, d)
    s.refresh(d)
    return _delivery(d)
