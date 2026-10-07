"""Lead capture: validation + storage + the built-in `capture_lead` tool.

The tool is injected into a persona's tool list by builtin_tools.tools_for() (only when the persona has lead capture
enabled) and executed in-process via the `vocalface-internal://capture_lead` URL scheme (llm_backends.execute_tool).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session as DB, SQLModel, select

from . import db, webhooks
from .models_leads import Lead, LeadCaptureConfig

FIELDS = ("name", "email", "phone", "company", "interest", "notes")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-']{1,64}@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_ensured: set[int] = set()
log = logging.getLogger('vocalface.leads')


def ensure() -> None:
    if id(db.engine) not in _ensured:
        SQLModel.metadata.create_all(db.engine)
        _ensured.add(id(db.engine))


class LeadError(ValueError):
    pass


def norm_email(v: Any) -> str:
    v = str(v or "").strip().strip(".,;:").lower()
    if not v:
        return ""
    if "@" not in v:  # speech-to-text style: "ravi at example dot com"
        v = re.sub(r"\s+at\s+", "@", v)
        v = re.sub(r"\s+dot\s+", ".", v)
    v = v.replace(" ", "")
    if len(v) > 254 or not _EMAIL.match(v):
        raise LeadError("email does not look valid; ask the person to spell it out again")
    return v


def norm_phone(v: Any) -> str:
    raw = str(v or "").strip()
    if not raw:
        return ""
    if re.search(r"[A-Za-z]", re.sub(r"(?i)\b(ext|x)\b.*$", "", raw)):
        raise LeadError("phone number must contain digits only; ask the person to repeat it")
    digits = re.sub(r"\D", "", raw)
    plus = raw.lstrip().startswith("+")
    if digits.startswith("00") and not plus:
        digits, plus = digits[2:], True
    if not 7 <= len(digits) <= 15:
        raise LeadError("phone number must have 7 to 15 digits; ask the person to repeat it (with country code if possible)")
    return ("+" if plus else "") + digits


def norm_text(v: Any, limit: int, label: str) -> str:
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    if len(v) > limit:
        raise LeadError(f"{label} is too long (max {limit} characters)")
    return v


def truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "y", "1", "ok", "agreed")
    return bool(v)


def lead_out(l: Lead) -> dict:
    return {"id": l.id, "conversation_id": l.conversation_id, "persona_id": l.persona_id, "name": l.name, "email": l.email,
            "phone": l.phone, "company": l.company, "interest": l.interest, "notes": l.notes,
            "extra": json.loads(l.extra or "{}"), "consent": l.consent, "consent_text": l.consent_text,
            "source": l.source, "created_at": l.created_at, "updated_at": l.updated_at}


def config_for(s: DB, persona_id: str) -> Optional[LeadCaptureConfig]:
    ensure()
    return s.get(LeadCaptureConfig, persona_id)


def required_fields(cfg: LeadCaptureConfig) -> list[str]:
    try:
        r = [f for f in json.loads(cfg.required_fields or "[]") if f in FIELDS]
    except ValueError:
        r = ["name"]
    return r


def save_lead(s: DB, *, account_id: str, conversation_id: str, persona_id: str, args: dict, cfg: Optional[LeadCaptureConfig],
              source: str = "tool", enforce_consent: bool = True) -> tuple[Lead, bool]:
    """Validate and upsert the lead of a conversation. Returns (lead, created). Raises LeadError."""
    ensure()
    clean: dict[str, str] = {}
    clean["name"] = norm_text(args.get("name"), 120, "name")
    if clean["name"] and "@" in clean["name"]:
        raise LeadError("name looks like an email address; ask for the person's name")
    clean["email"] = norm_email(args.get("email"))
    clean["phone"] = norm_phone(args.get("phone"))
    clean["company"] = norm_text(args.get("company"), 160, "company")
    clean["interest"] = norm_text(args.get("interest"), 300, "interest")
    clean["notes"] = norm_text(args.get("notes"), 1000, "notes")
    known = set(FIELDS) | {"consent"}
    extra = {str(k)[:40]: norm_text(v, 300, str(k)) for k, v in args.items() if k not in known and v not in (None, "", [], {})
             and isinstance(v, (str, int, float))}
    if len(extra) > 10:
        raise LeadError("too many extra fields")
    existing = s.exec(select(Lead).where(Lead.conversation_id == conversation_id)).first()
    merged = {f: clean[f] or (getattr(existing, f) if existing else "") for f in FIELDS}
    required = required_fields(cfg) if cfg else ["name"]
    missing = [f for f in required if not merged.get(f)]
    if missing:
        raise LeadError("missing required field(s): " + ", ".join(missing) + "; ask the person for them")
    if not merged["email"] and not merged["phone"]:
        raise LeadError("need at least an email or a phone number; ask how the team can reach them")
    consent = truthy(args.get("consent")) or bool(existing and existing.consent)
    if (cfg.require_consent if cfg else True) and enforce_consent and not consent:
        raise LeadError("the person has not agreed yet: explain in one sentence that their details are only used so the "
                        "team can follow up, ask if that is OK, and call capture_lead again with consent=true once they say yes")
    created = existing is None
    l = existing or Lead(account_id=account_id, conversation_id=conversation_id, persona_id=persona_id, source=source)
    for f in FIELDS:
        setattr(l, f, merged[f])
    if extra:
        l.extra = json.dumps({**json.loads(l.extra or "{}"), **extra})
    l.consent = consent
    if consent and cfg and not l.consent_text:
        l.consent_text = cfg.disclosure
    if not created:
        l.updated_at = datetime.now(timezone.utc)
    s.add(l); s.commit(); s.refresh(l)
    webhooks.emit(account_id, "lead.captured" if created else "lead.updated",
                  {"lead": lead_out(l), "conversation_id": conversation_id, "persona_id": persona_id}, session=s)
    return l, created


def run_internal(name: str, args: dict, ctx: dict) -> tuple[bool, str]:
    """Executor behind llm_backends.execute_tool for vocalface-internal:// tools. Never raises."""
    try:
        if name != "capture_lead":
            return False, json.dumps({"error": f"unknown internal tool {name}"})
        from .db import Conversation

        with DB(db.engine) as s:
            conv = s.get(Conversation, ctx.get("conversation_id", ""))
            if conv is None:
                return False, json.dumps({"error": "conversation not found"})
            cfg = config_for(s, conv.persona_id)
            if cfg is None or not cfg.enabled:
                return False, json.dumps({"error": "lead capture is disabled for this persona"})
            try:
                lead, created = save_lead(s, account_id=conv.account_id, conversation_id=conv.id, persona_id=conv.persona_id,
                                          args=args or {}, cfg=cfg)
            except LeadError as e:
                return False, json.dumps({"saved": False, "error": str(e)})
            return True, json.dumps({"saved": True, "message": "Thanks, I have saved your details so the team can follow up."
                                     if created else "Thanks, I have updated your details."})
    except Exception as e:  # noqa: BLE001
        return False, json.dumps({"error": f"lead capture failed: {type(e).__name__}"})


def analytics_block(s: DB, account_id: str, convs: list, personas: dict[str, str], since) -> dict:
    """Additive part of GET /v1/analytics: lead counts and objective completion rates per persona (conversations in range)."""
    from collections import defaultdict

    from .models_features import ObjectiveProgress, PersonaConfig

    ensure()
    n_conv: dict[str, int] = defaultdict(int)
    cid_persona = {}
    for c in convs:
        n_conv[c.persona_id] += 1
        cid_persona[c.id] = c.persona_id
    lead_rows = [l for l in s.exec(select(Lead).where(Lead.account_id == account_id)).all()
                 if (l.created_at if l.created_at.tzinfo else l.created_at.replace(tzinfo=timezone.utc)) >= since]
    per_lead: dict[str, int] = defaultdict(int)
    for l in lead_rows:
        per_lead[l.persona_id] += 1
    leads_pp = [{"persona_id": pid, "name": personas.get(pid, "(deleted)"), "leads": per_lead.get(pid, 0), "conversations": n_conv.get(pid, 0),
                 "conversion_rate": round(per_lead.get(pid, 0) / n_conv[pid], 3) if n_conv.get(pid) else None}
                for pid in sorted(set(per_lead) | set(n_conv), key=lambda p: -per_lead.get(p, 0))]
    done: dict[tuple[str, str], int] = defaultdict(int)
    for o in s.exec(select(ObjectiveProgress).where(ObjectiveProgress.conversation_id.in_(list(cid_persona) or ["-"]))).all():
        if o.completed and o.conversation_id in cid_persona:
            done[(cid_persona[o.conversation_id], o.name)] += 1
    obj_pp = []
    for pid in sorted(n_conv):
        cfg = s.get(PersonaConfig, pid)
        for o in (jl(cfg.objectives) if cfg else []):
            d = done.get((pid, o["name"]), 0)
            obj_pp.append({"persona_id": pid, "name": personas.get(pid, "(deleted)"), "objective": o["name"],
                           "conversations": n_conv[pid], "completed": d, "completion_rate": round(d / n_conv[pid], 3)})
    return {"leads": {"total": len(lead_rows), "per_persona": leads_pp}, "objectives": {"per_persona": obj_pp}}


def jl(s: str) -> list:
    try:
        return json.loads(s or "[]")
    except ValueError:
        return []


def on_objective_completed(account_id: str, conversation_id: str, persona_id: str, variables: dict) -> None:
    """Fallback: an objective whose judged variables include a name plus an email/phone means the visitor agreed to be
    contacted (the template success criteria require agreement). Store the lead (source='objective') if the model did not
    call capture_lead itself. Never raises."""
    try:
        v = {k: str(x) for k, x in (variables or {}).items() if k in FIELDS and isinstance(x, (str, int)) and str(x).strip()
             and str(x).strip().lower() not in ("null", "none", "true", "false")}
        v.update({k: x for k, x in ground(v, user_text(conversation_id)).items() if k in ("name", "email", "phone")})
        v = {k: x for k, x in v.items() if x}
        if not v.get("name") or not (v.get("email") or v.get("phone")):
            return
        with DB(db.engine) as s:
            cfg = config_for(s, persona_id)
            if cfg is None or not cfg.enabled or s.exec(select(Lead).where(Lead.conversation_id == conversation_id)).first():
                return
            save_lead(s, account_id=account_id, conversation_id=conversation_id, persona_id=persona_id,
                      args={**{k: str(x) for k, x in v.items()}, "consent": True}, cfg=cfg, source="objective")
    except Exception:  # noqa: BLE001 - invalid judged values (bad email etc.) are simply not stored
        pass


async def sweep(rt) -> Optional[Lead]:
    """Safety net at the end of a conversation (finalize_conversation): if the persona has lead capture enabled, no lead was
    stored and the transcript shows the visitor gave contact details AND clearly agreed to be contacted, extract and store them.
    Uses the judge model; invalid values are dropped. Returns the lead or None. Never raises."""
    try:
        from . import llm_backends as lb

        with DB(db.engine) as s:
            cfg = config_for(s, rt.persona_id)
            if cfg is None or not cfg.enabled or s.exec(select(Lead).where(Lead.conversation_id == rt.cid)).first():
                return None
        transcript = rt._transcript_text()
        if "User:" not in transcript:
            return None
        prompt = ("From this conversation extract the VISITOR's contact details, only what the visitor actually said (never the agent's "
                  "words, never guesses; use empty strings otherwise). Also say whether the visitor clearly agreed to be contacted or to "
                  "have their details passed to the team (agreed=true only for an explicit yes/OK/please do, not for merely giving details "
                  "when asked).\n\nTranscript:\n" + transcript[-6000:] +
                  '\n\nAnswer with JSON only: {"name":"","email":"","phone":"","company":"","interest":"","notes":"","agreed":true|false}')
        out = await lb.complete(rt.judge_backend(), "You are a strict, concise JSON-only extractor.", prompt)
        d = lb.parse_json_obj(out)
        if not d.get("agreed") or not d.get("name") or not (d.get("email") or d.get("phone")):
            return None
        args = {k: str(d.get(k) or "") for k in FIELDS if str(d.get(k) or "").lower() not in ("null", "none")}
        args.update({k: v for k, v in ground(args, user_text(rt.cid)).items() if k in ("name", "email", "phone")})
        if not args.get("name") or not (args.get("email") or args.get("phone")):
            return None
        args["consent"] = True
        with DB(db.engine) as s:
            l, _ = save_lead(s, account_id=rt.account_id, conversation_id=rt.cid, persona_id=rt.persona_id, args=args,
                             cfg=config_for(s, rt.persona_id), source="sweep")
            return lead_out_obj(l)
    except Exception:  # noqa: BLE001
        log.exception('lead sweep failed')
        return None


def lead_out_obj(l: Lead) -> Lead:
    """Detach a fully loaded copy (the session that loaded it is closing)."""
    return Lead(**{c: getattr(l, c) for c in Lead.model_fields})


def user_text(conversation_id: str) -> str:
    from .models_features import TranscriptTurn

    with DB(db.engine) as s:
        rows = s.exec(select(TranscriptTurn).where(TranscriptTurn.conversation_id == conversation_id, TranscriptTurn.role == "user")
                      .order_by(TranscriptTurn.seq)).all()
        return "\n".join(r.text for r in rows)


def ground(fields: dict, said: str) -> dict:
    """Keep only values the visitor actually said. Small judge models scramble spoken digits, so the phone number is taken
    from the visitor's own words (a 7-15 digit run, spaces/dashes allowed) rather than from the model's rendering."""
    out = dict(fields)
    low = said.lower()
    if out.get("name") and not all(w in low for w in re.findall(r"[^\W\d_]+", out["name"].lower())[:3]):
        out["name"] = ""
    if out.get("email"):
        flat = re.sub(r"\s+", "", low.replace(" at ", "@").replace(" dot ", "."))
        if out["email"].lower() not in flat:
            out["email"] = ""
    runs = [re.sub(r"\D", "", m) for m in re.findall(r"\+?\d[\d\s\-().]{5,}\d", said)]
    runs = [r for r in runs if 7 <= len(r) <= 15]
    if runs:
        out["phone"] = ("+" if re.search(r"\+\s?" + runs[-1][:2], said) else "") + runs[-1]
    else:
        out["phone"] = ""
    return out
