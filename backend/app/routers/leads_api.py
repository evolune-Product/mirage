"""Leads captured by agents (built-in capture_lead tool): list/search/export/delete + per-persona lead capture settings."""
import csv
import io
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_
from sqlmodel import Session, select

from .. import convo_runtime as cr, leads
from ..auth import current_account
from ..db import Account, Persona, get_session, now
from ..models_leads import Lead, LeadCaptureConfig

router = APIRouter()


class LeadCaptureIn(BaseModel):
    enabled: bool | None = None
    required_fields: list[str] | None = None  # subset of name,email,phone,company,interest,notes (email or phone is always needed too)
    require_consent: bool | None = None
    disclosure: str | None = Field(default=None, max_length=500)


class LeadIn(BaseModel):
    """Manual / server-side lead (e.g. from your own form); same validation as the tool, no consent gate unless you pass consent=false."""
    persona_id: str
    conversation_id: str | None = None
    name: str = ""
    email: str = ""
    phone: str = ""
    company: str = ""
    interest: str = ""
    notes: str = ""
    consent: bool = True


def _persona(s: Session, pid: str, acc: Account) -> Persona:
    p = s.get(Persona, pid)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    cr.ensure(); leads.ensure()
    return p


def _cfg_out(pid: str, c: LeadCaptureConfig | None) -> dict:
    c = c or LeadCaptureConfig(persona_id=pid, account_id="", enabled=False)
    return {"persona_id": pid, "enabled": c.enabled, "required_fields": leads.required_fields(c), "require_consent": c.require_consent,
            "disclosure": c.disclosure, "tool": "capture_lead" if c.enabled else None}


@router.get("/personas/{pid}/lead-capture")
def get_lead_capture(pid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return _cfg_out(pid, s.get(LeadCaptureConfig, pid))


@router.put("/personas/{pid}/lead-capture")
def put_lead_capture(pid: str, body: LeadCaptureIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Enable/configure the built-in capture_lead tool for a persona (partial update; first call creates the settings, enabled=true)."""
    _persona(s, pid, acc)
    c = s.get(LeadCaptureConfig, pid) or LeadCaptureConfig(persona_id=pid, account_id=acc.id, enabled=True)
    if body.required_fields is not None:
        bad = [f for f in body.required_fields if f not in leads.FIELDS]
        if bad:
            raise HTTPException(422, f"unknown field(s) {bad}; valid: {list(leads.FIELDS)}")
        c.required_fields = json.dumps(body.required_fields)
    for k in ("enabled", "require_consent", "disclosure"):
        v = getattr(body, k)
        if v is not None:
            setattr(c, k, v)
    c.updated_at = now()
    s.add(c); s.commit(); s.refresh(c)
    return _cfg_out(pid, c)


def _dt(v: str | None, end: bool = False) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"bad date '{v}' (use YYYY-MM-DD or ISO 8601)")
    if len(v) <= 10 and end:
        d = d.replace(hour=23, minute=59, second=59)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _query(acc: Account, persona_id, conversation_id, since, until, q, consent):
    stmt = select(Lead).where(Lead.account_id == acc.id)
    if persona_id:
        stmt = stmt.where(Lead.persona_id == persona_id)
    if conversation_id:
        stmt = stmt.where(Lead.conversation_id == conversation_id)
    if (d := _dt(since)):
        stmt = stmt.where(Lead.created_at >= d)
    if (d := _dt(until, True)):
        stmt = stmt.where(Lead.created_at <= d)
    if consent is not None:
        stmt = stmt.where(Lead.consent == consent)
    if q:
        like = f"%{q.lower().replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(*[func.lower(getattr(Lead, f)).like(like) for f in leads.FIELDS]))
    return stmt


@router.get("/leads")
def list_leads(persona_id: str | None = None, conversation_id: str | None = None, since: str | None = None, until: str | None = None,
               q: str | None = None, consent: bool | None = None, limit: int = 50, offset: int = 0,
               acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure(); leads.ensure()
    limit, offset = min(max(limit, 1), 200), max(offset, 0)
    stmt = _query(acc, persona_id, conversation_id, since, until, q, consent)
    total = s.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = s.exec(stmt.order_by(Lead.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [leads.lead_out(l) for l in rows], "total": total, "limit": limit, "offset": offset}


def _csv_safe(v: str) -> str:
    """Spreadsheet formula injection guard: a cell starting with = + - @ is prefixed with an apostrophe (real phone numbers are kept)."""
    if v and (v[0] in "=@\t\r" or (v[0] in "+-" and not re.fullmatch(r"\+?\d+", v))):
        return "'" + v
    return v


@router.get("/leads/export.csv")
def export_csv(persona_id: str | None = None, since: str | None = None, until: str | None = None, q: str | None = None,
               consent: bool | None = None, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure(); leads.ensure()
    rows = s.exec(_query(acc, persona_id, None, since, until, q, consent).order_by(Lead.created_at.desc()).limit(50000)).all()
    names = {p.id: p.name for p in s.exec(select(Persona).where(Persona.account_id == acc.id)).all()}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "created_at", "persona_id", "persona_name", "conversation_id", *leads.FIELDS, "consent", "source"])
    for l in rows:
        w.writerow([l.id, l.created_at.isoformat() if l.created_at else "", l.persona_id, _csv_safe(names.get(l.persona_id, "")),
                    l.conversation_id, *[_csv_safe(getattr(l, f)) for f in leads.FIELDS], "yes" if l.consent else "no", l.source])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="vocalface-leads.csv"'})


@router.get("/leads/{lid}")
def get_lead(lid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure(); leads.ensure()
    l = s.get(Lead, lid)
    if not l or l.account_id != acc.id:
        raise HTTPException(404, "lead not found")
    return leads.lead_out(l)


@router.delete("/leads/{lid}")
def delete_lead(lid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Permanently deletes the lead (GDPR/DPDP erasure). The conversation transcript is separate (see data deletion)."""
    cr.ensure(); leads.ensure()
    l = s.get(Lead, lid)
    if not l or l.account_id != acc.id:
        raise HTTPException(404, "lead not found")
    s.delete(l); s.commit()
    return {"deleted": lid}


@router.post("/leads")
def create_lead(body: LeadIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Create or update a lead from your own systems (validated like the tool; emits lead.captured)."""
    _persona(s, body.persona_id, acc)
    from ..db import Conversation

    cid = body.conversation_id
    if cid:
        c = s.get(Conversation, cid)
        if not c or c.account_id != acc.id or c.persona_id != body.persona_id:
            raise HTTPException(404, "conversation not found")
    else:  # a synthetic key so the one-lead-per-conversation rule does not merge unrelated manual leads
        cid = "manual_" + now().strftime("%Y%m%d%H%M%S%f")
    try:
        l, _ = leads.save_lead(s, account_id=acc.id, conversation_id=cid, persona_id=body.persona_id,
                               args=body.model_dump(exclude={"persona_id", "conversation_id"}), cfg=s.get(LeadCaptureConfig, body.persona_id),
                               source="api", enforce_consent=False)
    except leads.LeadError as e:
        raise HTTPException(422, str(e))
    return leads.lead_out(l)
