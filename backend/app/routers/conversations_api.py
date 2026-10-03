"""Transcripts, summaries, objectives, tool-call log for conversations."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from .. import convo_runtime as cr, db
from ..auth import current_account
from ..db import Account, Conversation, get_session
from ..models_features import ConversationMetric, ConversationMeta, ObjectiveProgress, ToolCallLog

router = APIRouter()


def _conv(s: Session, cid: str, acc: Account) -> Conversation:
    c = s.get(Conversation, cid)
    if not c or c.account_id != acc.id:
        raise HTTPException(404, "conversation not found")
    cr.ensure()
    return c


@router.get("/conversations/{cid}/transcript")
def transcript(cid: str, format: str = "json", acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    c = _conv(s, cid, acc)
    turns = cr.conversation_turns(s, cid)
    meta = s.get(ConversationMeta, cid)
    if format == "text":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("\n".join(f"{'User' if t.role == 'user' else 'Agent'}: {t.text}" for t in turns))
    return {"conversation_id": cid, "persona_id": c.persona_id, "status": c.status, "summary": meta.summary if meta else "",
            "turns": [{"seq": t.seq, "role": t.role, "text": t.text, "t_ms": t.t_ms, "first_audio_ms": t.first_audio_ms,
                       "interrupted": t.interrupted, "created_at": t.created_at} for t in turns]}


@router.get("/conversations/{cid}/summary")
def summary(cid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _conv(s, cid, acc)
    meta = s.get(ConversationMeta, cid)
    return {"conversation_id": cid, "summary": meta.summary if meta else "", "ready": bool(meta and meta.finalized_at)}


@router.get("/conversations/{cid}/objectives")
def objectives(cid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _conv(s, cid, acc)
    return [{"name": o.name, "completed": o.completed, "evidence": o.evidence, "variables": json.loads(o.variables or "{}"),
             "completed_at": o.completed_at}
            for o in s.exec(select(ObjectiveProgress).where(ObjectiveProgress.conversation_id == cid)).all()]


@router.get("/conversations/{cid}/tool-calls")
def tool_calls(cid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _conv(s, cid, acc)
    return [{"tool": t.tool_name, "arguments": json.loads(t.arguments or "{}"), "result": t.result, "ok": t.ok,
             "duration_ms": t.duration_ms, "created_at": t.created_at}
            for t in s.exec(select(ToolCallLog).where(ToolCallLog.conversation_id == cid).order_by(ToolCallLog.created_at)).all()]


@router.get("/conversations/{cid}/metrics")
def metrics(cid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _conv(s, cid, acc)
    m = s.get(ConversationMetric, cid)
    if not m:
        raise HTTPException(404, "metrics not available until the conversation has ended")
    return m
