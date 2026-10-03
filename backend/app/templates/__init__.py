"""Persona templates library: ready-made niche agents (prompt, greeting, objectives, guardrails, sample knowledge).

Each template is a plain dict in library/<module>.py (`TEMPLATE`). Sample knowledge documents describe a FICTIONAL company
with obviously fake data (example.com emails, 555-01xx phones); the user must replace them with their own documents.
"""
from __future__ import annotations

import importlib
import pkgutil
import re

from . import library

VERSION = 1
LEAD_FIELDS = ("name", "email", "phone", "company", "interest", "notes")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,48}$")
REQUIRED = ("id", "name", "niche", "summary", "system_prompt", "greeting", "objectives", "guardrails", "knowledge_docs",
            "variables", "sample_questions")
_cache: dict[str, dict] = {}


def validate(t: dict) -> None:
    missing = [k for k in REQUIRED if k not in t]
    if missing:
        raise ValueError(f"template {t.get('id')}: missing {missing}")
    if not _ID.match(t["id"]):
        raise ValueError(f"bad template id {t['id']}")
    for o in t["objectives"]:
        for v in o.get("output_variables", []):
            if v not in LEAD_FIELDS and not v.startswith("x_"):
                raise ValueError(f"{t['id']}: objective variable {v} is not a lead field (use x_ prefix for custom)")
    if not any(o.get("lead_fields") or any(v in LEAD_FIELDS for v in o.get("output_variables", [])) for o in t["objectives"]):
        raise ValueError(f"{t['id']}: needs an objective that maps to lead capture fields")
    for d in t["knowledge_docs"]:
        if "SAMPLE" not in d["title"] or "FICTIONAL" not in d["text"]:
            raise ValueError(f"{t['id']}: sample docs must be clearly marked SAMPLE / FICTIONAL")


def load() -> dict[str, dict]:
    if not _cache:
        for m in pkgutil.iter_modules(library.__path__):
            t = importlib.import_module(f"{__name__}.library.{m.name}").TEMPLATE
            validate(t)
            t.setdefault("version", VERSION)
            t.setdefault("language", "en")
            t.setdefault("suggested_llm", "ollama/qwen3:8b")
            t.setdefault("tools", ["capture_lead"])
            _cache[t["id"]] = t
    return _cache


def get(tid: str) -> dict | None:
    return load().get(tid)


def lead_fields(t: dict) -> list[str]:
    out: list[str] = []
    for o in t["objectives"]:
        for v in o.get("output_variables", []):
            if v in LEAD_FIELDS and v not in out:
                out.append(v)
    return out


def summary(t: dict) -> dict:
    return {"id": t["id"], "name": t["name"], "niche": t["niche"], "summary": t["summary"], "version": t["version"],
            "language": t["language"], "suggested_llm": t["suggested_llm"], "tools": t["tools"],
            "objectives": [o["name"] for o in t["objectives"]], "lead_fields": lead_fields(t),
            "knowledge_docs": len(t["knowledge_docs"]), "safety_notes": t.get("safety_notes", "")}


def detail(t: dict) -> dict:
    return {**summary(t), "persona_name": t.get("persona_name", t["name"]), "system_prompt": t["system_prompt"],
            "greeting": t["greeting"], "objectives": t["objectives"], "guardrails": t["guardrails"],
            "guardrail_fallback": t.get("guardrail_fallback", ""), "variables": t["variables"],
            "knowledge": [{"title": d["title"], "chars": len(d["text"]), "text": d["text"]} for d in t["knowledge_docs"]],
            "sample_questions": t["sample_questions"], "recommended_lead_required_fields": t.get("lead_required", ["name"])}


_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def fill(text: str, variables: dict) -> str:
    """Replace {{name}} placeholders that the caller defined; other placeholders (runtime variables such as
    {{first_name}}) are left untouched for the conversation to fill in."""
    return _VAR.sub(lambda m: str(variables[m.group(1)]) if m.group(1) in variables else m.group(0), text or "")
