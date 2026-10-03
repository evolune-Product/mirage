"""Embeddable "Talk to us" widget: management API (API key) + public script and iframe page (share token is the credential).

  POST /v1/personas/{pid}/widget      create a share token + widget settings in one call, returns the copy-paste snippet
  GET  /v1/widgets, GET/PUT/DELETE /v1/widgets/{token}
  GET  /widget.js                     the one-line script (public, cached)
  GET  /widget/frame/{token}          the page shown inside the iframe (guest conversation UI), framing limited to allowed domains
"""
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import convo_runtime as cr, widgets
from ..auth import current_account
from ..db import Account, Persona, get_session, now
from ..models_features import ShareLink
from ..models_templates import WidgetConfig

router = APIRouter()
PREFIX = ""  # explicit paths below: /v1/... for the API, /widget... for the public pieces
STATIC = Path(__file__).resolve().parents[1] / "static"
ASSET = Path(__file__).resolve().parents[1] / "templates" / "widget" / "mirage-widget.js"


class WidgetIn(BaseModel):
    allowed_domains: list[str] = []  # [] = any website may embed it
    label: str = "Talk to us"
    color: str = "#6d5efc"
    position: str = "bottom-right"
    greeting: str = ""  # teaser bubble next to the button
    language: str = ""  # conversation language ("" = persona default)
    # share-link limits (same meaning as POST /v1/personas/{pid}/share)
    max_seconds: int = Field(default=300, ge=10, le=3600)
    max_total_seconds: int = Field(default=3600, ge=10, le=86400)
    max_sessions_per_hour: int = Field(default=20, ge=1, le=1000)
    max_sessions_per_ip_hour: int = Field(default=5, ge=1, le=100)
    expires_in_hours: float | None = Field(default=None, gt=0, le=24 * 365)


class WidgetPatch(BaseModel):
    allowed_domains: list[str] | None = None
    label: str | None = None
    color: str | None = None
    position: str | None = None
    greeting: str | None = None
    language: str | None = None


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _out(w: WidgetConfig, l: ShareLink | None, request: Request) -> dict:
    base = _base(request)
    return {"token": w.token, "persona_id": w.persona_id, "allowed_domains": widgets.domains_of(w), "label": w.label,
            "color": w.color, "position": w.position, "greeting": w.greeting, "language": w.language,
            "script_url": f"{base}/widget.js", "frame_url": f"{base}/widget/frame/{w.token}", "share_url": f"{base}/guest/{w.token}",
            "snippet": widgets.snippet(base, w),
            "limits": ({"max_seconds": l.max_seconds, "max_total_seconds": l.max_total_seconds,
                        "max_sessions_per_hour": l.max_sessions_per_hour, "max_sessions_per_ip_hour": l.max_sessions_per_ip_hour,
                        "used_seconds": l.used_seconds, "sessions_started": l.sessions_started, "expires_at": l.expires_at,
                        "revoked": l.revoked_at is not None} if l else None),
            "created_at": w.created_at}


def _apply(w: WidgetConfig, b) -> None:
    try:
        if b.allowed_domains is not None:
            w.allowed_domains = json.dumps(widgets.normalize_domains(b.allowed_domains))
        for k in ("label", "color", "position", "greeting", "language"):
            v = getattr(b, k)
            if v is not None:
                setattr(w, k, v)
        widgets.validate_style(w.label, w.color, w.position, w.greeting, w.language)
        if w.language:
            from .. import languages

            w.language = languages.normalize_language(w.language)
    except ValueError as e:
        raise HTTPException(422, str(e))
    w.updated_at = now()


def _owned(s: Session, token: str, acc: Account) -> tuple[WidgetConfig | None, ShareLink]:
    cr.ensure()
    l = s.get(ShareLink, token)
    if not l or l.account_id != acc.id:
        raise HTTPException(404, "widget not found")
    return s.get(WidgetConfig, token), l


@router.post("/v1/personas/{pid}/widget")
def create_widget(pid: str, body: WidgetIn, request: Request, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure()
    p = s.get(Persona, pid)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    exp = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours) if body.expires_in_hours else None
    l = ShareLink(token="sh_" + secrets.token_urlsafe(18), account_id=acc.id, persona_id=pid, label="widget",
                  max_seconds=body.max_seconds, max_total_seconds=body.max_total_seconds,
                  max_sessions_per_hour=body.max_sessions_per_hour, max_sessions_per_ip_hour=body.max_sessions_per_ip_hour,
                  expires_at=exp)
    w = WidgetConfig(token=l.token, account_id=acc.id, persona_id=pid)
    _apply(w, body)  # validate before anything is stored
    s.add(l); s.add(w); s.commit(); s.refresh(w); s.refresh(l)
    return _out(w, l, request)


@router.get("/v1/widgets")
def list_widgets(request: Request, persona_id: str | None = None, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure()
    q = select(WidgetConfig).where(WidgetConfig.account_id == acc.id)
    if persona_id:
        q = q.where(WidgetConfig.persona_id == persona_id)
    return [_out(w, s.get(ShareLink, w.token), request) for w in s.exec(q.order_by(WidgetConfig.created_at.desc())).all()]


@router.get("/v1/widgets/{token}")
def get_widget(token: str, request: Request, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    w, l = _owned(s, token, acc)
    if w is None:
        raise HTTPException(404, "this share link has no widget settings yet (PUT /v1/widgets/{token} creates them)")
    return _out(w, l, request)


@router.put("/v1/widgets/{token}")
def put_widget(token: str, body: WidgetPatch, request: Request, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Partial update; also attaches widget settings (e.g. allowed domains) to an existing guest share token."""
    w, l = _owned(s, token, acc)
    w = w or WidgetConfig(token=token, account_id=acc.id, persona_id=l.persona_id)
    _apply(w, body)
    s.add(w); s.commit(); s.refresh(w)
    return _out(w, l, request)


@router.delete("/v1/widgets/{token}")
def delete_widget(token: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Revokes the share token (the widget stops working at once) and removes the settings."""
    w, l = _owned(s, token, acc)
    l.revoked_at = l.revoked_at or datetime.now(timezone.utc)
    s.add(l)
    if w:
        s.delete(w)
    s.commit()
    return {"deleted": token}


# ---------------- public ----------------


@router.get("/widget.js", include_in_schema=False)
def widget_js():
    return Response(ASSET.read_text(), media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"})


_BOOT = """<script>window.__MW=%s;</script>
<style>:root{--mw:%s}#go{background:var(--mw)!important;box-shadow:none!important}h2{font-size:24px;background:none!important;color:#ece9f2!important;-webkit-text-fill-color:#ece9f2}body{padding:.9rem .9rem 1.2rem}</style>
<script>(function(){if(window.parent===window)return;
window.addEventListener('keydown',function(e){if(e.key==='Escape')window.parent.postMessage({mirage:'widget',type:'close'},'*')},true);
window.addEventListener('load',function(){window.parent.postMessage({mirage:'widget',type:'ready'},'*')})})();</script>
"""


@router.get("/widget/frame/{token}", include_in_schema=False)
def widget_frame(token: str, request: Request, lang: str = "", s: Session = Depends(get_session)):
    if not token.startswith("sh_") or len(token) > 80:
        raise HTTPException(404, "not found")
    cr.ensure()
    l = s.get(ShareLink, token)
    if not l or l.revoked_at is not None:
        raise HTTPException(404, "not found")
    w = s.get(WidgetConfig, token)
    patterns = widgets.domains_of(w)
    ref = request.headers.get("referer", "")
    if patterns and ref and not widgets.origin_matches(patterns, ref):  # best effort; frame-ancestors is the real gate
        raise HTTPException(403, "this agent may not be embedded on this website")
    language = ""
    for cand in (lang, w.language if w else ""):
        if cand:
            try:
                from .. import languages

                language = languages.normalize_language(cand)
                break
            except ValueError:
                pass
    cfg = {"lang": language}
    color = w.color if w else "#6d5efc"
    page = (STATIC / "guest.html").read_text().replace("</head>", _BOOT % (json.dumps(cfg), color) + "</head>", 1)
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                       "Content-Security-Policy": widgets.frame_ancestors(patterns)})
