"""Embed widget: settings API, public script/frame, allowed-domain enforcement, booking/notify integrations."""
import json

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app import db, convo_runtime as cr, llm_backends as lb, webhooks, widgets
from app.models_features import ConversationMeta

from .tpl_helpers import client, make_persona, new_conversation, signup  # noqa: F401


def mk(c, **body):
    h = signup(c)
    pid = make_persona(c, h)["persona_id"]
    r = c.post(f"/v1/personas/{pid}/widget", json=body, headers=h)
    assert r.status_code == 200, r.text
    return h, pid, r.json()


def test_create_widget_snippet_and_crud(client):
    h, pid, w = mk(client, label="Ask Maya", color="#112233", position="bottom-left", greeting="Hi \"there\"", language="hi",
                   allowed_domains=["Example.com", "*.shop.example.com", "localhost:8000"])
    assert w["token"].startswith("sh_") and w["allowed_domains"] == ["example.com", "*.shop.example.com", "localhost:8000"]
    assert f'data-token="{w["token"]}"' in w["snippet"] and 'data-label="Ask Maya"' in w["snippet"] and "&quot;" in w["snippet"]
    assert w["snippet"].startswith("<script src=") and w["snippet"].endswith("async></script>") and "mk_" not in w["snippet"]
    assert w["frame_url"].endswith(f"/widget/frame/{w['token']}") and w["limits"]["max_seconds"] == 300 and w["language"] == "hi"
    assert client.get("/v1/widgets", headers=h).json()[0]["token"] == w["token"]
    up = client.put(f"/v1/widgets/{w['token']}", json={"label": "Hello", "allowed_domains": []}, headers=h).json()
    assert up["label"] == "Hello" and up["allowed_domains"] == [] and up["color"] == "#112233"
    assert client.get(f"/v1/widgets/{w['token']}", headers=h).json()["label"] == "Hello"
    assert client.delete(f"/v1/widgets/{w['token']}", headers=h).json() == {"deleted": w["token"]}
    assert client.get(f"/v1/widgets/{w['token']}", headers=h).status_code == 404
    assert client.get(f"/v1/guest/{w['token']}/info").status_code == 404  # revoked at once


def test_widget_validation_and_isolation(client):
    h = signup(client)
    pid = make_persona(client, h)["persona_id"]
    u = f"/v1/personas/{pid}/widget"
    for bad in ({"color": "red"}, {"position": "top"}, {"label": ""}, {"greeting": "x" * 141}, {"language": "klingon"},
                {"allowed_domains": ["*"]}, {"allowed_domains": ["a b.com"]}, {"allowed_domains": ["http://x.com/path"]},
                {"allowed_domains": ["x.com"] * 51 + [f"d{i}.com" for i in range(60)]}):
        assert client.post(u, json=bad, headers=h).status_code == 422, bad
    assert client.get("/v1/widgets", headers=h).json() == []  # nothing stored by failed creates
    w = client.post(u, json={}, headers=h).json()
    h2 = signup(client, "o@b.com")
    assert client.post(f"/v1/personas/{pid}/widget", json={}, headers=h2).status_code == 404
    assert client.get(f"/v1/widgets/{w['token']}", headers=h2).status_code == 404
    assert client.put(f"/v1/widgets/{w['token']}", json={"label": "x"}, headers=h2).status_code == 404
    assert client.delete(f"/v1/widgets/{w['token']}", headers=h2).status_code == 404
    assert client.get("/v1/widgets", headers=h2).json() == []


def test_put_attaches_settings_to_existing_share_token(client):
    h = signup(client)
    pid = make_persona(client, h)["persona_id"]
    tok = client.post(f"/v1/personas/{pid}/share", json={}, headers=h).json()["token"]
    assert client.get(f"/v1/widgets/{tok}", headers=h).status_code == 404
    w = client.put(f"/v1/widgets/{tok}", json={"allowed_domains": ["example.com"]}, headers=h).json()
    assert w["allowed_domains"] == ["example.com"] and w["label"] == "Talk to us"


def test_script_and_frame_are_public_and_frameable(client):
    _, _, w = mk(client, allowed_domains=["example.com"], color="#ff0000", language="es")
    js = client.get("/widget.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"] and "data-token" in js.text and "max-age" in js.headers["cache-control"]
    assert len(js.content) < 9000, "keep the bundle small"
    f = client.get(f"/widget/frame/{w['token']}")
    assert f.status_code == 200 and "x-frame-options" not in f.headers
    csp = f.headers["content-security-policy"]
    assert csp.startswith("frame-ancestors 'self'") and "https://example.com:*" in csp and "http://example.com:*" in csp
    assert '"lang": "es"' in f.text and "--mw:#ff0000" in f.text and "VocalFaceClient" in f.text
    assert client.get(f"/widget/frame/{w['token']}?lang=fr").text.count('"lang": "fr"') == 1
    assert client.get(f"/widget/frame/{w['token']}?lang=zz").text.count('"lang": "es"') == 1  # bad override ignored
    # other pages keep clickjacking protection
    assert client.get(f"/guest/{w['token']}").headers["x-frame-options"] == "DENY"
    assert client.get("/widget/frame/sh_unknown").status_code == 404 and client.get("/widget/frame/nope").status_code == 404
    # referer from a site that is not allowed is refused up front (frame-ancestors is the browser-side gate)
    assert client.get(f"/widget/frame/{w['token']}", headers={"referer": "https://evil.com/p"}).status_code == 403
    assert client.get(f"/widget/frame/{w['token']}", headers={"referer": "https://www.example.com/p"}).status_code == 403  # apex only
    assert client.get(f"/widget/frame/{w['token']}", headers={"referer": "https://example.com/p"}).status_code == 200


def test_open_widget_allows_any_site(client):
    _, _, w = mk(client)
    assert client.get(f"/widget/frame/{w['token']}").headers["content-security-policy"].startswith("frame-ancestors *")
    r = client.post(f"/v1/guest/{w['token']}/conversations", headers={"origin": "https://anything.example"})
    assert r.status_code == 200


@pytest.mark.parametrize("patterns,origin,ok", [
    (["example.com"], "https://example.com", True), (["example.com"], "http://example.com:8080", True),
    (["example.com"], "https://www.example.com", False), (["*.example.com"], "https://www.example.com", True),
    (["*.example.com"], "https://example.com", False), (["*.example.com"], "https://evil-example.com", False),
    (["example.com"], "https://example.com.evil.com", False), (["localhost:8000"], "http://localhost:8000", True),
    (["localhost:8000"], "http://localhost:9000", False), (["https://example.com"], "http://example.com", False),
    (["example.com:*"], "https://example.com:444", True), (["example.com"], "null", False), (["example.com"], "", False),
    (["example.com"], "file://example.com", False),
])
def test_origin_matching(patterns, origin, ok):
    assert widgets.origin_matches(patterns, origin) is ok


def test_guest_api_enforces_allowed_domains(client):
    _, _, w = mk(client, allowed_domains=["example.com", "localhost:8000"])
    t = w["token"]
    start = lambda **hd: client.post(f"/v1/guest/{t}/conversations", headers=hd)  # noqa: E731
    assert start(origin="https://example.com").status_code == 200
    assert start(origin="http://localhost:8000").status_code == 200
    assert start(origin="https://evil.com").status_code == 403
    assert start().status_code == 403  # no Origin header at all
    assert start(origin="http://testserver").status_code == 200  # the API's own origin (share page / iframe page)
    # the info endpoint stays public (it only returns the persona name)
    assert client.get(f"/v1/guest/{t}/info", headers={"origin": "https://evil.com"}).status_code == 200
    # websocket
    from starlette.websockets import WebSocketDisconnect

    cid = start(origin="https://example.com").json()["conversation_id"]
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/v1/guest/{t}/stream?cid={cid}", headers={"origin": "https://evil.com"}):
            pass
    assert e.value.code == 4403


def test_guest_start_language_body(client):
    _, _, w = mk(client)
    t = w["token"]
    r = client.post(f"/v1/guest/{t}/conversations", json={"language": "hi"})
    assert r.status_code == 200
    with Session(db.engine) as s:
        assert s.get(ConversationMeta, r.json()["conversation_id"]).language == "hi"
    assert client.post(f"/v1/guest/{t}/conversations", json={"language": "zz"}).status_code == 422
    r = client.post(f"/v1/guest/{t}/conversations")  # old clients: no body
    assert r.status_code == 200
    with Session(db.engine) as s:
        assert s.get(ConversationMeta, r.json()["conversation_id"]).language is None


def test_widget_js_and_sdk_copy_in_sync():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    a = (root / "backend/app/templates/widget/vocalface-widget.js").read_text()
    b = (root / "sdk/embed/vocalface-widget.js").read_text()
    assert a == b


# ---------------- booking / notify integrations ----------------


def test_integrations_api_and_tools(client):
    h = signup(client)
    pid = make_persona(client, h, "b2b-saas-sales-demo")["persona_id"]
    i = client.get(f"/v1/personas/{pid}/integrations", headers=h).json()
    assert not i["booking"]["enabled"] and not i["notify"]["enabled"]
    r = client.put(f"/v1/personas/{pid}/integrations", json={"booking_webhook_url": "https://hooks.zapier.com/hooks/catch/1/x/",
                                                             "booking_secret": "whsec_b"}, headers=h).json()
    assert r["booking"] == {"enabled": True, "webhook_url": "https://hooks.zapier.com/hooks/catch/1/x/", "has_secret": True, "tool": "book_meeting"}
    assert "whsec_b" not in json.dumps(r)
    assert client.put(f"/v1/personas/{pid}/integrations", json={"notify_webhook_url": "ftp://x"}, headers=h).status_code == 422
    cid = new_conversation(client, h, pid)
    with Session(db.engine) as s:
        rt = cr.ConversationRuntime.build(s, s.get(db.Conversation, cid), s.get(db.Persona, pid))
    assert [t.name for t in rt.tools] == ["capture_lead", "book_meeting"]
    assert "Booking:" in rt.system_prompt("x") and "never claim it is already booked" in rt.system_prompt("x")
    book = rt.tools[1]
    assert book.secret == "whsec_b" and "preferred_time" in book.parameters["required"]
    # the tool POSTs the documented payload, signed
    sent = {}
    lb.set_tool_post(lambda url, body, headers, timeout: sent.update(url=url, body=body, headers=headers) or (200, '{"message":"queued"}'))
    ok, out = lb.execute_tool(book, {"name": "A", "email": "a@b.co", "preferred_time": "Tue 3pm"}, {"conversation_id": cid, "persona_id": pid})
    assert ok and sent["url"].startswith("https://hooks.zapier.com")
    payload = json.loads(sent["body"])
    assert payload["tool"] == "book_meeting" and payload["arguments"]["preferred_time"] == "Tue 3pm" and payload["conversation_id"] == cid
    assert webhooks.verify("whsec_b", sent["body"], sent["headers"]["VocalFace-Signature"])
    # test endpoint
    t = client.post(f"/v1/personas/{pid}/integrations/test", json={"which": "booking"}, headers=h).json()
    assert t["ok"] and json.loads(sent["body"])["arguments"]["test"] is True
    assert client.post(f"/v1/personas/{pid}/integrations/test", json={"which": "notify"}, headers=h).status_code == 409
    assert client.post(f"/v1/personas/{pid}/integrations/test", json={"which": "x"}, headers=h).status_code == 422
    # clearing disables the tool
    client.put(f"/v1/personas/{pid}/integrations", json={"booking_webhook_url": "", "booking_secret": ""}, headers=h)
    with Session(db.engine) as s:
        rt = cr.ConversationRuntime.build(s, s.get(db.Conversation, cid), s.get(db.Persona, pid))
    assert [t.name for t in rt.tools] == ["capture_lead"]


def test_notify_tool_failure_is_reported(client):
    h = signup(client)
    pid = make_persona(client, h)["persona_id"]
    client.put(f"/v1/personas/{pid}/integrations", json={"notify_webhook_url": "https://n.example.com/h"}, headers=h)
    lb.set_tool_post(lambda *a: (500, "boom"))
    assert client.post(f"/v1/personas/{pid}/integrations/test", json={"which": "notify"}, headers=h).json()["ok"] is False


def test_python_sdk_methods(client):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))
    from vocalface_sdk import VocalFace

    m = VocalFace(http_client=client)
    m.signup("sdk2@x.com")
    assert len(m.list_templates()["templates"]) == 8 and m.get_template("online-tutor")["language"] == "auto"
    r = m.instantiate_template("customer-support", variables={"company_name": "Zed"})
    pid = r["persona_id"]
    assert m.get_lead_capture(pid)["enabled"] and m.set_lead_capture(pid, required_fields=["name", "email"])["required_fields"] == ["name", "email"]
    ld = m.create_lead(pid, name="Ann", email="ann@x.co")
    assert m.list_leads(q="ann")["total"] == 1 and m.get_lead(ld["id"])["name"] == "Ann" and "ann@x.co" in m.export_leads_csv()
    assert m.delete_lead(ld["id"])["deleted"] == ld["id"]
    w = m.create_widget(pid, allowed_domains=["example.com"])
    assert m.list_widgets(pid)[0]["token"] == w["token"] and m.update_widget(w["token"], label="Hi")["label"] == "Hi"
    assert m.set_integrations(pid, notify_webhook_url="https://n.example.com/h")["notify"]["enabled"]
    assert m.get_integrations(pid)["notify"]["enabled"] and m.delete_widget(w["token"])["deleted"] == w["token"]
