"""Persona config (language, greeting, objectives, guardrails, custom LLM, memory) and tools."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import convo_runtime as cr, languages, webhooks
from ..auth import current_account
from ..db import Account, Persona, get_session
from ..models_features import PersonaConfig, PersonaTool
from ..secretbox import encrypt

router = APIRouter()


class Objective(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    success_criteria: str = ""
    output_variables: list[str] = []


class Guardrail(BaseModel):
    name: str = ""
    rule: str = Field(min_length=1, max_length=500)  # injected into the system prompt
    forbidden_phrases: list[str] = []  # enforced on the output (clause by clause) before it is spoken


class CustomLLM(BaseModel):
    base_url: str = ""  # OpenAI-compatible, e.g. https://api.openai.com/v1 ; "" = local Ollama
    model: str = ""
    api_key: str | None = None  # write-only: stored encrypted, never returned. Omit to keep, "" to clear.


class ConfigIn(BaseModel):
    language: str | None = None
    greeting: str | None = None
    objectives: list[Objective] | None = None
    guardrails: list[Guardrail] | None = None
    guardrail_fallback: str | None = None
    memory_enabled: bool | None = None
    custom_llm: CustomLLM | None = None
    stt_model: str | None = None


def _persona(s: Session, pid: str, acc: Account) -> Persona:
    p = s.get(Persona, pid)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    cr.ensure()
    return p


def _out(pid: str, c: PersonaConfig | None) -> dict:
    c = c or PersonaConfig(persona_id=pid, account_id="")
    return {"persona_id": pid, "language": c.language, "greeting": c.greeting,
            "objectives": json.loads(c.objectives), "guardrails": json.loads(c.guardrails),
            "guardrail_fallback": c.guardrail_fallback, "memory_enabled": c.memory_enabled,
            "custom_llm": {"base_url": c.llm_base_url, "model": c.llm_model, "has_api_key": bool(c.llm_api_key_enc)},
            "stt_model": c.stt_model}


@router.get("/personas/{pid}/config")
def get_config(pid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return _out(pid, s.get(PersonaConfig, pid))


@router.put("/personas/{pid}/config")
def put_config(pid: str, body: ConfigIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Partial update: only fields present in the body change."""
    _persona(s, pid, acc)
    c = s.get(PersonaConfig, pid) or PersonaConfig(persona_id=pid, account_id=acc.id)
    if body.language is not None:
        try:
            c.language = languages.normalize_language(body.language)
        except ValueError as e:
            raise HTTPException(422, str(e))
    if body.greeting is not None:
        if len(body.greeting) > 500:
            raise HTTPException(422, "greeting max 500 chars")
        c.greeting = body.greeting
    if body.objectives is not None:
        names = [o.name for o in body.objectives]
        if len(set(names)) != len(names):
            raise HTTPException(422, "objective names must be unique")
        c.objectives = json.dumps([o.model_dump() for o in body.objectives])
    if body.guardrails is not None:
        c.guardrails = json.dumps([g.model_dump() for g in body.guardrails])
    if body.guardrail_fallback is not None:
        c.guardrail_fallback = body.guardrail_fallback
    if body.memory_enabled is not None:
        c.memory_enabled = body.memory_enabled
    if body.stt_model is not None:
        c.stt_model = body.stt_model
    if body.custom_llm is not None:
        u = body.custom_llm
        if u.base_url:
            try:
                webhooks.validate_url(u.base_url)
            except ValueError as e:
                raise HTTPException(422, f"custom_llm.base_url: {e}")
        c.llm_base_url, c.llm_model = u.base_url, u.model
        if u.api_key is not None:
            c.llm_api_key_enc = encrypt(u.api_key)
        if not u.base_url:
            c.llm_api_key_enc = ""
    from ..db import now
    c.updated_at = now()
    s.add(c); s.commit(); s.refresh(c)
    return _out(pid, c)


# ---- tools ----
class ToolIn(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}}  # JSON schema for the arguments
    webhook_url: str
    secret: str | None = None  # optional: calls are signed with Mirage-Signature (same scheme as webhooks)
    timeout_s: float = Field(default=8.0, gt=0, le=30)


def _tool_out(t: PersonaTool) -> dict:
    return {"id": t.id, "name": t.name, "description": t.description, "parameters": json.loads(t.parameters),
            "webhook_url": t.webhook_url, "has_secret": bool(t.secret_enc), "timeout_s": t.timeout_s}


@router.post("/personas/{pid}/tools")
def add_tool(pid: str, body: ToolIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    try:
        webhooks.validate_url(body.webhook_url)
    except ValueError as e:
        raise HTTPException(422, f"webhook_url: {e}")
    if body.parameters.get("type") != "object":
        raise HTTPException(422, "parameters must be a JSON schema with type 'object'")
    if s.exec(select(PersonaTool).where(PersonaTool.persona_id == pid, PersonaTool.name == body.name)).first():
        raise HTTPException(409, "a tool with this name already exists")
    t = PersonaTool(persona_id=pid, account_id=acc.id, name=body.name, description=body.description,
                    parameters=json.dumps(body.parameters), webhook_url=body.webhook_url,
                    secret_enc=encrypt(body.secret) if body.secret else "", timeout_s=body.timeout_s)
    s.add(t); s.commit(); s.refresh(t)
    return _tool_out(t)


@router.get("/personas/{pid}/tools")
def list_tools(pid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    return [_tool_out(t) for t in s.exec(select(PersonaTool).where(PersonaTool.persona_id == pid)).all()]


@router.delete("/personas/{pid}/tools/{tid}")
def delete_tool(pid: str, tid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    _persona(s, pid, acc)
    t = s.get(PersonaTool, tid)
    if not t or t.persona_id != pid:
        raise HTTPException(404, "tool not found")
    s.delete(t); s.commit()
    return {"deleted": tid}
