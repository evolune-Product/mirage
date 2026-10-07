"""Optional per-persona webhooks behind the built-in book_meeting and send_notification tools."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import convo_runtime as cr, llm_backends as lb, webhooks
from ..auth import current_account
from ..db import Account, Persona, get_session, now
from ..models_templates import PersonaIntegration
from ..secretbox import decrypt, encrypt

router = APIRouter()


class IntegrationsIn(BaseModel):
    booking_webhook_url: str | None = None  # "" disables book_meeting
    booking_secret: str | None = None  # write-only; "" clears
    notify_webhook_url: str | None = None  # "" disables send_notification
    notify_secret: str | None = None


class TestIn(BaseModel):
    which: str = "booking"  # booking | notify


def _persona(s: Session, pid: str, acc: Account) -> Persona:
    p = s.get(Persona, pid)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    cr.ensure()
    return p


def _out(pid: str, i: PersonaIntegration | None) -> dict:
    i = i or PersonaIntegration(persona_id=pid, account_id="")
    return {"persona_id": pid,
            "booking": {"enabled": bool(i.booking_url), "webhook_url": i.booking_url, "has_secret": bool(i.booking_secret_enc), "tool": "book_meeting"},
            "notify": {"enabled": bool(i.notify_url), "webhook_url": i.notify_url, "has_secret": bool(i.notify_secret_enc), "tool": "send_notification"}}


@router.get("/personas/{pid}/integrations")
def get_integrations(pid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return _out(pid, s.get(PersonaIntegration, pid))


@router.put("/personas/{pid}/integrations")
def put_integrations(pid: str, body: IntegrationsIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    i = s.get(PersonaIntegration, pid) or PersonaIntegration(persona_id=pid, account_id=acc.id)
    for field, url in (("booking_url", body.booking_webhook_url), ("notify_url", body.notify_webhook_url)):
        if url is not None:
            if url:
                try:
                    webhooks.validate_url(url)
                except ValueError as e:
                    raise HTTPException(422, f"{field}: {e}")
            setattr(i, field, url)
    if body.booking_secret is not None:
        i.booking_secret_enc = encrypt(body.booking_secret) if body.booking_secret else ""
    if body.notify_secret is not None:
        i.notify_secret_enc = encrypt(body.notify_secret) if body.notify_secret else ""
    i.updated_at = now()
    s.add(i); s.commit(); s.refresh(i)
    return _out(pid, i)


@router.post("/personas/{pid}/integrations/test")
def test_integration(pid: str, body: TestIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Sends a clearly marked sample call (arguments.test = true) to the configured webhook so you can check your Zap / endpoint."""
    _persona(s, pid, acc)
    i = s.get(PersonaIntegration, pid)
    if body.which not in ("booking", "notify"):
        raise HTTPException(422, "which must be 'booking' or 'notify'")
    url = (i.booking_url if body.which == "booking" else i.notify_url) if i else ""
    if not url:
        raise HTTPException(409, f"{body.which} webhook is not configured")
    enc = i.booking_secret_enc if body.which == "booking" else i.notify_secret_enc
    args = ({"name": "Test Person", "email": "test@example.com", "preferred_time": "Tomorrow 3pm", "topic": "VocalFace test", "test": True}
            if body.which == "booking" else {"subject": "VocalFace test", "message": "This is a test notification.", "test": True})
    spec = lb.ToolSpec("book_meeting" if body.which == "booking" else "send_notification", "", {}, url, decrypt(enc) if enc else "", 10.0)
    ok, text = lb.execute_tool(spec, args, {"conversation_id": "test", "persona_id": pid})
    return {"ok": ok, "response": text[:500]}
