"""Lead capture: tool, validation, API, CSV, webhooks, analytics, deletion, builtin tool wiring."""
import asyncio
import csv
import io
import json

import pytest
from sqlmodel import Session, select

from app import builtin_tools, convo_runtime as cr, db, leads, llm_backends as lb, webhooks
from app.models_features import ConversationMeta, ObjectiveProgress, WebhookDelivery
from app.models_leads import Lead

from .tpl_helpers import client, make_persona, new_conversation, signup  # noqa: F401


def H(h):
    return h


def setup(c, tid="customer-support"):
    h = signup(c)
    pid = make_persona(c, h, tid)["persona_id"]
    return h, pid, new_conversation(c, h, pid)


def call(cid, pid, **args):
    return leads.run_internal("capture_lead", args, {"conversation_id": cid, "persona_id": pid})


GOOD = dict(name="Ravi Kumar", email="Ravi@Example.com", phone="+91 98765-43210", company="Zenith", interest="demo", consent=True)


def test_capture_validates_and_stores(client):
    h, pid, cid = setup(client)
    ok, out = call(cid, pid, **GOOD)
    assert ok and json.loads(out)["saved"]
    l = client.get("/v1/leads", headers=h).json()["items"][0]
    assert l["email"] == "ravi@example.com" and l["phone"] == "+919876543210" and l["consent"] and l["conversation_id"] == cid
    assert l["persona_id"] == pid and l["source"] == "tool" and l["consent_text"]
    # second call merges into the same lead (one per conversation)
    ok, out = call(cid, pid, notes="wants SSO", consent=True)
    assert ok and "updated" in json.loads(out)["message"]
    items = client.get("/v1/leads", headers=h).json()
    assert items["total"] == 1 and items["items"][0]["notes"] == "wants SSO" and items["items"][0]["name"] == "Ravi Kumar"


@pytest.mark.parametrize("args,frag", [
    (dict(name="A", email="not-an-email", consent=True), "email"),
    (dict(name="A", phone="12", consent=True), "phone"),
    (dict(name="A", phone="call me maybe", consent=True), "phone"),
    (dict(name="A", consent=True), "email or a phone"),
    (dict(email="a@b.co", consent=True), "name"),
    (dict(name="a@b.co", email="a@b.co", consent=True), "name looks like"),
    (dict(name="A", email="a@b.co"), "not agreed"),
    (dict(name="A", email="a@b.co", consent="no"), "not agreed"),
    (dict(name="A" * 200, email="a@b.co", consent=True), "too long"),
])
def test_capture_rejects_bad_input(client, args, frag):
    h, pid, cid = setup(client)
    ok, out = call(cid, pid, **args)
    assert not ok and frag in json.loads(out)["error"]
    assert client.get("/v1/leads", headers=h).json()["total"] == 0


def test_spoken_email_and_phone_normalisation():
    assert leads.norm_email("ravi at example dot com") == "ravi@example.com"
    assert leads.norm_phone("0044 20 7946 0958") == "+442079460958"
    assert leads.norm_phone("(555) 010-0199") == "5550100199"
    assert leads.norm_email("") == "" and leads.norm_phone("") == ""
    with pytest.raises(leads.LeadError):
        leads.norm_email("a@b")


def test_disabled_persona_and_unknown_conversation(client):
    h, pid, cid = setup(client)
    assert client.put(f"/v1/personas/{pid}/lead-capture", json={"enabled": False}, headers=h).json()["enabled"] is False
    ok, out = call(cid, pid, **GOOD)
    assert not ok and "disabled" in json.loads(out)["error"]
    assert not leads.run_internal("capture_lead", GOOD, {"conversation_id": "c_x", "persona_id": pid})[0]
    assert not leads.run_internal("other", {}, {})[0]


def test_lead_capture_config_and_required_fields(client):
    h, pid, cid = setup(client)
    r = client.put(f"/v1/personas/{pid}/lead-capture", json={"required_fields": ["name", "company"], "require_consent": False,
                                                           "disclosure": "Used only to call you back."}, headers=h).json()
    assert r["required_fields"] == ["name", "company"] and not r["require_consent"]
    assert client.put(f"/v1/personas/{pid}/lead-capture", json={"required_fields": ["bogus"]}, headers=h).status_code == 422
    ok, out = call(cid, pid, name="A", email="a@b.co")
    assert not ok and "company" in json.loads(out)["error"]
    ok, _ = call(cid, pid, name="A", email="a@b.co", company="X")  # consent not required now
    assert ok
    l = client.get("/v1/leads", headers=h).json()["items"][0]
    assert not l["consent"] and l["company"] == "X"


def test_runtime_wires_tool_and_prompt(client):
    h, pid, cid = setup(client)
    with Session(db.engine) as s:
        conv = s.get(db.Conversation, cid)
        rt = cr.ConversationRuntime.build(s, conv, s.get(db.Persona, pid))
    assert [t.name for t in rt.tools] == ["capture_lead"] and rt.tools[0].webhook_url.startswith("vocalface-internal://")
    sp = rt.system_prompt("You are X.")
    assert "Contact details (lead capture)" in sp and "never push" in sp.lower() or "Never push" in sp
    # a persona without lead capture is untouched
    p2 = client.post("/v1/personas", json={"name": "plain", "system_prompt": "x"}, headers=h).json()["id"]
    c2 = new_conversation(client, h, p2)
    with Session(db.engine) as s:
        rt2 = cr.ConversationRuntime.build(s, s.get(db.Conversation, c2), s.get(db.Persona, p2))
    assert rt2.tools == [] and "lead capture" not in rt2.system_prompt("x")
    # a persona tool of the same name wins
    client.post(f"/v1/personas/{pid}/tools", json={"name": "capture_lead", "description": "mine", "webhook_url": "https://x.example.com/t"}, headers=h)
    with Session(db.engine) as s:
        rt3 = cr.ConversationRuntime.build(s, s.get(db.Conversation, cid), s.get(db.Persona, pid))
    assert [t.webhook_url for t in rt3.tools] == ["https://x.example.com/t"]


def test_llm_tool_call_end_to_end_through_featurellm(client):
    """The model calls capture_lead; the in-process executor stores the lead and returns a message for the model."""
    h, pid, cid = setup(client)
    with Session(db.engine) as s:
        rt = cr.ConversationRuntime.build(s, s.get(db.Conversation, cid), s.get(db.Persona, pid))
    seen = {}

    class Be:
        n = 0

        async def chat(self, messages, tools=None):
            Be.n += 1
            if Be.n == 1:
                seen["tools"] = [t["function"]["name"] for t in tools]
                yield "tool_call", {"id": "1", "name": "capture_lead", "arguments": dict(GOOD)}
            else:
                seen["result"] = [m for m in messages if m.get("role") == "tool"][-1]["content"]
                yield "text", "Thanks Ravi, the team will be in touch."

        assistant_tool_msg = staticmethod(lb.OllamaBackend.assistant_tool_msg)
        tool_result_msg = staticmethod(lb.OllamaBackend.tool_result_msg)

    logged = []
    llm = lb.FeatureLLM(Be(), rt.tools, {"conversation_id": cid, "persona_id": pid}, logged.append)

    async def go():
        return "".join([x async for x in llm.stream("s", [], "yes please call me")])

    assert "Thanks Ravi" in asyncio.run(go())
    assert seen["tools"] == ["capture_lead"] and json.loads(seen["result"])["saved"] is True
    assert logged[0]["ok"] and client.get("/v1/leads", headers=h).json()["total"] == 1


def test_webhook_events(client):
    h, pid, cid = setup(client)
    client.post("/v1/webhooks", json={"url": "https://hooks.example.com/x", "events": ["lead.captured", "lead.updated"]}, headers=h)
    assert "lead.captured" in client.get("/v1/webhooks/events", headers=h).json()["events"]
    call(cid, pid, **GOOD)
    call(cid, pid, notes="more", consent=True)
    with Session(db.engine) as s:
        evs = [(d.event, json.loads(d.payload)) for d in s.exec(select(WebhookDelivery)).all()] if hasattr(WebhookDelivery, "payload") else []
    types = [e for e, _ in evs]
    assert types.count("lead.captured") == 1 and types.count("lead.updated") == 1
    data = [p for e, p in evs if e == "lead.captured"][0]["data"]
    assert data["lead"]["email"] == "ravi@example.com" and data["conversation_id"] == cid and data["persona_id"] == pid


def test_list_filters_search_pagination(client):
    h, pid, _ = setup(client)
    p2 = make_persona(client, h, "hr-onboarding-buddy")["persona_id"]
    for i, (p, name, email, comp) in enumerate([(pid, "Alice Smith", "alice@x.com", "Globex"), (pid, "Bob Jones", "bob@y.com", "Initech"),
                                                (p2, "Carol White", "carol@z.com", "Globex")]):
        c = new_conversation(client, h, p)
        assert call(c, p, name=name, email=email, company=comp, consent=True)[0]
    L = lambda **q: client.get("/v1/leads", params=q, headers=h).json()  # noqa: E731
    assert L()["total"] == 3 and L(persona_id=p2)["total"] == 1
    assert [x["name"] for x in L(q="globex")["items"]] == ["Carol White", "Alice Smith"]  # newest first
    assert L(q="bob@y")["total"] == 1 and L(q="nothing")["total"] == 0
    assert L(limit=2)["items"].__len__() == 2 and L(limit=2, offset=2)["items"].__len__() == 1 and L(limit=2)["total"] == 3
    assert L(since="2999-01-01")["total"] == 0 and L(until="2000-01-01")["total"] == 0
    assert L(since="2020-01-01", until="2999-12-31")["total"] == 3
    assert L(consent="true")["total"] == 3 and L(consent="false")["total"] == 0
    assert client.get("/v1/leads?since=garbage", headers=h).status_code == 422
    assert L(limit=100000)["limit"] == 200


def test_csv_export_and_injection_guard(client):
    h, pid, cid = setup(client)
    call(cid, pid, name="=HYPERLINK(\"http://evil\")", email="a@b.co", phone="+1 555 010 0199", notes="@cmd, \"quoted\"", consent=True)
    r = client.get("/v1/leads/export.csv", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 1 and rows[0]["name"].startswith("'=") and rows[0]["notes"].startswith("'@") and rows[0]["phone"] == "+15550100199"
    assert rows[0]["consent"] == "yes" and rows[0]["persona_name"] == "Sam (support)" and rows[0]["conversation_id"] == cid
    assert list(rows[0])[:3] == ["id", "created_at", "persona_id"]
    assert len(list(csv.DictReader(io.StringIO(client.get("/v1/leads/export.csv?q=zzz", headers=h).text)))) == 0


def test_get_delete_isolation_and_manual_create(client):
    h, pid, cid = setup(client)
    h2 = signup(client, "other@b.com")
    call(cid, pid, **GOOD)
    lid = client.get("/v1/leads", headers=h).json()["items"][0]["id"]
    assert client.get(f"/v1/leads/{lid}", headers=h).json()["id"] == lid
    assert client.get(f"/v1/leads/{lid}", headers=h2).status_code == 404 and client.delete(f"/v1/leads/{lid}", headers=h2).status_code == 404
    assert client.get("/v1/leads", headers=h2).json()["total"] == 0 and "ravi" not in client.get("/v1/leads/export.csv", headers=h2).text
    m = client.post("/v1/leads", json={"persona_id": pid, "name": "Manual", "email": "m@x.co"}, headers=h)
    assert m.status_code == 200 and m.json()["source"] == "api" and m.json()["consent"] is True
    assert client.post("/v1/leads", json={"persona_id": pid, "name": "M", "email": "bad"}, headers=h).status_code == 422
    assert client.post("/v1/leads", json={"persona_id": "p_x", "name": "M", "email": "m@x.co"}, headers=h).status_code == 404
    assert client.post("/v1/leads", json={"persona_id": pid, "conversation_id": "c_x", "name": "M", "email": "m@x.co"}, headers=h).status_code == 404
    assert client.get("/v1/leads", headers=h).json()["total"] == 2
    assert client.delete(f"/v1/leads/{lid}", headers=h).json() == {"deleted": lid}
    assert client.get(f"/v1/leads/{lid}", headers=h).status_code == 404 and client.get("/v1/leads", headers=h).json()["total"] == 1


def test_account_data_deletion_removes_leads(client):
    h, pid, cid = setup(client)
    call(cid, pid, **GOOD)
    h2 = signup(client, "keep@b.com")
    pid2 = make_persona(client, h2)["persona_id"]
    call(new_conversation(client, h2, pid2), pid2, **GOOD)
    r = client.post("/v1/account/delete-my-data", json={"confirm": "delete-my-data"}, headers=h)
    assert r.status_code == 200, r.text
    with Session(db.engine) as s:
        rows = s.exec(select(Lead)).all()
    assert [l.persona_id for l in rows] == [pid2]  # only the other account's lead survives


def test_analytics_leads_and_objective_rates(client):
    h, pid, cid = setup(client)
    c2 = new_conversation(client, h, pid)
    call(cid, pid, **GOOD)
    with Session(db.engine) as s:
        s.add(ObjectiveProgress(conversation_id=cid, name="resolve_issue", completed=True)); s.commit()
    a = client.get("/v1/analytics?days=7", headers=h).json()
    assert a["leads"]["total"] == 1 and a["leads"]["per_persona"][0] == {"persona_id": pid, "name": "Sam (support)", "leads": 1,
                                                                        "conversations": 2, "conversion_rate": 0.5}
    o = {x["objective"]: x for x in a["objectives"]["per_persona"]}
    assert o["resolve_issue"]["completed"] == 1 and o["resolve_issue"]["completion_rate"] == 0.5
    assert o["capture_escalation_contact"]["completed"] == 0 and a["totals"]["conversations"] == 2  # old keys intact


def test_sweep_and_objective_fallback(client):
    h, pid, cid = setup(client)
    from app.models_features import TranscriptTurn

    with Session(db.engine) as s:
        for i, (r, tx) in enumerate([("user", "please call me back"), ("assistant", "Name and phone?"), ("user", "Ravi, 9876543210, yes OK")]):
            s.add(TranscriptTurn(conversation_id=cid, seq=i, role=r, text=tx))
        s.commit()
    with Session(db.engine) as s:
        rt = cr.ConversationRuntime.build(s, s.get(db.Conversation, cid), s.get(db.Persona, pid))

    async def fake(system, prompt):
        return '{"name":"Ravi","email":"","phone":"9876543210","company":"","interest":"callback","notes":"","agreed":true}'

    lb.set_completer(fake)
    l = asyncio.run(leads.sweep(rt))
    assert l and l.source == "sweep" and l.consent and l.phone == "9876543210"
    assert asyncio.run(leads.sweep(rt)) is None  # already stored

    async def no(system, prompt):
        return '{"name":"Ravi","phone":"9876543210","agreed":false}'

    lb.set_completer(no)
    c2 = new_conversation(client, h, pid)
    rt.cid = c2
    with Session(db.engine) as s:
        s.add(TranscriptTurn(conversation_id=c2, seq=0, role="user", text="x"))
        s.commit()
    assert asyncio.run(leads.sweep(rt)) is None  # no agreement -> nothing stored
    c3 = new_conversation(client, h, pid)
    leads.on_objective_completed(l.account_id, c3, pid, {"name": "A", "email": "bad"})
    assert client.get(f"/v1/leads?conversation_id={c3}", headers=h).json()["total"] == 0
    with Session(db.engine) as s:  # the visitor really said these words; the judge scrambled the digits
        s.add(TranscriptTurn(conversation_id=c3, seq=0, role="user", text="I am Anna Lee, number is 5 5 5 0 1 0 0 1 9 9 please"))
        s.commit()
    leads.on_objective_completed(l.account_id, c3, pid, {"name": "Anna Lee", "phone": "55501019"})
    assert client.get(f"/v1/leads?conversation_id={c3}", headers=h).json()["items"][0]["source"] == "objective"

    assert client.get(f"/v1/leads?conversation_id={c3}", headers=h).json()["items"][0]["phone"] == "5550100199"


def test_ground_drops_values_the_visitor_did_not_say():
    said = "My name is Ravi Kumar and my number is +91 98765 43210, mail ravi at example dot com"
    g = leads.ground({"name": "Ravi Kumar", "email": "ravi@example.com", "phone": "917653210"}, said)
    assert g == {"name": "Ravi Kumar", "email": "ravi@example.com", "phone": "+919876543210"}
    g = leads.ground({"name": "Bob Stone", "email": "x@y.com", "phone": "123"}, "hello there")
    assert g == {"name": "", "email": "", "phone": ""}
