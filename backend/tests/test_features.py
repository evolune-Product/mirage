"""Feature layer: transcripts/summary/memory, webhooks, persona config (greeting, objectives, guardrails, tools,
custom LLM), multilingual, API keys, analytics, video features, guest share links."""
import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlmodel import Session, select

from app import convo_runtime as cr, db, languages, llm_backends as lb, secretbox, webhooks
from app.models_features import (ApiKey, ConversationMeta, ConversationMetric, PersonaConfig, ShareLink, ShareSession,
                                 TranscriptTurn, WebhookDelivery)

from .test_realtime import FakeLLM, collect, env, speak  # noqa: F401


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from app import safety
    monkeypatch.setenv("VOCALFACE_RL_SIGNUP", "1000/60")  # many signups per test; the limiter is global across the suite
    monkeypatch.setenv("VOCALFACE_RL_IP", "100000/60")
    monkeypatch.setenv("VOCALFACE_RL_KEY", "100000/60")
    safety.limiter.reset()
    lb.set_completer(None)
    lb.set_tool_post(None)
    yield
    lb.set_completer(None)
    lb.set_tool_post(None)
    safety.limiter.reset()


def H(key):
    return {"x-api-key": key}


def persona_id(c, key):
    return c.get("/v1/personas", headers=H(key)).json()[0]["id"]


def run(coro):
    return asyncio.run(coro)


# ---------------- 1. transcripts, summary, memory ----------------


def test_transcript_persisted_per_turn_and_summary_memory(env):
    c, key, cid = env

    async def fake(system, prompt):
        return "The user said hello and is called Sam."

    lb.set_completer(fake)
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json()
        speak(ws)
        collect(ws, "agent_done")
    t = c.get(f"/v1/conversations/{cid}/transcript", headers=H(key)).json()
    assert [x["role"] for x in t["turns"]] == ["user", "assistant"]
    assert t["turns"][0]["text"] == "hello there"
    assert "this is a test." in t["turns"][1]["text"].lower().replace("hi, ", "") or "Hi" in t["turns"][1]["text"]
    assert t["turns"][1]["first_audio_ms"] is not None
    assert c.get(f"/v1/conversations/{cid}/transcript?format=text", headers=H(key)).text.startswith("User: hello there")
    # end -> summary saved as a persona memory (background task) + metrics + webhooks fired
    assert c.post(f"/v1/conversations/{cid}/end", headers=H(key)).status_code == 200
    assert c.get(f"/v1/conversations/{cid}/summary", headers=H(key)).json() == {
        "conversation_id": cid, "summary": "The user said hello and is called Sam.", "ready": True}
    pid = persona_id(c, key)
    mems = c.get(f"/v1/personas/{pid}/memories", headers=H(key)).json()
    assert mems and mems[0]["summary"].startswith("The user said hello")
    m = c.get(f"/v1/conversations/{cid}/metrics", headers=H(key)).json()
    assert m["user_turns"] == 1 and m["agent_turns"] == 1
    # memory is wired into the NEXT conversation's system prompt
    cid2 = c.post("/v1/conversations", json={"persona_id": pid}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{cid2}/stream?api_key={key}") as ws:
        ws.receive_json()
        speak(ws)
        collect(ws, "agent_done")
    assert "called Sam" in FakeLLM.seen[0]
    # finalize is idempotent
    assert run(cr.finalize_conversation(cid)) is False


def test_memory_scoped_by_participant_and_disabled(env):
    c, key, cid = env
    pid = persona_id(c, key)
    lb.set_completer(lambda s, p: asyncio.sleep(0, result="Alice likes tea."))

    def talk(participant):
        cc = c.post("/v1/conversations", json={"persona_id": pid, "participant_id": participant}, headers=H(key)).json()["id"]
        with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
            ws.receive_json(); speak(ws); collect(ws, "agent_done")
        c.post(f"/v1/conversations/{cc}/end", headers=H(key))
        return cc

    talk("alice")
    talk("bob")
    back = c.post("/v1/conversations", json={"persona_id": pid, "participant_id": "alice"}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{back}/stream?api_key={key}") as ws:
        ws.receive_json(); speak(ws); collect(ws, "agent_done")
    assert "Alice likes tea" in FakeLLM.seen[0]  # returning participant gets their own memory
    # a third participant sees nothing
    cc = c.post("/v1/conversations", json={"persona_id": pid, "participant_id": "carol"}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
        ws.receive_json(); speak(ws); collect(ws, "agent_done")
    assert "Alice likes tea" not in FakeLLM.seen[0]
    # memory can be switched off per persona
    c.put(f"/v1/personas/{pid}/config", json={"memory_enabled": False}, headers=H(key))
    cc = c.post("/v1/conversations", json={"persona_id": pid, "participant_id": "alice"}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
        ws.receive_json(); speak(ws); collect(ws, "agent_done")
    assert "Alice likes tea" not in FakeLLM.seen[0]


def test_conversation_variables_and_context_in_prompt(env):
    c, key, cid = env
    pid = persona_id(c, key)
    c.put(f"/v1/personas/{pid}/config", json={"guardrails": [{"rule": "Never mention {{competitor}}."}]}, headers=H(key))
    cc = c.post("/v1/conversations", json={"persona_id": pid, "context": "Talking to {{first_name}}.",
                                           "variables": {"first_name": "Priya", "competitor": "Acme"}}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
        ws.receive_json(); speak(ws); collect(ws, "agent_done")
    sysmsg = FakeLLM.seen[0]
    assert "Talking to Priya." in sysmsg and "Never mention Acme." in sysmsg
    bad = c.post("/v1/conversations", json={"persona_id": pid, "language": "klingon"}, headers=H(key))
    assert bad.status_code == 422


# ---------------- 2. webhooks ----------------


def test_webhook_crud_and_secret_not_listed(env):
    c, key, _ = env
    r = c.post("/v1/webhooks", json={"url": "http://x.test/h", "events": ["conversation.ended"]}, headers=H(key)).json()
    assert r["secret"].startswith("whsec_")
    assert "secret" not in c.get("/v1/webhooks", headers=H(key)).json()[0]
    assert c.post("/v1/webhooks", json={"url": "ftp://x"}, headers=H(key)).status_code == 422
    assert c.post("/v1/webhooks", json={"url": "http://x.test", "events": ["nope"]}, headers=H(key)).status_code == 422
    assert c.patch(f"/v1/webhooks/{r['id']}", json={"active": False}, headers=H(key)).json()["active"] is False
    s2 = c.post("/v1/webhooks/" + r["id"] + "/rotate-secret", headers=H(key)).json()["secret"]
    assert s2 != r["secret"]
    other = c.post("/v1/signup", json={"email": "o@o.o"}).json()["api_key"]
    assert c.delete(f"/v1/webhooks/{r['id']}", headers=H(other)).status_code == 404
    assert c.delete(f"/v1/webhooks/{r['id']}", headers=H(key)).status_code == 200


def test_webhook_signature_roundtrip_retry_and_log(env):
    c, key, cid = env
    wh = c.post("/v1/webhooks", json={"url": "http://x.test/h", "events": ["conversation.started", "conversation.ended"]},
                headers=H(key)).json()
    pid = persona_id(c, key)
    c.post("/v1/conversations", json={"persona_id": pid}, headers=H(key))  # -> conversation.started
    calls = []

    def flaky(url, headers, body):
        calls.append((headers, body))
        return (500, "boom") if len(calls) == 1 else (200, "ok")

    assert webhooks.deliver_due(flaky) == 1  # attempt 1 -> 500
    d = c.get(f"/v1/webhooks/{wh['id']}/deliveries", headers=H(key)).json()
    assert d[0]["status"] == "pending" and d[0]["attempts"] == 1 and d[0]["last_status_code"] == 500
    assert webhooks.deliver_due(flaky) == 0  # backoff: not due yet
    later = datetime.now(timezone.utc) + timedelta(seconds=15)
    assert webhooks.deliver_due(flaky, now=later) == 1
    d = c.get(f"/v1/webhooks/{wh['id']}/deliveries", headers=H(key)).json()
    assert d[0]["status"] == "delivered" and d[0]["attempts"] == 2
    headers, body = calls[-1]
    assert webhooks.verify(wh["secret"], body, headers["VocalFace-Signature"])
    assert not webhooks.verify("whsec_wrong", body, headers["VocalFace-Signature"])
    assert not webhooks.verify(wh["secret"], body + " ", headers["VocalFace-Signature"])
    ev = json.loads(body)
    assert ev["type"] == "conversation.started" and ev["data"]["persona_id"] == pid
    assert headers["VocalFace-Event"] == "conversation.started"


def test_webhook_gives_up_after_max_attempts_and_manual_retry(env):
    c, key, _ = env
    wh = c.post("/v1/webhooks", json={"url": "http://x.test/h"}, headers=H(key)).json()
    acc_id = c.post("/v1/signup", json={"email": "z@z.z"})  # noqa: F841 (just exercises signup)
    with Session(db.engine) as s:
        from app.db import Account
        aid = s.exec(select(Account)).first().id
    assert webhooks.emit(aid, "video.error", {"video_id": "v_1"}) == 1
    t = datetime.now(timezone.utc)
    for i in range(webhooks.MAX_ATTEMPTS + 2):
        webhooks.deliver_due(lambda u, h, b: (503, "down"), now=t + timedelta(hours=3 * (i + 1)))
    d = c.get(f"/v1/webhooks/{wh['id']}/deliveries", headers=H(key)).json()[0]
    assert d["status"] == "failed" and d["attempts"] == webhooks.MAX_ATTEMPTS
    # event filtering: an endpoint subscribed to something else gets nothing
    c.patch(f"/v1/webhooks/{wh['id']}", json={"events": ["replica.ready"]}, headers=H(key))
    assert webhooks.emit(aid, "video.error", {}) == 0
    assert webhooks.emit(aid, "replica.ready", {}) == 1


def test_webhook_fires_for_end_events_and_replica_video_events(env):
    c, key, cid = env
    c.post("/v1/webhooks", json={"url": "http://x.test/h"}, headers=H(key))
    lb.set_completer(lambda s, p: asyncio.sleep(0, result="sum"))
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json(); speak(ws); collect(ws, "agent_done")
    c.post(f"/v1/conversations/{cid}/end", headers=H(key))
    from app import events
    rid = c.post("/v1/replicas", json={"name": "r", "train_video_url": "http://x/v.mp4"}, headers=H(key)).json()["id"]
    with Session(db.engine) as s:
        r = s.get(db.Replica, rid); r.status = "ready"; s.add(r)
        v = db.Video(account_id=r.account_id, replica_id=rid, script="hi", status="ready"); s.add(v); s.commit()
        vid = v.id
    events.on_replica_finished(rid)
    events.on_video_finished(vid)
    with Session(db.engine) as s:
        types = sorted(json.loads(d.payload)["type"] for d in s.exec(select(WebhookDelivery)).all())
    assert types == sorted(["conversation.ended", "transcript.ready", "replica.ready", "video.ready"])


# ---------------- 3. persona config ----------------


def test_config_validation_and_secret_never_returned(env):
    c, key, _ = env
    pid = persona_id(c, key)
    assert c.put(f"/v1/personas/{pid}/config", json={"language": "xx"}, headers=H(key)).status_code == 422
    r = c.put(f"/v1/personas/{pid}/config", json={
        "language": "es", "greeting": "Hola {{name}}", "memory_enabled": True,
        "custom_llm": {"base_url": "http://llm.test/v1", "model": "m1", "api_key": "sk-SECRET-123"},
        "objectives": [{"name": "get_email", "description": "ask for email", "output_variables": ["email"]}]},
        headers=H(key))
    assert r.status_code == 200
    body = r.json()
    assert "sk-SECRET-123" not in json.dumps(body) and body["custom_llm"]["has_api_key"] is True
    assert "sk-SECRET-123" not in json.dumps(c.get(f"/v1/personas/{pid}/config", headers=H(key)).json())
    with Session(db.engine) as s:
        enc = s.get(PersonaConfig, pid).llm_api_key_enc
    assert enc and "sk-SECRET-123" not in enc and secretbox.decrypt(enc) == "sk-SECRET-123"
    # partial update keeps the key; duplicate objective names rejected
    c.put(f"/v1/personas/{pid}/config", json={"greeting": "Hi"}, headers=H(key))
    with Session(db.engine) as s:
        assert secretbox.decrypt(s.get(PersonaConfig, pid).llm_api_key_enc) == "sk-SECRET-123"
    dup = c.put(f"/v1/personas/{pid}/config", json={"objectives": [{"name": "a"}, {"name": "a"}]}, headers=H(key))
    assert dup.status_code == 422
    other = c.post("/v1/signup", json={"email": "o@o.o"}).json()["api_key"]
    assert c.get(f"/v1/personas/{pid}/config", headers=H(other)).status_code == 404


def test_secretbox_both_modes(monkeypatch):
    t = secretbox.encrypt("hunter2")
    assert secretbox.decrypt(t) == "hunter2" and secretbox.encrypt("") == ""
    # force the stdlib fallback
    import builtins
    real = builtins.__import__

    def no_crypto(name, *a, **k):
        if name.startswith("cryptography"):
            raise ImportError
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_crypto)
    t2 = secretbox.encrypt("hunter2")
    assert t2.startswith("h1:") and secretbox.decrypt(t2) == "hunter2"
    with pytest.raises(ValueError):
        secretbox.decrypt(t2[:-4] + "AAAA")


def test_greeting_spoken_first_and_saved(env):
    c, key, cid = env
    pid = persona_id(c, key)
    c.put(f"/v1/personas/{pid}/config", json={"greeting": "Welcome, {{first_name}}!"}, headers=H(key))
    cc = c.post("/v1/conversations", json={"persona_id": pid, "variables": {"first_name": "Ravi"}}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
        assert ws.receive_json()["type"] == "ready"
        msgs = collect(ws, "agent_done")  # no user speech at all: the agent speaks first
        speak(ws)
        msgs2 = collect(ws, "agent_done")
    assert any(m.get("bytes") for m in msgs)
    assert any("Welcome, Ravi!" in m["text"] for m in msgs if m.get("text"))
    turns = c.get(f"/v1/conversations/{cc}/transcript", headers=H(key)).json()["turns"]
    assert turns[0] == {**turns[0], "role": "assistant", "text": "Welcome, Ravi!"}
    assert [t["role"] for t in turns] == ["assistant", "user", "assistant"]
    assert FakeLLM.seen[1][0]["content"] == "Welcome, Ravi!"  # greeting is part of the LLM's history


def test_objectives_judged_and_reported(env):
    c, key, cid = env
    pid = persona_id(c, key)
    c.put(f"/v1/personas/{pid}/config", json={"objectives": [
        {"name": "greet", "description": "Say hello", "output_variables": ["greeting_word"]},
        {"name": "close", "description": "Close the sale"}]}, headers=H(key))
    c.post("/v1/webhooks", json={"url": "http://x.test/h", "events": ["objective.completed"]}, headers=H(key))
    seen_prompts = []

    async def judge(system, prompt):
        seen_prompts.append(prompt)
        return ('Sure: {"objectives":[{"name":"greet","completed":true,"evidence":"user said hello there",'
                '"variables":{"greeting_word":"hello","junk":"x"}},{"name":"close","completed":false,"evidence":""}]}')

    lb.set_completer(judge)
    cc = c.post("/v1/conversations", json={"persona_id": pid}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
        ws.receive_json(); speak(ws)
        msgs = collect(ws, "objective_completed")
    ev = json.loads([m["text"] for m in msgs if m.get("text") and "objective_completed" in m["text"]][0])
    assert ev["name"] == "greet" and ev["variables"] == {"greeting_word": "hello"}
    assert "Close the sale" in FakeLLM.seen[0]  # open objectives are in the prompt
    objs = c.get(f"/v1/conversations/{cc}/objectives", headers=H(key)).json()
    assert objs == [{**objs[0], "name": "greet", "completed": True}]
    with Session(db.engine) as s:
        assert any(json.loads(d.payload)["type"] == "objective.completed" for d in s.exec(select(WebhookDelivery)).all())
    # a completed objective is not re-judged / re-announced at the end
    c.post(f"/v1/conversations/{cc}/end", headers=H(key))
    assert len(seen_prompts) >= 1 and all("- name: greet" not in p for p in seen_prompts[1:])


def test_guardrails_prompt_and_output_filter(env):
    c, key, cid = env
    pid = persona_id(c, key)
    c.put(f"/v1/personas/{pid}/config", json={
        "guardrails": [{"name": "no-test", "rule": "Never say the word second.", "forbidden_phrases": ["second sentence"]}],
        "guardrail_fallback": "I would rather not say."}, headers=H(key))
    c.post("/v1/webhooks", json={"url": "http://x.test/h", "events": ["guardrail.triggered"]}, headers=H(key))
    cc = c.post("/v1/conversations", json={"persona_id": pid}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
        ws.receive_json(); speak(ws)
        msgs = collect(ws, "agent_done")
    spoken = " ".join(json.loads(m["text"])["text"] for m in msgs if m.get("text") and '"role":"agent"' in m["text"].replace(" ", ""))
    assert "I would rather not say." in spoken and "Second" not in spoken
    assert "Never say the word second." in FakeLLM.seen[0]
    with Session(db.engine) as s:
        assert any(json.loads(d.payload)["type"] == "guardrail.triggered" for d in s.exec(select(WebhookDelivery)).all())


def test_guarded_llm_unit_global_blocklist_and_pass_through():
    class Inner:
        async def stream(self, system, history, user):
            for t in ["Hello there, ", "friend. ", "I will kill you. ", "Bye."]:
                yield t

    async def collect_all(llm):
        return "".join([t async for t in llm.stream("", [], "")])

    out = run(collect_all(lb.GuardedLLM(Inner(), [], "NOPE.")))
    assert "NOPE." in out and "kill" not in out and out.startswith("Hello there,")


# ---- tools / custom llm against real local HTTP servers ----


class _Handler(BaseHTTPRequestHandler):
    log = []
    llm_calls = 0

    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        _Handler.log.append((self.path, dict(self.headers), body))
        if self.path == "/tool":
            out = json.dumps({"speech": f"The weather in {body['arguments']['city']} is sunny."}).encode()
            self.send_response(200); self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
            return
        # OpenAI-compatible /chat/completions (streaming SSE)
        _Handler.llm_calls += 1
        has_tool_result = any(m.get("role") == "tool" for m in body["messages"])
        chunks = []
        if body.get("tools") and not has_tool_result:
            chunks = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "get_weather", "arguments": '{"ci'}}]}}]},
                      {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ty": "Paris"}'}}]}}]}]
        else:
            txt = [m["content"] for m in body["messages"] if m.get("role") == "tool"]
            for w in (["It is sunny in Paris. "] if txt else ["Plain answer. "]):
                chunks.append({"choices": [{"delta": {"content": w}}]})
        payload = "".join(f"data: {json.dumps(ch)}\n\n" for ch in chunks) + "data: [DONE]\n\n"
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
        self.wfile.write(payload.encode())


@pytest.fixture()
def http_server():
    _Handler.log, _Handler.llm_calls = [], 0
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_custom_openai_llm_with_tool_calling_and_signed_tool_webhook(env, http_server):
    c, key, cid = env
    pid = persona_id(c, key)
    c.put(f"/v1/personas/{pid}/config", json={"custom_llm": {"base_url": http_server, "model": "gpt-x", "api_key": "sk-abc"}},
          headers=H(key))
    t = c.post(f"/v1/personas/{pid}/tools", json={
        "name": "get_weather", "description": "Weather lookup",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        "webhook_url": http_server + "/tool", "secret": "tool-secret"}, headers=H(key))
    assert t.status_code == 200 and t.json()["has_secret"] and "secret" not in t.json()
    assert c.post(f"/v1/personas/{pid}/tools", json={"name": "get_weather", "webhook_url": http_server + "/tool"},
                  headers=H(key)).status_code == 409
    assert c.post(f"/v1/personas/{pid}/tools", json={"name": "bad name", "webhook_url": http_server}, headers=H(key)).status_code == 422
    cc = c.post("/v1/conversations", json={"persona_id": pid}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
        ws.receive_json(); speak(ws)
        msgs = collect(ws, "agent_done")
    spoken = " ".join(json.loads(m["text"])["text"] for m in msgs if m.get("text") and '"role":"agent"' in m["text"].replace(" ", ""))
    assert "sunny in Paris" in spoken
    llm_reqs = [l for l in _Handler.log if l[0] == "/chat/completions"]
    assert llm_reqs[0][1].get("authorization") == "Bearer sk-abc" and llm_reqs[0][2]["model"] == "gpt-x"
    assert llm_reqs[0][2]["tools"][0]["function"]["name"] == "get_weather"
    tool_req = [l for l in _Handler.log if l[0] == "/tool"][0]
    assert tool_req[2]["arguments"] == {"city": "Paris"} and tool_req[2]["conversation_id"] == cc
    sig = tool_req[1].get("VocalFace-Signature") or tool_req[1].get("vocalface-signature")
    assert webhooks.verify("tool-secret", json.dumps(tool_req[2], separators=(",", ":")), sig)
    calls = c.get(f"/v1/conversations/{cc}/tool-calls", headers=H(key)).json()
    assert calls[0]["tool"] == "get_weather" and calls[0]["ok"] and "sunny" in calls[0]["result"]
    assert c.delete(f"/v1/personas/{pid}/tools/{t.json()['id']}", headers=H(key)).status_code == 200


def test_tool_failure_is_handled_gracefully():
    class Be:
        n = 0

        async def chat(self, messages, tools=None):
            Be.n += 1
            if Be.n == 1:
                yield "tool_call", {"id": "1", "name": "t", "arguments": {}}
            else:
                assert any(m.get("role") == "tool" and "error" in m["content"] for m in messages)
                yield "text", "Sorry, that lookup failed."

        assemble = None
        assistant_tool_msg = staticmethod(lb.OllamaBackend.assistant_tool_msg)
        tool_result_msg = staticmethod(lb.OllamaBackend.tool_result_msg)

    lb.set_tool_post(lambda url, body, headers, timeout: (500, "x"))
    llm = lb.FeatureLLM(Be(), [lb.ToolSpec("t", "d", {}, "http://t")])

    async def go():
        return "".join([x async for x in llm.stream("s", [], "u")])

    assert run(go()) == "Sorry, that lookup failed."


# ---------------- 4. multilingual ----------------


def test_voices_and_languages_endpoints(env):
    c, key, _ = env
    v = c.get("/v1/voices", headers=H(key)).json()
    codes = {l["code"] for l in v["languages"]}
    assert {"en", "es", "hi", "fr", "auto"} <= codes and v["voices"]
    es = c.get("/v1/voices?language=es", headers=H(key)).json()["voices"]
    assert es and all(x["language"] == "es" for x in es) and any(x["default_for_language"] for x in es)
    assert c.get("/v1/voices").status_code == 422  # key required
    assert languages.lang_for_voice("hf_alpha") == "hi" and languages.lang_for_voice("ef_dora") == "es"
    assert languages.normalize_language("ES_es") == "es" and languages.normalize_language("pt-BR") == "pt"


def test_language_tts_routes_to_right_kokoro_voice():
    calls = []

    class K:
        def create(self, text, voice, speed, lang):
            import numpy as np
            calls.append((voice, lang))
            return np.zeros(240, dtype="float32"), 24000

    class Base:
        k = K()

        async def synthesize(self, text, voice="default"):
            calls.append(("base", voice))
            yield b"\0\0"

    async def go(tts, voice="default"):
        return [x async for x in tts.synthesize("hola", voice)]

    run(go(languages.LanguageTTS(Base(), "es")))
    run(go(languages.LanguageTTS(Base(), "hi")))
    run(go(languages.LanguageTTS(Base(), "en")))
    run(go(languages.LanguageTTS(Base(), "es"), "ff_siwis"))  # explicit voice wins
    assert calls == [("ef_dora", "es"), ("hf_alpha", "hi"), ("base", "default"), ("ff_siwis", "fr-fr")]


def test_language_prompt_and_wrapping(env):
    c, key, _ = env
    pid = persona_id(c, key)
    c.put(f"/v1/personas/{pid}/config", json={"language": "hi"}, headers=H(key))
    cc = c.post("/v1/conversations", json={"persona_id": pid}, headers=H(key)).json()["id"]
    # the multilingual STT/TTS wrappers are installed; swap the model-loading parts out
    import app.languages as L
    orig = (L.MultilingualSTT.transcribe, L.LanguageTTS.synthesize)
    FakeLLM.seen = None
    used = {}

    async def fake_tr(self, pcm, sr=16000):
        used["stt_lang"] = self.language
        return "नमस्ते"

    async def fake_synth(self, text, voice="default"):
        used["tts_lang"] = self._effective_language()
        yield b"\x01\x00" * 2400

    L.MultilingualSTT.transcribe, L.LanguageTTS.synthesize = fake_tr, fake_synth
    try:
        with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
            ws.receive_json(); speak(ws); msgs = collect(ws, "agent_done")
    finally:
        L.MultilingualSTT.transcribe, L.LanguageTTS.synthesize = orig
    assert any("नमस्ते" in m["text"] for m in msgs if m.get("text"))
    assert used == {"stt_lang": "hi", "tts_lang": "hi"}
    assert "Hindi" in FakeLLM.seen[0]


# ---------------- 5. api keys ----------------


def test_api_keys_hashed_multi_revoke_legacy_works(env):
    c, key, cid = env
    k = c.post("/v1/keys", json={"name": "ci"}, headers=H(key)).json()
    assert k["key"].startswith("mk_") and k["prefix"] == k["key"][:8]
    with Session(db.engine) as s:
        row = s.get(ApiKey, k["id"])
        assert row.key_hash != k["key"] and k["key"] not in row.key_hash and len(row.key_hash) == 64
    listing = c.get("/v1/keys", headers=H(k["key"])).json()
    assert not any(x.get("key") for x in listing)
    assert listing[0]["id"] == "legacy" and any(x["id"] == k["id"] for x in listing)
    assert c.get("/v1/usage", headers=H(k["key"])).status_code == 200
    # new key works for websockets too
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={k['key']}") as ws:
        assert ws.receive_json()["type"] == "ready"
    assert c.delete(f"/v1/keys/{k['id']}", headers=H(key)).json()["revoked_at"]
    assert c.get("/v1/usage", headers=H(k["key"])).status_code == 401
    assert c.get("/v1/usage", headers=H(key)).status_code == 200  # legacy still valid
    assert c.delete("/v1/keys/legacy", headers=H(key)).status_code == 409
    other = c.post("/v1/signup", json={"email": "o@o.o"}).json()["api_key"]
    k2 = c.post("/v1/keys", json={}, headers=H(key)).json()
    assert c.delete(f"/v1/keys/{k2['id']}", headers=H(other)).status_code == 404
    rot = c.post("/v1/keys/legacy/rotate", headers=H(key)).json()
    assert c.get("/v1/usage", headers=H(key)).status_code == 401 and c.get("/v1/usage", headers=H(rot["key"])).status_code == 200


# ---------------- 6. analytics ----------------


def test_analytics_and_metrics(env):
    c, key, cid = env
    lb.set_completer(lambda s, p: asyncio.sleep(0, result="sum"))
    for _ in range(2):
        pid = persona_id(c, key)
        cc = c.post("/v1/conversations", json={"persona_id": pid}, headers=H(key)).json()["id"]
        with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
            ws.receive_json(); speak(ws); collect(ws, "agent_done")
        c.post(f"/v1/conversations/{cc}/end", headers=H(key))
    a = c.get("/v1/analytics?days=7", headers=H(key)).json()
    assert a["totals"]["conversations"] == 3 and a["totals"]["user_turns"] == 2
    assert len(a["conversations_per_day"]) == 7 and a["conversations_per_day"][-1]["conversations"] == 3
    assert a["first_audio_latency_ms"]["samples"] == 2 and a["first_audio_latency_ms"]["avg"] > 0
    assert a["top_personas"][0]["name"] == "P" and a["top_personas"][0]["conversations"] == 3
    assert a["videos"]["total"] == 0 and "credits_seconds" in a
    other = c.post("/v1/signup", json={"email": "o@o.o"}).json()["api_key"]
    assert c.get("/v1/analytics", headers=H(other)).json()["totals"]["conversations"] == 0


# ---------------- 7. video features ----------------


def _ready_replica(c, key):
    rid = c.post("/v1/replicas", json={"name": "r", "train_video_url": "http://x/v.mp4"}, headers=H(key)).json()["id"]
    with Session(db.engine) as s:
        r = s.get(db.Replica, rid); r.status = "ready"; s.add(r); s.commit()
    return rid


def test_template_preview_and_bulk(env):
    c, key, _ = env
    rid = _ready_replica(c, key)
    pv = c.post("/v1/videos/template/preview", json={"script_template": "Hi {{first_name}} at {{ company }}",
                                                      "variables": {"first_name": "Ann"}}, headers=H(key)).json()
    assert pv["variables"] == ["company", "first_name"] and pv["missing"] == ["company"] and pv["rendered"] is None
    body = {"replica_id": rid, "script_template": "Hi {{first_name}}, welcome to {{company}}.",
            "rows": [{"first_name": "Ann", "company": "Acme"}, {"first_name": "Bo", "company": "Zed"}], "voice": "af_bella"}
    r = c.post("/v1/video-jobs/bulk", json=body, headers=H(key))
    assert r.status_code == 200
    b = r.json()
    assert b["total"] == 2 and b["items"][0]["script"] == "Hi Ann, welcome to Acme." and b["counts"] == {"queued": 2}
    vid = b["items"][1]["video_id"]
    assert c.get(f"/v1/videos/{vid}", headers=H(key)).json()["script"] == "Hi Bo, welcome to Zed."
    from app.models_extra import VideoMeta
    with Session(db.engine) as s:
        assert s.get(VideoMeta, vid).voice == "af_bella"
    bad = c.post("/v1/video-jobs/bulk", json={**body, "rows": [{"first_name": "Ann"}, {"first_name": "B", "company": "Z"}]}, headers=H(key))
    assert bad.status_code == 422 and bad.json()["detail"]["rows"] == [{"row": 0, "missing": ["company"]}]
    harm = c.post("/v1/video-jobs/bulk", json={**body, "script_template": "I will kill you, {{first_name}}"}, headers=H(key))
    assert harm.status_code == 422
    hv = c.post("/v1/video-jobs/bulk", json={**body, "rows": [{"first_name": "send me your password", "company": "x"}]}, headers=H(key))
    assert hv.status_code in (200, 422)
    nr = c.post("/v1/video-jobs/bulk", json={**body, "replica_id": "r_nope"}, headers=H(key))
    assert nr.status_code == 404
    assert c.get(f"/v1/video-batches/{b['id']}", headers=H(key)).json()["total"] == 2
    assert len(c.get("/v1/video-batches", headers=H(key)).json()) >= 1


def test_translate_variants_and_batch_completion_webhook(env, monkeypatch):
    c, key, _ = env
    rid = _ready_replica(c, key)
    from app.routers import video_features_api as vf

    async def fake_translate(text, src, dst):
        return f"[{dst}] {text}"

    monkeypatch.setattr(vf, "translate_text", fake_translate)
    c.post("/v1/webhooks", json={"url": "http://x.test/h", "events": ["video_batch.completed", "video.ready"]}, headers=H(key))
    r = c.post("/v1/video-jobs/translate", json={"replica_id": rid, "script": "Hello world", "languages": ["es", "hi", "es"],
                                                  "include_original": True}, headers=H(key))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["language"] for i in items] == ["en", "es", "hi"] and items[1]["script"] == "[es] Hello world"
    from app.models_extra import VideoMeta
    with Session(db.engine) as s:
        assert [s.get(VideoMeta, i["video_id"]).voice for i in items] == ["af_heart", "ef_dora", "hf_alpha"]
    assert c.post("/v1/video-jobs/translate", json={"replica_id": rid, "script": "x", "languages": ["tlh"]}, headers=H(key)).status_code == 422
    assert c.post("/v1/video-jobs/translate", json={"replica_id": rid, "script": "x", "languages": ["de"]}, headers=H(key)).status_code == 422
    from app import events
    for i in items:
        with Session(db.engine) as s:
            v = s.get(db.Video, i["video_id"]); v.status = "ready"; v.output_url = "/v1/files/videos/x.mp4"; s.add(v); s.commit()
        events.on_video_finished(i["video_id"])
    with Session(db.engine) as s:
        types = [json.loads(d.payload)["type"] for d in s.exec(select(WebhookDelivery)).all()]
    assert types.count("video.ready") == 3 and types.count("video_batch.completed") == 1
    assert c.get(f"/v1/video-batches/{r.json()['id']}", headers=H(key)).json()["completed"] is True


def test_kokoro_voice_language_for_video_renderer():
    from app import jobs
    seen = {}

    class K:
        def create(self, text, voice, speed, lang):
            import numpy as np
            seen.update(voice=voice, lang=lang)
            return np.zeros(100, dtype="float32"), 24000

    v = jobs.KokoroPresetVoice(); v._k = K()
    import tempfile, pathlib
    v.synthesize("hola", pathlib.Path(tempfile.mkdtemp()) / "a.wav", None, "ef_dora")
    assert seen == {"voice": "ef_dora", "lang": "es"}


# ---------------- 8. guest share links ----------------


def _share(c, key, **kw):
    pid = persona_id(c, key)
    r = c.post(f"/v1/personas/{pid}/share", json=kw, headers=H(key))
    assert r.status_code == 200, r.text
    return r.json()


def test_share_create_info_page_and_revoke(env):
    c, key, _ = env
    s = _share(c, key, label="demo", max_seconds=60)
    assert s["token"].startswith("sh_") and s["url"].endswith("/guest/" + s["token"])
    info = c.get(f"/v1/guest/{s['token']}/info").json()
    assert info["persona_name"] == "P" and info["max_seconds"] == 60 and info["available"]
    page = c.get(f"/guest/{s['token']}")
    assert page.status_code == 200 and "AudioWorklet" in page.text and "api_key" not in page.text
    assert c.get("/guest/not-a-token").status_code == 404
    assert c.delete(f"/v1/share/{s['token']}", headers=H(key)).json()["revoked"] is True
    assert c.get(f"/v1/guest/{s['token']}/info").status_code == 404
    assert c.post(f"/v1/guest/{s['token']}/conversations").status_code == 404
    other = c.post("/v1/signup", json={"email": "o@o.o"}).json()["api_key"]
    s2 = _share(c, key)
    assert c.delete(f"/v1/share/{s2['token']}", headers=H(other)).status_code == 404


def test_share_expiry(env):
    c, key, _ = env
    s = _share(c, key, expires_in_hours=1)
    with Session(db.engine) as ss:
        l = ss.get(ShareLink, s["token"]); l.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1); ss.add(l); ss.commit()
    assert c.get(f"/v1/guest/{s['token']}/info").status_code == 410


def test_guest_rate_limits_and_cost_cap(env):
    c, key, _ = env
    s = _share(c, key, max_seconds=30, max_total_seconds=50, max_sessions_per_ip_hour=2)
    t = s["token"]
    a = c.post(f"/v1/guest/{t}/conversations")
    assert a.status_code == 200 and a.json()["max_seconds"] == 30
    b = c.post(f"/v1/guest/{t}/conversations")
    assert b.status_code == 200 and b.json()["max_seconds"] == 20  # cost cap: 50 - 30 reserved
    assert c.post(f"/v1/guest/{t}/conversations").status_code == 429  # per-IP cap (2/hour)
    s2 = _share(c, key, max_seconds=30, max_total_seconds=30, max_sessions_per_ip_hour=10)
    assert c.post(f"/v1/guest/{s2['token']}/conversations").status_code == 200
    assert c.post(f"/v1/guest/{s2['token']}/conversations").status_code == 429  # total cap reached
    s3 = _share(c, key, max_sessions_per_hour=1, max_sessions_per_ip_hour=10)
    assert c.post(f"/v1/guest/{s3['token']}/conversations").status_code == 200
    assert c.post(f"/v1/guest/{s3['token']}/conversations").status_code == 429  # per-link hourly cap
    # owner out of credits
    from app.db import Account
    with Session(db.engine) as ss:
        acc = ss.exec(select(Account)).first(); acc.credits_seconds = 0; ss.add(acc); ss.commit()
    s4 = _share(c, key)
    assert c.post(f"/v1/guest/{s4['token']}/conversations").status_code == 402


def test_guest_websocket_full_flow_counts_usage(env):
    c, key, _ = env
    lb.set_completer(lambda s_, p: asyncio.sleep(0, result="guest summary"))
    s = _share(c, key, max_seconds=60)
    t = s["token"]
    sess = c.post(f"/v1/guest/{t}/conversations").json()
    cid = sess["conversation_id"]
    with pytest.raises(Exception):  # wrong token / foreign cid is rejected
        with c.websocket_connect(f"/v1/guest/sh_wrong/stream?cid={cid}"):
            pass
    with pytest.raises(Exception):
        with c.websocket_connect(f"/v1/guest/{t}/stream?cid=c_nope"):
            pass
    with c.websocket_connect(sess["ws_path"]) as ws:
        assert ws.receive_json()["type"] == "ready"
        speak(ws); collect(ws, "agent_done")
    with Session(db.engine) as ss:
        conv = ss.get(db.Conversation, cid)
        assert conv.status == "ended" and conv.seconds_used >= 1
        link = ss.get(ShareLink, t)
        assert link.used_seconds == conv.seconds_used and link.sessions_started == 1
        assert ss.get(ShareSession, cid).counted
        assert ss.get(ConversationMeta, cid).summary == "guest summary"
    # an ended conversation cannot be re-attached
    with pytest.raises(Exception):
        with c.websocket_connect(sess["ws_path"]) as ws:
            ws.receive_json()
    tr = c.get(f"/v1/conversations/{cid}/transcript", headers=H(key)).json()
    assert [x["role"] for x in tr["turns"]] == ["user", "assistant"]


def test_time_limit_closes_websocket(env):
    c, key, cid = env
    pid = persona_id(c, key)
    cc = c.post("/v1/conversations", json={"persona_id": pid, "max_seconds": 1}, headers=H(key)).json()["id"]
    with c.websocket_connect(f"/v1/conversations/{cc}/stream?api_key={key}") as ws:
        ws.receive_json()
        msgs = []
        for _ in range(200):
            m = ws.receive()
            msgs.append(m)
            if m["type"] == "websocket.close":
                break
    assert msgs[-1]["type"] == "websocket.close" and msgs[-1]["code"] == 4408
    assert any("time limit" in (m.get("text") or "") for m in msgs)


# ---------------- reaper ----------------


def test_reaper_ends_abandoned_conversations(env):
    c, key, cid = env
    lb.set_completer(lambda s, p: asyncio.sleep(0, result="reaped summary"))
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json(); speak(ws); collect(ws, "agent_done")
    assert cr.reap_idle(grace_s=3600) == []  # closed just now: within grace
    with Session(db.engine) as s:
        m = s.get(ConversationMeta, cid); m.last_closed_at = datetime.now(timezone.utc) - timedelta(minutes=5); s.add(m); s.commit()
    ended = cr.reap_idle(grace_s=30)
    assert ended == [cid]
    run(cr.finalize_conversation(cid, "idle"))
    assert c.get("/v1/usage", headers=H(key)).json()["credits_seconds"] < 600
    assert c.get(f"/v1/conversations/{cid}/summary", headers=H(key)).json()["summary"] == "reaped summary"
    # later explicit /end does not double charge
    before = c.get("/v1/usage", headers=H(key)).json()["credits_seconds"]
    c.post(f"/v1/conversations/{cid}/end", headers=H(key))
    assert c.get("/v1/usage", headers=H(key)).json()["credits_seconds"] == before


# ---------------- webhook test endpoint against a real receiver + SDK ----------------


def test_webhook_test_endpoint_delivers_signed_request_to_real_server(env):
    c, key, _ = env
    got = []

    class R(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            n = int(self.headers.get("content-length", 0))
            got.append((self.rfile.read(n).decode(), dict(self.headers)))
            self.send_response(204); self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), R)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        wh = c.post("/v1/webhooks", json={"url": f"http://127.0.0.1:{srv.server_port}/x"}, headers=H(key)).json()
        d = c.post(f"/v1/webhooks/{wh['id']}/test", headers=H(key)).json()
    finally:
        srv.shutdown()
    assert d["status"] == "delivered" and d["last_status_code"] == 204
    body, headers = got[0]
    assert json.loads(body)["type"] == "webhook.test"
    assert webhooks.verify(wh["secret"], body, headers["VocalFace-Signature"])
    # a dead receiver is recorded, not raised, and retried later
    wh2 = c.post("/v1/webhooks", json={"url": "http://127.0.0.1:1/x"}, headers=H(key)).json()
    d2 = c.post(f"/v1/webhooks/{wh2['id']}/test", headers=H(key)).json()
    assert d2["status"] == "pending" and d2["last_error"] and d2["attempts"] == 1


def test_sdk_feature_methods(env):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))
    from vocalface_sdk import VocalFace

    c, key, cid = env
    m = VocalFace(api_key=key, http_client=c)
    pid = m.list_personas()[0]["id"]
    m.set_persona_config(pid, greeting="Hi", language="es", objectives=[{"name": "o", "description": "d"}])
    assert m.get_persona_config(pid)["language"] == "es"
    t = m.add_tool(pid, "lookup", "http://x.test/t", "desc", {"type": "object", "properties": {}}, secret="s")
    assert m.list_tools(pid)[0]["name"] == "lookup"
    m.delete_tool(pid, t["id"])
    wh = m.create_webhook("http://x.test/h", ["conversation.ended"])
    body = '{"a":1}'
    sig = webhooks.sign(wh["secret"], body)
    assert VocalFace.verify_webhook(wh["secret"], body, sig) and not VocalFace.verify_webhook("nope", body, sig)
    assert m.list_webhooks()[0]["id"] == wh["id"] and m.webhook_deliveries(wh["id"]) == []
    m.delete_webhook(wh["id"])
    assert any(v["id"] == "ef_dora" for v in m.voices("es")["voices"])
    k = m.create_key("x"); assert m.list_keys()[-1]["id"] == k["id"]; assert m.revoke_key(k["id"])["revoked_at"]
    assert m.analytics(3)["range_days"] == 3
    assert m.preview_template("Hi {{n}}", {"n": "A"})["rendered"] == "Hi A"
    s = m.create_share_link(pid, max_seconds=30); assert m.list_share_links(pid)[0]["token"] == s["token"]
    assert m.revoke_share_link(s["token"])["revoked"]
    c2 = m.create_conversation(pid, variables={"a": "b"}, participant_id="p1")
    assert m.get_transcript(c2["id"])["turns"] == [] and m.get_summary(c2["id"])["ready"] is False
    assert m.get_transcript_text(c2["id"]) == ""
    assert m.get_objectives(c2["id"]) == [] and m.get_tool_calls(c2["id"]) == []


def test_judge_backend_env_override(env, monkeypatch):
    c, key, cid = env
    with Session(db.engine) as s:
        conv = s.get(db.Conversation, cid); persona = s.get(db.Persona, conv.persona_id)
        rt = cr.ConversationRuntime.build(s, conv, persona)
    assert rt.judge_backend().model != "qwen3:8b"
    monkeypatch.setenv("VOCALFACE_JUDGE_MODEL", "qwen3:8b")
    assert rt.judge_backend().model == "qwen3:8b"


def test_delivery_lease_prevents_double_send(env):
    c, key, _ = env
    c.post("/v1/webhooks", json={"url": "http://x.test/h"}, headers=H(key))
    with Session(db.engine) as s:
        from app.db import Account
        aid = s.exec(select(Account)).first().id
    webhooks.emit(aid, "video.ready", {"video_id": "v"})
    inner = []

    def outer_sender(url, headers, body):
        inner.append(webhooks.deliver_due(lambda u, h, b: (200, "dup")))  # a second worker running at the same time
        return 200, "ok"

    assert webhooks.deliver_due(outer_sender) == 1
    assert inner == [0]
    with Session(db.engine) as s:
        d = s.exec(select(WebhookDelivery)).first()
        assert d.status == "delivered" and d.attempts == 1
