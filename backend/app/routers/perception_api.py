"""Perception config + one-off frame description.

GET/PUT /v1/personas/{pid}/perception   - enable, consent acknowledgement, camera/screen, store_frames, VLM model, interval
POST    /v1/perception/describe         - describe one JPEG/PNG (base64) with the local VLM (testing, offline analysis)
GET     /v1/perception/models           - recommended local VLMs with licences (measured numbers: docs/overnight/intelligence.md)
"""
import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from .. import convo_runtime as cr
from ..auth import current_account
from ..db import Account, Persona, get_session, now
from ..models_perception import PerceptionConfig
from ..perception import manager as pm
from ..perception.vlm import PROMPTS, VLM

router = APIRouter()


class PerceptionIn(BaseModel):
    enabled: bool | None = None
    consent_acknowledged: bool | None = None
    require_user_consent: bool | None = None
    camera: bool | None = None
    screen: bool | None = None
    store_frames: bool | None = None
    vlm_model: str | None = Field(default=None, max_length=80)
    interval_s: float | None = Field(default=None, ge=1.0, le=30.0)


def _out(pid: str, c: PerceptionConfig | None) -> dict:
    c = c or PerceptionConfig(persona_id=pid, account_id="")
    return {"persona_id": pid, "enabled": c.enabled, "consent_acknowledged": c.consent_acknowledged,
            "require_user_consent": c.require_user_consent, "camera": c.camera, "screen": c.screen,
            "store_frames": c.store_frames, "vlm_model": c.vlm_model or pm.VLM(c.vlm_model).model, "interval_s": c.interval_s,
            "privacy": {"frames_stored": c.store_frames, "face_identification": False,
                        "active": bool(c.enabled and c.consent_acknowledged)}}


def _persona(s: Session, pid: str, acc: Account) -> Persona:
    p = s.get(Persona, pid)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    cr.ensure()
    return p


@router.get("/personas/{pid}/perception")
def get_perception(pid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return _out(pid, s.get(PerceptionConfig, pid))


@router.put("/personas/{pid}/perception")
def put_perception(pid: str, body: PerceptionIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Partial update. `enabled: true` is refused until `consent_acknowledged` is true (you confirm your users are
    told that camera/screen frames are analysed)."""
    _persona(s, pid, acc)
    c = s.get(PerceptionConfig, pid) or PerceptionConfig(persona_id=pid, account_id=acc.id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    if c.enabled and not c.consent_acknowledged:
        raise HTTPException(422, "set consent_acknowledged=true to enable perception: you confirm users are told that "
                                 "their camera/screen frames are analysed")
    c.updated_at = now()
    s.add(c); s.commit(); s.refresh(c)
    return _out(pid, c)


class DescribeIn(BaseModel):
    jpeg_b64: str = Field(max_length=2_500_000)
    source: str = "camera"
    prompt: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=80)


@router.post("/perception/describe")
async def describe(body: DescribeIn, acc: Account = Depends(current_account)):
    """One-off frame description (nothing is stored). Same prompts and downscaling as the live path."""
    try:
        raw = base64.b64decode(body.jpeg_b64, validate=False)
        small, _ = pm._prep(raw)
    except (binascii.Error, ValueError, OSError):
        raise HTTPException(422, "jpeg_b64 is not a decodable image")
    src = body.source if body.source in PROMPTS else "camera"
    v = VLM(body.model)
    try:
        text = await v.describe(small, body.prompt or PROMPTS[src])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"vision model unavailable: {type(e).__name__}")
    return {"text": text, "model": v.model, "ms": round(v.last_ms)}


@router.get("/perception/models")
def models(acc: Account = Depends(current_account)):
    return {"default": VLM().model, "models": [
        {"model": "moondream", "licence": "Apache-2.0", "commercial": True, "note": "1.8B: fast but often returns empty answers and misses screen text (measured)"},
        {"model": "gemma3:4b", "licence": "Gemma Terms of Use", "commercial": True, "note": "default: 8/8 on OCR+scene tests, 4.5 GB RAM"},
        {"model": "qwen2.5vl:7b", "licence": "Apache-2.0", "commercial": True, "note": "best OCR/screens, slowest"},
        {"model": "qwen2.5vl:3b", "licence": "Qwen Research (non-commercial)", "commercial": False, "note": "do not ship commercially"},
    ]}
