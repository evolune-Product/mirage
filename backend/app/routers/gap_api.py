"""Competitor-gap features: URL knowledge sources, citations, pronunciation glossary, interruption tuning,
conversation insights/sentiment analytics, scheduled share links (+ .ics)."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import convo_runtime as cr, gap_knowledge, insights, knowledge as kb, pronunciation
from ..auth import current_account
from ..db import Account, Conversation, Persona, get_session
from ..models_extra import KnowledgeDoc
from ..models_features import ConversationMeta, ShareLink
from ..models_gap import (ConversationInsight, KnowledgeSource, PronunciationEntry, ShareSchedule, TurnCitation,
                          VoiceTuning)

router = APIRouter()


def _persona(s: Session, pid: str, acc: Account) -> Persona:
    p = s.get(Persona, pid)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    from .. import db
    from ..models_extra import ensure_tables

    cr.ensure(); ensure_tables(db.engine)
    return p


def _conv(s: Session, cid: str, acc: Account) -> Conversation:
    c = s.get(Conversation, cid)
    if not c or c.account_id != acc.id:
        raise HTTPException(404, "conversation not found")
    cr.ensure()
    return c


# ---------------- knowledge from URLs ----------------


class UrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    title: str = ""


@router.post("/personas/{pid}/knowledge/url")
def add_url(pid: str, body: UrlIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    try:
        title, text, h = gap_knowledge.fetch_document(body.url)
        doc = kb.ingest(pid, body.title or title, text, "url", session=s)
    except ValueError as e:
        raise HTTPException(422, str(e))
    out = {**doc.model_dump(), "url": body.url.strip()}
    s.add(KnowledgeSource(doc_id=doc.id, persona_id=pid, url=body.url.strip(), content_hash=h)); s.commit()
    return out


@router.post("/personas/{pid}/knowledge/{did}/refresh")
def refresh_url(pid: str, did: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Re-fetch a URL source. Unchanged content is a no-op; changed content replaces the document's chunks."""
    _persona(s, pid, acc)
    src = s.get(KnowledgeSource, did)
    doc = s.get(KnowledgeDoc, did)
    if not src or not doc or src.persona_id != pid:
        raise HTTPException(404, "URL source not found")
    try:
        title, text, h = gap_knowledge.fetch_document(src.url)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if h == src.content_hash:
        src.fetched_at = datetime.now(timezone.utc); s.add(src); s.commit()
        return {"changed": False, "doc_id": did}
    new = kb.ingest(pid, doc.title, text, "url", session=s)
    kb.delete_doc(did, s)
    s.add(KnowledgeSource(doc_id=new.id, persona_id=pid, url=src.url, content_hash=h))
    s.delete(src); s.commit()
    return {"changed": True, "doc_id": new.id}


@router.get("/personas/{pid}/knowledge-sources")
def list_sources(pid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return [{"doc_id": x.doc_id, "url": x.url, "fetched_at": x.fetched_at}
            for x in s.exec(select(KnowledgeSource).where(KnowledgeSource.persona_id == pid)).all()]


@router.get("/conversations/{cid}/citations")
def citations(cid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Per assistant turn: the knowledge excerpts the answer was grounded on."""
    _conv(s, cid, acc)
    by: dict[int, list] = defaultdict(list)
    for c in s.exec(select(TurnCitation).where(TurnCitation.conversation_id == cid).order_by(TurnCitation.seq, TurnCitation.rank)).all():
        by[c.seq].append({"doc_id": c.doc_id, "title": c.title, "url": c.url or None, "score": c.score, "snippet": c.snippet})
    return [{"seq": k, "sources": v} for k, v in sorted(by.items())]


# ---------------- pronunciation ----------------


class PronIn(BaseModel):
    term: str = Field(min_length=1, max_length=80)
    replacement: str = Field(min_length=1, max_length=120)
    case_sensitive: bool = False


@router.get("/personas/{pid}/pronunciations")
def list_pron(pid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return s.exec(select(PronunciationEntry).where(PronunciationEntry.persona_id == pid).order_by(PronunciationEntry.created_at)).all()


@router.post("/personas/{pid}/pronunciations")
def add_pron(pid: str, body: PronIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    rows = s.exec(select(PronunciationEntry).where(PronunciationEntry.persona_id == pid)).all()
    if len(rows) >= pronunciation.MAX_ENTRIES:
        raise HTTPException(422, f"max {pronunciation.MAX_ENTRIES} entries")
    existing = next((r for r in rows if r.term.lower() == body.term.strip().lower()), None)
    e = existing or PronunciationEntry(persona_id=pid, term=body.term.strip(), replacement="")
    e.replacement, e.case_sensitive = body.replacement.strip(), body.case_sensitive
    s.add(e); s.commit(); s.refresh(e)
    return e


@router.delete("/personas/{pid}/pronunciations/{eid}")
def del_pron(pid: str, eid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    e = s.get(PronunciationEntry, eid)
    if not e or e.persona_id != pid:
        raise HTTPException(404, "entry not found")
    s.delete(e); s.commit()
    return {"deleted": eid}


class PronPreviewIn(BaseModel):
    text: str = Field(max_length=2000)


@router.post("/personas/{pid}/pronunciations/preview")
def preview_pron(pid: str, body: PronPreviewIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Shows the exact text the TTS will be given."""
    _persona(s, pid, acc)
    rules = pronunciation.compile_rules(s.exec(select(PronunciationEntry).where(PronunciationEntry.persona_id == pid)).all())
    return {"original": body.text, "spoken_as": pronunciation.apply(body.text, rules)}


# ---------------- interruption / turn tuning ----------------


class TuningIn(BaseModel):
    interruption_sensitivity: float | None = Field(default=None, ge=0, le=1)
    allow_interruptions: bool | None = None
    turn_patience_ms: int | None = Field(default=None, ge=300, le=3000)


def _tuning_out(pid: str, t: VoiceTuning | None) -> dict:
    t = t or VoiceTuning(persona_id=pid, account_id="")
    return {"persona_id": pid, "interruption_sensitivity": t.interruption_sensitivity,
            "allow_interruptions": t.allow_interruptions, "turn_patience_ms": t.turn_patience_ms}


@router.get("/personas/{pid}/voice-tuning")
def get_tuning(pid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return _tuning_out(pid, s.get(VoiceTuning, pid))


@router.put("/personas/{pid}/voice-tuning")
def put_tuning(pid: str, body: TuningIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    t = s.get(VoiceTuning, pid) or VoiceTuning(persona_id=pid, account_id=acc.id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(t, k, v)
    t.updated_at = datetime.now(timezone.utc)
    s.add(t); s.commit(); s.refresh(t)
    return _tuning_out(pid, t)


# ---------------- insights ----------------


@router.get("/conversations/{cid}/insights")
def conv_insights(cid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _conv(s, cid, acc)
    r = insights.compute_and_store(s, cid)  # cheap and idempotent: always reflects the turns recorded so far
    return insights.out(r)


@router.get("/analytics/insights")
def insights_overview(days: int = 30, persona_id: str | None = None, acc: Account = Depends(current_account),
                      s: Session = Depends(get_session)):
    cr.ensure()
    days = min(max(days, 1), 365)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = [r for r in s.exec(select(ConversationInsight).where(ConversationInsight.account_id == acc.id)).all()
            if cr.utc(r.created_at) >= since and (not persona_id or r.persona_id == persona_id)]
    labels = Counter(r.label for r in rows)
    topics = Counter(t for r in rows for t in json.loads(r.topics))
    per_day: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        per_day[cr.utc(r.created_at).date().isoformat()].append(r.sentiment)
    return {"days": days, "conversations": len(rows),
            "avg_sentiment": round(sum(r.sentiment for r in rows) / len(rows), 3) if rows else None,
            "labels": {k: labels.get(k, 0) for k in ("positive", "neutral", "negative")},
            "declining": sum(1 for r in rows if r.trend == "declining"),
            "top_topics": [{"topic": t, "count": n} for t, n in topics.most_common(10)],
            "by_day": [{"date": d, "avg_sentiment": round(sum(v) / len(v), 3), "conversations": len(v)} for d, v in sorted(per_day.items())],
            "most_negative": [{"conversation_id": r.conversation_id, "sentiment": r.sentiment}
                              for r in sorted(rows, key=lambda r: r.sentiment)[:5] if r.sentiment < 0]}


# ---------------- scheduled links ----------------


class ScheduleIn(BaseModel):
    starts_at: datetime
    duration_minutes: int = Field(default=30, ge=5, le=480)
    invitee_name: str = Field(default="", max_length=80)
    invitee_email: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=300)


def _sched_out(x: ShareSchedule) -> dict:
    return {"token": x.token, "starts_at": cr.utc(x.starts_at), "ends_at": cr.utc(x.ends_at), "invitee_name": x.invitee_name,
            "invitee_email": x.invitee_email, "note": x.note}


def _own_link(s: Session, token: str, acc: Account) -> ShareLink:
    cr.ensure()
    l = s.get(ShareLink, token)
    if not l or l.account_id != acc.id:
        raise HTTPException(404, "share link not found")
    return l


@router.put("/share/{token}/schedule")
def set_schedule(token: str, body: ScheduleIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """The link only opens at starts_at (guests before then get 425) and closes after the duration."""
    l = _own_link(s, token, acc)
    st = cr.utc(body.starts_at) if body.starts_at.tzinfo else body.starts_at.replace(tzinfo=timezone.utc)
    end = st + timedelta(minutes=body.duration_minutes)
    if end <= datetime.now(timezone.utc):
        raise HTTPException(422, "the scheduled window is already over")
    x = s.get(ShareSchedule, token) or ShareSchedule(token=token, account_id=acc.id, starts_at=st)
    x.starts_at, x.ends_at = st, end
    x.invitee_name, x.invitee_email, x.note = body.invitee_name, body.invitee_email, body.note
    l.expires_at = max(cr.utc(l.expires_at), end) if l.expires_at else None  # keep an expiry that would cut the window short
    s.add(x); s.add(l); s.commit()
    return _sched_out(x)


@router.get("/share/{token}/schedule")
def get_schedule(token: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _own_link(s, token, acc)
    x = s.get(ShareSchedule, token)
    if not x:
        raise HTTPException(404, "no schedule on this link")
    return _sched_out(x)


@router.delete("/share/{token}/schedule")
def clear_schedule(token: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _own_link(s, token, acc)
    x = s.get(ShareSchedule, token)
    if x:
        s.delete(x); s.commit()
    return {"deleted": token}


def _ics_escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def schedule_ics(x: ShareSchedule, url: str, title: str) -> str:
    f = lambda d: cr.utc(d).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Mirage//Scheduled call//EN", "METHOD:PUBLISH", "BEGIN:VEVENT",
        f"UID:{x.token}@mirage", f"DTSTAMP:{f(x.created_at)}", f"DTSTART:{f(x.starts_at)}", f"DTEND:{f(x.ends_at)}",
        f"SUMMARY:{_ics_escape(title)}", f"DESCRIPTION:{_ics_escape((x.note + chr(10) if x.note else '') + 'Join: ' + url)}",
        f"URL:{url}", "END:VEVENT", "END:VCALENDAR", ""])


@router.get("/guest/{token}/schedule.ics")
def public_ics(token: str, request: Request, s: Session = Depends(get_session)):
    """Public (the token is the credential): lets the invitee add the call to a calendar."""
    cr.ensure()
    l = s.get(ShareLink, token)
    x = s.get(ShareSchedule, token)
    if not l or l.revoked_at is not None or not x:
        raise HTTPException(404, "not found")
    p = s.get(Persona, l.persona_id)
    return Response(schedule_ics(x, f"{str(request.base_url).rstrip('/')}/guest/{token}", f"Call with {p.name if p else 'AI agent'}"), media_type="text/calendar")
