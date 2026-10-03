"""Persona templates library: browse niche templates and create a ready persona in one call."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from .. import convo_runtime as cr, knowledge as kb, languages, leads, templates as T, webhooks
from ..auth import current_account
from ..db import Account, Persona, Replica, get_session, now
from ..models_features import PersonaConfig
from ..models_leads import LeadCaptureConfig
from ..models_templates import PersonaIntegration, TemplateInstance
from ..secretbox import encrypt

router = APIRouter()


class InstantiateIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)  # persona name (default: the template's)
    variables: dict[str, str] = {}  # replace {{placeholders}} in the prompt/greeting/guardrails, e.g. {"company_name": "Zenith"}
    language: str | None = None  # override the template language
    llm: str | None = None  # override the suggested model, e.g. "ollama/llama3.2:3b"
    tts_voice: str = "default"
    replica_id: str | None = None
    include_sample_knowledge: bool = True  # ingest the clearly-fake SAMPLE documents (replace them with your own)
    enable_lead_capture: bool = True
    booking_webhook_url: str | None = None  # optional: enables the book_meeting tool (see docs/WIDGET.md / API.md)
    booking_secret: str | None = None
    notify_webhook_url: str | None = None  # optional: enables the send_notification tool
    notify_secret: str | None = None


@router.get("/templates")
def list_templates(niche: str | None = None, acc: Account = Depends(current_account)):
    items = [T.summary(t) for t in T.load().values() if not niche or t["niche"] == niche]
    return {"templates": sorted(items, key=lambda x: x["id"]), "niches": sorted({t["niche"] for t in T.load().values()})}


@router.get("/templates/{tid}")
def get_template(tid: str, acc: Account = Depends(current_account)):
    t = T.get(tid)
    if not t:
        raise HTTPException(404, "template not found")
    return T.detail(t)


@router.post("/templates/{tid}/instantiate")
def instantiate(tid: str, body: InstantiateIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    t = T.get(tid)
    if not t:
        raise HTTPException(404, "template not found")
    cr.ensure()
    leads.ensure()
    if body.replica_id and not ((r := s.get(Replica, body.replica_id)) and r.account_id == acc.id):
        raise HTTPException(404, "replica not found")
    if body.tts_voice.startswith("clone"):
        from ..routers.resources import _check_voice

        _check_voice(s, acc, body.tts_voice)
    unknown = [k for k in body.variables if k not in t["variables"]]
    if unknown or any(len(v) > 200 for v in body.variables.values()):
        raise HTTPException(422, f"variables: unknown {unknown} (valid: {sorted(t['variables'])}) or value over 200 chars")
    lang = t["language"]
    if body.language:
        try:
            lang = languages.normalize_language(body.language)
        except ValueError as e:
            raise HTTPException(422, str(e))
    for label, u in (("booking_webhook_url", body.booking_webhook_url), ("notify_webhook_url", body.notify_webhook_url)):
        if u:
            try:
                webhooks.validate_url(u)
            except ValueError as e:
                raise HTTPException(422, f"{label}: {e}")
    v = {**t["variables"], **body.variables}
    f = lambda x: T.fill(x, v)  # noqa: E731
    p = Persona(account_id=acc.id, name=body.name or f(t.get("persona_name", t["name"])), system_prompt=f(t["system_prompt"]),
                replica_id=body.replica_id, llm=body.llm or t["suggested_llm"], tts_voice=body.tts_voice)
    s.add(p)
    s.add(PersonaConfig(persona_id=p.id, account_id=acc.id, language=lang, greeting=f(t["greeting"]),
                        objectives=json.dumps([{**o, "description": f(o.get("description", ""))} for o in t["objectives"]]),
                        guardrails=json.dumps([{**g, "rule": f(g["rule"])} for g in t["guardrails"]]),
                        guardrail_fallback=f(t.get("guardrail_fallback", "")) or "Sorry, I can't help with that."))
    s.add(TemplateInstance(persona_id=p.id, account_id=acc.id, template_id=t["id"], template_version=t["version"]))
    lead_required = [x for x in t.get("lead_required", ["name"])]
    if body.enable_lead_capture:
        s.add(LeadCaptureConfig(persona_id=p.id, account_id=acc.id, enabled=True, required_fields=json.dumps(lead_required)))
    if body.booking_webhook_url or body.notify_webhook_url:
        s.add(PersonaIntegration(persona_id=p.id, account_id=acc.id, booking_url=body.booking_webhook_url or "",
                                 booking_secret_enc=encrypt(body.booking_secret) if body.booking_secret else "",
                                 notify_url=body.notify_webhook_url or "",
                                 notify_secret_enc=encrypt(body.notify_secret) if body.notify_secret else "", updated_at=now()))
    s.commit(); s.refresh(p)
    docs = []
    if body.include_sample_knowledge:
        for d in t["knowledge_docs"]:
            doc = kb.ingest(p.id, d["title"], d["text"], source="template", session=s)
            docs.append({"id": doc.id, "title": doc.title, "chunks": doc.n_chunks})
    tools = ["capture_lead"] if body.enable_lead_capture else []
    if body.booking_webhook_url:
        tools.append("book_meeting")
    if body.notify_webhook_url:
        tools.append("send_notification")
    warnings = []
    if docs:
        warnings.append("Sample knowledge documents describe a FICTIONAL company (%s). Replace them with your own: add yours with "
                        "POST /v1/personas/%s/knowledge/text, then DELETE the SAMPLE documents." % (t["variables"].get("company_name") or
                        t["variables"].get("clinic_name") or t["variables"].get("venue_name") or t["variables"].get("school_name")
                        or t["variables"].get("agency_name") or "see titles", p.id))
    if t.get("safety_notes"):
        warnings.append(t["safety_notes"])
    return {"persona_id": p.id, "template_id": t["id"], "persona": {"id": p.id, "name": p.name, "llm": p.llm, "tts_voice": p.tts_voice,
                                                                 "replica_id": p.replica_id, "system_prompt": p.system_prompt},
            "config": {"language": lang, "greeting": f(t["greeting"]), "objectives": [o["name"] for o in t["objectives"]],
                       "guardrails": [g.get("name", "") for g in t["guardrails"]]},
            "knowledge_docs": docs, "tools": tools,
            "lead_capture": {"enabled": body.enable_lead_capture, "required_fields": lead_required if body.enable_lead_capture else []},
            "warnings": warnings,
            "next_steps": ["POST /v1/personas/%s/widget to get an embeddable 'Talk to us' snippet" % p.id,
                           "GET /v1/leads to see captured leads", "POST /v1/webhooks with events [\"lead.captured\"]"]}
