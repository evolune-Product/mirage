"""Shareable guest links: owner creates a token for a persona; anyone with the link can talk to it, bounded by
per-session seconds, a total cost cap (seconds), per-link and per-IP session rate limits, expiry and revocation.

Guests never see an API key. The guest WebSocket authenticates the token, then runs the normal conversation
stream (same code path as /v1/conversations/{cid}/stream) for a conversation that belongs to this token only.
The public page is served at /guest/{token} (see guest_page.py)."""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import convo_runtime as cr, hardening
from ..auth import current_account
from ..db import Account, Conversation, Persona, get_session
from ..models_features import ConversationMeta, ShareLink, ShareSession

router = APIRouter()


class ShareIn(BaseModel):
    label: str = ""
    max_seconds: int = Field(default=300, ge=10, le=3600)
    max_total_seconds: int = Field(default=3600, ge=10, le=86400)
    max_sessions_per_hour: int = Field(default=20, ge=1, le=1000)
    max_sessions_per_ip_hour: int = Field(default=5, ge=1, le=100)
    expires_in_hours: float | None = Field(default=None, gt=0, le=24 * 365)


def _out(l: ShareLink, request: Request | None = None) -> dict:
    base = str(request.base_url).rstrip("/") if request else ""
    return {"token": l.token, "url": f"{base}/guest/{l.token}", "persona_id": l.persona_id, "label": l.label,
            "max_seconds": l.max_seconds, "max_total_seconds": l.max_total_seconds,
            "max_sessions_per_hour": l.max_sessions_per_hour, "max_sessions_per_ip_hour": l.max_sessions_per_ip_hour,
            "used_seconds": l.used_seconds, "sessions_started": l.sessions_started, "expires_at": l.expires_at,
            "revoked": l.revoked_at is not None, "created_at": l.created_at}


@router.post("/personas/{pid}/share")
def create_share(pid: str, body: ShareIn, request: Request, acc: Account = Depends(current_account),
                 s: Session = Depends(get_session)):
    cr.ensure()
    p = s.get(Persona, pid)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    exp = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours) if body.expires_in_hours else None
    l = ShareLink(token="sh_" + secrets.token_urlsafe(18), account_id=acc.id, persona_id=pid, label=body.label[:80],
                  max_seconds=body.max_seconds, max_total_seconds=body.max_total_seconds,
                  max_sessions_per_hour=body.max_sessions_per_hour, max_sessions_per_ip_hour=body.max_sessions_per_ip_hour,
                  expires_at=exp)
    s.add(l); s.commit(); s.refresh(l)
    return _out(l, request)


@router.get("/personas/{pid}/share")
def list_shares(pid: str, request: Request, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure()
    p = s.get(Persona, pid)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    return [_out(l, request) for l in s.exec(select(ShareLink).where(ShareLink.persona_id == pid)).all()]


@router.delete("/share/{token}")
def revoke_share(token: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure()
    l = s.get(ShareLink, token)
    if not l or l.account_id != acc.id:
        raise HTTPException(404, "share link not found")
    l.revoked_at = l.revoked_at or datetime.now(timezone.utc)
    s.add(l); s.commit()
    return _out(l)


# ---------------- public (token is the credential) ----------------


def _link(s: Session, token: str) -> ShareLink:
    cr.ensure()
    l = s.get(ShareLink, token)
    if not l or l.revoked_at is not None:
        raise HTTPException(404, "link not found")
    if l.expires_at and cr.utc(l.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(410, "link expired")
    return l


def _reserved(s: Session, token: str) -> int:
    """Seconds promised to sessions that are still running (not yet counted into used_seconds)."""
    total = 0
    for ss in s.exec(select(ShareSession).where(ShareSession.token == token, ShareSession.counted == False)).all():  # noqa: E712
        c = s.get(Conversation, ss.conversation_id)
        if c and c.status == "active" and datetime.now(timezone.utc) - cr.utc(ss.started_at) < timedelta(seconds=ss.allowed_seconds + 60):
            total += ss.allowed_seconds
    return total


@router.get("/guest/{token}/info")
def info(token: str, s: Session = Depends(get_session)):
    l = _link(s, token)
    p = s.get(Persona, l.persona_id)
    remaining = max(l.max_total_seconds - l.used_seconds - _reserved(s, token), 0)
    return {"persona_name": p.name if p else "", "label": l.label, "max_seconds": min(l.max_seconds, remaining),
            "available": remaining >= 10}


@router.post("/guest/{token}/conversations")
def start(token: str, request: Request, s: Session = Depends(get_session)):
    l = _link(s, token)
    acc = s.get(Account, l.account_id)
    p = s.get(Persona, l.persona_id)
    if not acc or not p:
        raise HTTPException(404, "link not found")
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    recent = [x for x in s.exec(select(ShareSession).where(ShareSession.token == token)).all() if cr.utc(x.started_at) > hour_ago]
    ip = hardening.client_ip(request.scope)
    if len(recent) >= l.max_sessions_per_hour:
        raise HTTPException(429, "this link has reached its hourly session limit", headers={"Retry-After": "600"})
    if len([x for x in recent if x.ip == ip]) >= l.max_sessions_per_ip_hour:
        raise HTTPException(429, "too many sessions from your network; try again later", headers={"Retry-After": "600"})
    from ..billing import session_allowance

    host_allowance = session_allowance(s, acc)  # credits + capped overage headroom
    if host_allowance <= 0:
        raise HTTPException(402, "the host is out of credits")
    remaining = l.max_total_seconds - l.used_seconds - _reserved(s, token)
    allowed = min(l.max_seconds, remaining, host_allowance)
    if allowed < 10:
        raise HTTPException(429, "this link has reached its usage limit")
    c = Conversation(account_id=acc.id, persona_id=p.id)
    c.room_url = f"/rooms/{c.id}"
    s.add(c); s.flush()
    s.add(ConversationMeta(conversation_id=c.id, account_id=acc.id, persona_id=p.id, share_token=token, max_seconds=allowed))
    s.add(ShareSession(conversation_id=c.id, token=token, ip=ip, allowed_seconds=allowed))
    l.sessions_started += 1
    s.add(l); s.commit()
    from .. import webhooks
    webhooks.emit(acc.id, "conversation.started", {"conversation_id": c.id, "persona_id": p.id, "share_token": token}, session=s)
    return {"conversation_id": c.id, "max_seconds": allowed, "ws_path": f"/v1/guest/{token}/stream?cid={c.id}"}


@router.websocket("/guest/{token}/stream")
async def guest_stream(ws: WebSocket, token: str, cid: str = "", s: Session = Depends(get_session)):
    from .realtime import stream

    try:
        l = _link(s, token)
    except HTTPException:
        await ws.close(code=4404, reason="link not found or expired")
        return
    ss = s.get(ShareSession, cid) if cid else None
    acc = s.get(Account, l.account_id)
    if not ss or ss.token != token or not acc:
        await ws.close(code=4404, reason="session not found")
        return
    result = None
    try:
        result = await stream(ws, cid, acc.api_key, s)  # the owner's key is only used server-side; the guest never sees it
    finally:
        if result == "dropped":  # connection lost without a goodbye: keep the session open so the page can reconnect and
            return  # resume; the reaper ends it (and counts the usage) once the grace period passes without a reconnect
        try:
            s.expire_all()
            c = s.get(Conversation, cid)
            if c is not None and c.status == "active":
                cr.end_conversation_row(s, s.get(Account, c.account_id), c)
            await cr.finalize_conversation(cid, "guest_ended")
        except Exception:  # noqa: BLE001 - the reaper is the backstop
            pass
