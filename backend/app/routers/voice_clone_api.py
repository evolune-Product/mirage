"""Consent-gated voice cloning for a replica: POST/GET/DELETE /v1/replicas/{id}/voice (+ /voice/preview).

A cloned voice is a synthetic copy of a real person's voice: it is only ever created for a replica with an active,
voice-verified consent record, only used by its own account, and deleted when the consent is revoked or the replica is
deleted. Every step is audit-logged (GET /v1/audit)."""
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session

from ..auth import current_account
from ..db import Account, Replica, get_session
from ..safety import audit, enforce_rate_limit, moderate_or_raise
from ..voice_clone import service

router = APIRouter()


class VoiceIn(BaseModel):
    force: bool = False  # re-extract the reference and re-test even if a voice already exists


class PreviewIn(BaseModel):
    text: str
    language: str = "en"


def _own(s: Session, rid: str, acc: Account) -> Replica:
    r = s.get(Replica, rid)
    if not r or r.account_id != acc.id:
        raise HTTPException(404, "replica not found")
    return r


def _refuse(e: service.CloneRefused):
    raise HTTPException(e.status, {"error": "voice_clone_refused", "message": str(e)})


@router.post("/replicas/{rid}/voice", status_code=202)
def create_voice(rid: str, background: BackgroundTasks, body: VoiceIn | None = None,
                 acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Clone the replica's voice from its training video. Needs active, voice-verified consent. Asynchronous: poll GET."""
    enforce_rate_limit(acc.api_key, 10, 60, "voice_clone")
    _own(s, rid, acc)
    try:
        was = service.get_voice(s, rid)
        v = service.request_voice(s, acc.id, rid, force=bool(body and body.force))
    except service.CloneRefused as e:
        audit(s, acc.id, "voice.clone_refused", rid, str(e)[:300])
        _refuse(e)
    if v.status == "queued" and (was is None or was.status != "queued" or (body and body.force)):
        background.add_task(service.process_voice, rid, None, True)
    return service.to_dict(v, rid)


@router.get("/replicas/{rid}/voice")
def get_voice(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _own(s, rid, acc)
    v = service.get_voice(s, rid)
    if v is not None and v.status == "ready" and service.usable_reference(rid, acc.id) is None:
        s.expire_all()  # the gate may have just purged it (consent revoked)
        v = service.get_voice(s, rid)
    return service.to_dict(v, rid)


@router.delete("/replicas/{rid}/voice")
def delete_voice(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Delete the cloned voice and its stored reference audio. The replica and its consent are untouched."""
    _own(s, rid, acc)
    return {"deleted": service.delete_voice(s, rid, acc.id, reason="deleted")}


@router.post("/replicas/{rid}/voice/preview")
async def preview_voice(rid: str, body: PreviewIn, acc: Account = Depends(current_account),
                        s: Session = Depends(get_session)):
    """Short WAV of the cloned voice saying `text` (<= 300 chars, moderated). 503 if the clone engine is not available."""
    enforce_rate_limit(acc.api_key, 10, 60, "voice_preview")
    _own(s, rid, acc)
    text = body.text.strip()
    if not text or len(text) > 300:
        raise HTTPException(422, "text must be 1-300 characters")
    moderate_or_raise(text)
    try:
        service.require_consent(s, rid)
    except service.CloneRefused as e:
        _refuse(e)
    ref = await asyncio.to_thread(service.usable_reference, rid, acc.id)
    if ref is None:
        raise HTTPException(409, "cloned voice is not ready")
    from ..voice_clone.sidecar import SAMPLE_RATE, CloneUnavailable, default_sidecar

    try:
        pcm = await asyncio.to_thread(default_sidecar().synth, text, ref, body.language)
    except CloneUnavailable as e:
        raise HTTPException(503, f"voice clone engine unavailable: {e}")
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE); w.writeframes(pcm)
    await asyncio.to_thread(service.audit_use, rid, "preview")
    return Response(buf.getvalue(), media_type="audio/wav", headers={"X-Mirage-Synthetic-Voice": "cloned"})
