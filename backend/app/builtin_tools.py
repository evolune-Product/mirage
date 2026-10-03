"""Built-in tools every persona can have without registering a webhook per persona:

  capture_lead       in-process (leads.py); available when the persona has lead capture enabled
  book_meeting       POSTs the slot request to the customer's booking webhook (PersonaIntegration.booking_url)
  send_notification  POSTs a message to the customer's notify webhook (PersonaIntegration.notify_url)

`tools_for()` returns *transient* PersonaTool rows (never saved) that ConversationRuntime appends to the persona's own tools.
A persona tool with the same name wins. book_meeting / send_notification only exist when their webhook is configured.
"""
from __future__ import annotations

import json

from sqlmodel import Session as DB

from .models_features import PersonaTool
from .models_templates import PersonaIntegration
from .secretbox import encrypt  # noqa: F401  (re-exported for routers)

INTERNAL = "mirage-internal://"

CAPTURE_LEAD = {
    "name": "capture_lead",
    "description": ("Save the visitor's contact details so the team can follow up. Call it only AFTER the visitor has said they are "
                    "happy to share their details, and only with what they actually told you (never invent or guess values). "
                    "Needs their name plus an email or a phone number. Set consent=true only if they agreed to be contacted."),
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "Full name as the visitor said it"},
        "email": {"type": "string", "description": "Email address"},
        "phone": {"type": "string", "description": "Phone number, with country code if known"},
        "company": {"type": "string", "description": "Company or organisation, if relevant"},
        "interest": {"type": "string", "description": "What they are interested in, in a few words"},
        "notes": {"type": "string", "description": "Anything else the team should know (short)"},
        "consent": {"type": "boolean", "description": "true only if the visitor agreed to be contacted / to have these details saved"},
    }, "required": ["name", "consent"]},
}

BOOK_MEETING = {
    "name": "book_meeting",
    "description": ("Request a meeting or appointment slot. Call it once the visitor has told you their name, email and the day/time "
                    "they would like. Never promise it is confirmed: say the team will confirm by email."),
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string"}, "email": {"type": "string"},
        "preferred_time": {"type": "string", "description": "Day and time as the visitor said it, e.g. 'Tuesday 3pm'"},
        "timezone": {"type": "string", "description": "Visitor's timezone if known, e.g. Asia/Kolkata"},
        "topic": {"type": "string", "description": "What the meeting is about"},
        "duration_minutes": {"type": "integer", "description": "Length in minutes if specified"},
    }, "required": ["name", "email", "preferred_time"]},
}

SEND_NOTIFICATION = {
    "name": "send_notification",
    "description": ("Send a short message to the human team (for example an urgent request or a question you cannot answer). "
                    "Only when the visitor asks you to pass something on, or it is clearly urgent."),
    "parameters": {"type": "object", "properties": {
        "subject": {"type": "string"}, "message": {"type": "string", "description": "What the team needs to know"},
        "urgency": {"type": "string", "enum": ["low", "normal", "high"]},
        "contact": {"type": "string", "description": "How to reach the visitor, if they shared it"},
    }, "required": ["subject", "message"]},
}

ADDENDA = {
    "capture_lead": (
        "Contact details (lead capture): only when it is genuinely useful (the visitor wants a follow-up, a quote, a callback or "
        "a booking), offer once, politely, to have the team follow up. Never push: if they decline or ignore it, keep helping "
        "and do not ask again. Before you ask for details, say in one short sentence how they will be used (only so the team "
        "can follow up). Ask for one thing at a time, in this order: name, then email or phone. Read what you heard back to "
        "confirm. Never invent details. Once they have agreed and given you their name and an email or phone, call capture_lead "
        "with consent=true (this is the exception to the rule about not calling tools unprompted). After it succeeds, thank them briefly."),
    "book_meeting": ("Booking: if the visitor wants a meeting or appointment, collect name, email and their preferred day/time one "
                     "at a time, then call book_meeting. Say the team will confirm by email; never claim it is already booked."),
    "send_notification": "If something is urgent or you cannot help, you may call send_notification so a human sees it.",
}


def prompt_addendum(tool_names: set[str]) -> str:
    parts = [ADDENDA[n] for n in ("capture_lead", "book_meeting", "send_notification") if n in tool_names]
    return "\n\n" + "\n\n".join(parts) if parts else ""


def _row(persona_id: str, account_id: str, spec: dict, url: str, secret_enc: str = "") -> PersonaTool:
    return PersonaTool(persona_id=persona_id, account_id=account_id, name=spec["name"], description=spec["description"],
                       parameters=json.dumps(spec["parameters"]), webhook_url=url, secret_enc=secret_enc, timeout_s=10.0)


def tools_for(s: DB, persona_id: str, account_id: str) -> list[PersonaTool]:
    from . import leads

    out: list[PersonaTool] = []
    cfg = leads.config_for(s, persona_id)
    if cfg is not None and cfg.enabled:
        out.append(_row(persona_id, account_id, CAPTURE_LEAD, INTERNAL + "capture_lead"))
    integ = s.get(PersonaIntegration, persona_id)
    if integ is not None:
        if integ.booking_url:
            out.append(_row(persona_id, account_id, BOOK_MEETING, integ.booking_url, integ.booking_secret_enc))
        if integ.notify_url:
            out.append(_row(persona_id, account_id, SEND_NOTIFICATION, integ.notify_url, integ.notify_secret_enc))
    return out
