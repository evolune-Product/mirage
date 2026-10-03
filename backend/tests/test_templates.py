"""Persona templates library."""
import json

import pytest
from sqlmodel import Session, select

from app import db, templates as T
from app.models_features import PersonaConfig
from app.models_leads import LeadCaptureConfig
from app.models_templates import TemplateInstance

from .tpl_helpers import client, make_persona, signup  # noqa: F401

IDS = ["b2b-saas-sales-demo", "clinic-patient-intake", "customer-support", "hospitality-concierge", "hr-onboarding-buddy",
       "interview-practice-coach", "online-tutor", "real-estate-lead-qualifier"]


def test_list_and_detail(client):
    h = signup(client)
    r = client.get("/v1/templates", headers=h).json()
    assert [t["id"] for t in r["templates"]] == IDS
    assert {"sales", "support", "healthcare", "education", "real-estate", "hr", "hospitality", "career"} == set(r["niches"])
    for t in r["templates"]:
        assert t["lead_fields"] and "name" in t["lead_fields"] and t["tools"] == ["capture_lead"] and t["knowledge_docs"] >= 1
    assert [t["id"] for t in client.get("/v1/templates?niche=hr", headers=h).json()["templates"]] == ["hr-onboarding-buddy"]
    d = client.get("/v1/templates/clinic-patient-intake", headers=h).json()
    assert "diagnose" in d["system_prompt"] and d["guardrails"] and d["variables"]["emergency_number"]
    assert d["safety_notes"] and d["knowledge"][0]["title"].startswith("SAMPLE")
    assert client.get("/v1/templates/nope", headers=h).status_code == 404
    assert client.get("/v1/templates").status_code == 422  # api key required


@pytest.mark.parametrize("tid", IDS)
def test_every_template_is_well_formed(tid):
    t = T.get(tid)
    T.validate(t)
    assert t["greeting"] and len(t["objectives"]) >= 2 and len(t["guardrails"]) >= 3
    assert any(g["name"] in ("contact-details-privacy", "privacy-disclosure") for g in t["guardrails"]), "data-use disclosure guardrail"
    assert t["sample_questions"] and t["probes"]
    for d in t["knowledge_docs"]:  # clearly fake data
        assert d["title"].startswith("SAMPLE") and "FICTIONAL" in d["text"]
    assert any("example.com" in d["text"] and "555-01" in d["text"] for d in t["knowledge_docs"]), "fake contact data"
    prompt = t["system_prompt"].lower()
    assert "ai" in prompt  # discloses it is an AI
    assert T.fill(t["system_prompt"], t["variables"]).count("{{") == 0, "unfilled placeholders in prompt"
    assert T.fill(t["greeting"], t["variables"]).count("{{") == 0


@pytest.mark.parametrize("tid", IDS)
def test_instantiate_creates_working_persona(client, tid):
    h = signup(client)
    r = make_persona(client, h, tid)
    pid = r["persona_id"]
    assert r["tools"] == ["capture_lead"] and r["lead_capture"]["enabled"] and r["warnings"]
    assert r["persona"]["system_prompt"].count("{{") == 0 and r["config"]["greeting"].count("{{") == 0
    assert len(r["knowledge_docs"]) == len(T.get(tid)["knowledge_docs"]) and all(d["chunks"] >= 1 for d in r["knowledge_docs"])
    assert any(p["id"] == pid for p in client.get("/v1/personas", headers=h).json())
    cfg = client.get(f"/v1/personas/{pid}/config", headers=h).json()
    assert cfg["greeting"] and cfg["objectives"] and cfg["guardrails"] and cfg["language"] == T.get(tid)["language"]
    docs = client.get(f"/v1/personas/{pid}/knowledge", headers=h).json()
    assert docs and all(d["title"].startswith("SAMPLE") for d in docs)
    hits = client.post(f"/v1/personas/{pid}/knowledge/search", json={"query": T.get(tid)["sample_questions"][0]["q"], "k": 2}, headers=h).json()
    assert hits
    lc = client.get(f"/v1/personas/{pid}/lead-capture", headers=h).json()
    assert lc["enabled"] and lc["tool"] == "capture_lead"
    with Session(db.engine) as s:
        assert s.get(TemplateInstance, pid).template_id == tid


def test_variables_language_llm_and_options(client):
    h = signup(client)
    r = make_persona(client, h, "b2b-saas-sales-demo", name="Zed", variables={"company_name": "Zenith", "agent_name": "Zoe"},
                     language="hi", llm="ollama/llama3.2:3b", include_sample_knowledge=False, enable_lead_capture=False)
    assert r["persona"]["name"] == "Zed" and "Zoe" in r["persona"]["system_prompt"] and "Zenith" in r["persona"]["system_prompt"]
    assert r["persona"]["llm"] == "ollama/llama3.2:3b" and r["config"]["language"] == "hi"
    assert r["knowledge_docs"] == [] and r["tools"] == [] and not r["lead_capture"]["enabled"]
    pid = r["persona_id"]
    assert client.get(f"/v1/personas/{pid}/knowledge", headers=h).json() == []
    assert client.get(f"/v1/personas/{pid}/lead-capture", headers=h).json()["enabled"] is False
    cfg = client.get(f"/v1/personas/{pid}/config", headers=h).json()
    assert "Zenith" in cfg["greeting"] and all("{{" not in g["rule"] for g in cfg["guardrails"])


def test_instantiate_validation_and_isolation(client):
    h, h2 = signup(client), signup(client, "b@b.com")
    u = "/v1/templates/customer-support/instantiate"
    assert client.post(u, json={"variables": {"bogus": "x"}}, headers=h).status_code == 422
    assert client.post(u, json={"variables": {"company_name": "x" * 201}}, headers=h).status_code == 422
    assert client.post(u, json={"language": "klingon"}, headers=h).status_code == 422
    assert client.post(u, json={"replica_id": "r_nope"}, headers=h).status_code == 404
    assert client.post(u, json={"notify_webhook_url": "ftp://x"}, headers=h).status_code == 422
    assert client.post("/v1/templates/nope/instantiate", json={}, headers=h).status_code == 404
    assert client.post(u, json={}).status_code == 422
    pid = make_persona(client, h)["persona_id"]
    assert client.get(f"/v1/personas/{pid}/lead-capture", headers=h2).status_code == 404
    assert client.get("/v1/personas", headers=h2).json() == []


def test_integrations_via_instantiate(client):
    h = signup(client)
    r = make_persona(client, h, "b2b-saas-sales-demo", booking_webhook_url="https://hooks.example.com/book", booking_secret="s3",
                     notify_webhook_url="https://hooks.example.com/notify")
    assert r["tools"] == ["capture_lead", "book_meeting", "send_notification"]
    i = client.get(f"/v1/personas/{r['persona_id']}/integrations", headers=h).json()
    assert i["booking"]["enabled"] and i["booking"]["has_secret"] and not i["notify"]["has_secret"]
    assert "s3" not in json.dumps(i)
