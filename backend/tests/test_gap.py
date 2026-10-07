"""Competitor-gap features: URL knowledge + citations, pronunciation glossary, interruption tuning, insights, scheduled links."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app import db, gap_knowledge, insights, pronunciation, voice_tuning
from app.models_gap import KnowledgeSource, TurnCitation

from .test_features import H, persona_id  # noqa: F401
from .test_realtime import FakeLLM, FakeTTS, collect, env, speak  # noqa: F401
from app.pipeline import session as sess_mod
from app.pipeline.session import Providers

@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    from app import safety

    monkeypatch.setenv("VOCALFACE_RL_SIGNUP", "1000/60")
    monkeypatch.setenv("VOCALFACE_RL_IP", "100000/60")
    monkeypatch.setenv("VOCALFACE_RL_KEY", "100000/60")
    safety.limiter.reset()
    yield
    safety.limiter.reset()


HTML = """<html><head><title>Acme Pricing</title><style>.x{color:red}</style><script>var secret=1;</script></head>
<body><nav>Home About</nav><h1>Plans</h1><p>The Pro plan costs $79 per month and includes 5 seats.</p>
<footer>copyright</footer></body></html>"""


@pytest.fixture()
def fetcher():
    state = {"html": HTML, "calls": []}

    def f(url):
        state["calls"].append(url)
        return "text/html; charset=utf-8", state["html"]

    gap_knowledge.set_fetcher(f)
    yield state
    gap_knowledge.set_fetcher(None)


# ---------- URL knowledge ----------

def test_html_to_text_strips_noise():
    title, text = gap_knowledge.html_to_text(HTML)
    assert title == "Acme Pricing"
    assert "Pro plan costs $79" in text and "secret" not in text and "color:red" not in text
    assert "Home About" not in text and "copyright" not in text and "# Plans" in text


def test_url_ingest_search_refresh(env, fetcher):
    c, key, _ = env
    pid = persona_id(c, key)
    r = c.post(f"/v1/personas/{pid}/knowledge/url", json={"url": "https://acme.test/pricing"}, headers=H(key))
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["title"] == "Acme Pricing" and doc["source"] == "url" and doc["n_chunks"] >= 1
    hits = c.post(f"/v1/personas/{pid}/knowledge/search", json={"query": "Pro plan price per month"}, headers=H(key)).json()
    assert hits and "$79" in hits[0]["text"]
    assert c.get(f"/v1/personas/{pid}/knowledge-sources", headers=H(key)).json()[0]["url"] == "https://acme.test/pricing"
    r2 = c.post(f"/v1/personas/{pid}/knowledge/{doc['id']}/refresh", headers=H(key)).json()
    assert r2 == {"changed": False, "doc_id": doc["id"]}
    fetcher["html"] = HTML.replace("$79", "$99")
    r3 = c.post(f"/v1/personas/{pid}/knowledge/{doc['id']}/refresh", headers=H(key)).json()
    assert r3["changed"] and r3["doc_id"] != doc["id"]
    docs = c.get(f"/v1/personas/{pid}/knowledge", headers=H(key)).json()
    assert [d["id"] for d in docs] == [r3["doc_id"]]
    hits = c.post(f"/v1/personas/{pid}/knowledge/search", json={"query": "Pro plan price"}, headers=H(key)).json()
    assert "$99" in hits[0]["text"] and all("$79" not in h["text"] for h in hits)


def test_url_validation_and_isolation(env, fetcher):
    c, key, _ = env
    pid = persona_id(c, key)
    assert c.post(f"/v1/personas/{pid}/knowledge/url", json={"url": "ftp://x.test/a"}, headers=H(key)).status_code == 422
    fetcher["html"] = "<html><script>x</script></html>"
    assert c.post(f"/v1/personas/{pid}/knowledge/url", json={"url": "https://x.test/empty"}, headers=H(key)).status_code == 422
    other = c.post("/v1/signup", json={"email": "z@z.z"}).json()["api_key"]
    assert c.post(f"/v1/personas/{pid}/knowledge/url", json={"url": "https://x.test/a"}, headers=H(other)).status_code == 404


def test_url_fetch_is_ssrf_guarded(monkeypatch):
    from app import netguard

    monkeypatch.setenv("VOCALFACE_BLOCK_PRIVATE_URLS", "1")
    assert netguard.blocking()
    with pytest.raises(ValueError, match="private|loopback"):
        gap_knowledge.fetch_document("http://127.0.0.1:9/secret")
    with pytest.raises(ValueError, match="only http"):
        gap_knowledge.fetch_document("file:///etc/passwd")


# ---------- citations (live conversation) ----------

def test_citations_recorded_and_streamed(env, fetcher):
    c, key, cid = env
    pid = persona_id(c, key)
    c.post(f"/v1/personas/{pid}/knowledge/url", json={"url": "https://acme.test/pricing"}, headers=H(key))
    c.post(f"/v1/personas/{pid}/knowledge/text", json={"title": "Pets", "text": "Pets are welcome in all rooms."}, headers=H(key))

    class STT:
        async def transcribe(self, pcm, sr=16000):
            return "how much does the pro plan cost per month"

    sess_mod.set_provider_factory(lambda spec="": Providers(STT(), FakeLLM(), FakeTTS()))
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json()
        speak(ws)
        msgs = collect(ws, "agent_done")
    texts = [m["text"] for m in msgs if m.get("text")]
    assert any('"citations"' in t.replace(" ", "") for t in texts)
    cites = c.get(f"/v1/conversations/{cid}/citations", headers=H(key)).json()
    assert cites and cites[0]["sources"][0]["title"] == "Acme Pricing" and cites[0]["sources"][0]["url"] == "https://acme.test/pricing"
    assert "$79" in cites[0]["sources"][0]["snippet"]
    tr = c.get(f"/v1/conversations/{cid}/transcript", headers=H(key)).json()["turns"]
    assert cites[0]["seq"] == [t for t in tr if t["role"] == "assistant"][0]["seq"]


# ---------- pronunciation ----------

def test_pronunciation_rules_apply():
    class E:
        def __init__(self, t, r, cs=False):
            self.term, self.replacement, self.case_sensitive = t, r, cs

    rules = pronunciation.compile_rules([E("AI", "A.I."), E("AI Labs", "A I Labs"), E("Nuvee", "NOO-vee"), E("SQL", "sequel", True)])
    assert pronunciation.apply("Nuvee's AI Labs build AI, said.", rules) == "NOO-vee's A I Labs build A.I., said."
    assert pronunciation.apply("said", rules) == "said"  # word boundaries
    assert pronunciation.apply("sql SQL", rules) == "sql sequel"  # case sensitive
    assert pronunciation.apply("x", []) == "x"


def test_pronunciation_api_and_tts_wrapper(env):
    import asyncio

    c, key, cid = env
    pid = persona_id(c, key)
    r = c.post(f"/v1/personas/{pid}/pronunciations", json={"term": "Nuvee", "replacement": "NOO-vee"}, headers=H(key))
    assert r.status_code == 200
    c.post(f"/v1/personas/{pid}/pronunciations", json={"term": "nuvee", "replacement": "NOO-vay"}, headers=H(key))  # upsert by term
    rows = c.get(f"/v1/personas/{pid}/pronunciations", headers=H(key)).json()
    assert len(rows) == 1 and rows[0]["replacement"] == "NOO-vay"
    pv = c.post(f"/v1/personas/{pid}/pronunciations/preview", json={"text": "Welcome to Nuvee."}, headers=H(key)).json()
    assert pv["spoken_as"] == "Welcome to NOO-vay."
    assert c.post(f"/v1/personas/{pid}/pronunciations", json={"term": "", "replacement": "x"}, headers=H(key)).status_code == 422
    # the runtime wraps the TTS: the base engine receives the respelling, the transcript keeps the original
    seen = []

    class Rec:
        async def synthesize(self, text, voice="default"):
            seen.append(text)
            yield b"\x01\x00" * 2400

    class LLM:
        async def stream(self, system, history, user):
            yield "Welcome to Nuvee. "

    sess_mod.set_provider_factory(lambda spec="": Providers(type("S", (), {"transcribe": lambda *a, **k: _co("hi")})(), LLM(), Rec()))
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json()
        speak(ws)
        collect(ws, "agent_done")
    assert any("NOO-vay" in t for t in seen) and not any("Nuvee" in t for t in seen)
    tr = c.get(f"/v1/conversations/{cid}/transcript", headers=H(key)).json()["turns"]
    assert "Nuvee" in tr[-1]["text"]
    assert c.delete(f"/v1/personas/{pid}/pronunciations/{rows[0]['id']}", headers=H(key)).status_code == 200


async def _co(v):
    return v


# ---------- interruption tuning ----------

def test_barge_frames_mapping():
    assert voice_tuning.barge_frames(5, 0.5) == 5
    assert voice_tuning.barge_frames(5, 1.0) == 2 and voice_tuning.barge_frames(5, 0.0) == 8
    assert voice_tuning.barge_frames(5, 0.8) < 5 < voice_tuning.barge_frames(5, 0.2)


def test_tuning_api_and_applied_to_session(env):
    c, key, cid = env
    pid = persona_id(c, key)
    assert c.get(f"/v1/personas/{pid}/voice-tuning", headers=H(key)).json()["interruption_sensitivity"] == 0.5
    r = c.put(f"/v1/personas/{pid}/voice-tuning", json={"interruption_sensitivity": 0.9, "turn_patience_ms": 1200}, headers=H(key)).json()
    assert r["interruption_sensitivity"] == 0.9 and r["turn_patience_ms"] == 1200 and r["allow_interruptions"] is True
    for bad in ({"interruption_sensitivity": 1.5}, {"turn_patience_ms": 50}):
        assert c.put(f"/v1/personas/{pid}/voice-tuning", json=bad, headers=H(key)).status_code == 422
    captured = {}
    orig = voice_tuning.apply

    def spy(sess, t):
        orig(sess, t)
        captured.update(frames=sess.barge_frames, eot=sess.turn.s.end_of_turn_ms)

    voice_tuning.apply, = (spy,)
    try:
        with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
            assert ws.receive_json()["type"] == "ready"
    finally:
        voice_tuning.apply = orig
    assert captured == {"frames": voice_tuning.barge_frames(5, 0.9), "eot": 1200}


def test_interruptions_disabled(env):
    c, key, cid = env
    pid = persona_id(c, key)
    c.put(f"/v1/personas/{pid}/voice-tuning", json={"allow_interruptions": False}, headers=H(key))
    from app.models_gap import VoiceTuning

    class S:
        barge_frames = 5

        class turn:
            class s:
                end_of_turn_ms = 700

    with Session(db.engine) as s:
        t = s.get(VoiceTuning, pid)
    sess = S()
    voice_tuning.apply(sess, t)
    assert sess.barge_frames > 10 ** 6


# ---------- insights ----------

def test_sentiment_scoring():
    assert insights.score_text("This is great, thanks, really helpful!") > 0.2
    assert insights.score_text("This is terrible and useless, I want a refund") < -0.2
    assert insights.score_text("it is not good") < 0
    assert insights.score_text("what time do you open") == 0.0


def test_analyse_trend_topics():
    turns = []
    msgs = ["this is terrible and broken", "useless slow problem", "ok that works", "great thanks, perfect and helpful"]
    for i, m in enumerate(msgs):
        turns += [{"seq": 2 * i, "role": "user", "text": m, "interrupted": False},
                  {"seq": 2 * i + 1, "role": "assistant", "text": "Sure, let me help with the refund", "interrupted": i == 1}]
    d = insights.analyse(turns)
    assert d["trend"] == "improving" and d["interruptions"] == 1 and len(d["turn_sentiments"]) == 4
    assert "refund" not in d["topics"]  # agent words never count as user topics
    d2 = insights.analyse([{"seq": 0, "role": "user", "text": "pricing pricing pricing for teams?", "interrupted": False}])
    assert d2["topics"][0] == "pricing" and d2["questions"] == 1


def test_insights_endpoints(env):
    c, key, cid = env
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json()
        speak(ws)
        collect(ws, "agent_done")
    c.post(f"/v1/conversations/{cid}/end", headers=H(key))
    i = c.get(f"/v1/conversations/{cid}/insights", headers=H(key)).json()
    assert i["conversation_id"] == cid and i["label"] == "neutral" and i["user_words"] == 2 and 0 < i["user_talk_ratio"] < 1
    o = c.get("/v1/analytics/insights", headers=H(key)).json()
    assert o["conversations"] == 1 and o["labels"]["neutral"] == 1 and o["by_day"][0]["conversations"] == 1
    assert c.get("/v1/analytics/insights?persona_id=nope", headers=H(key)).json()["conversations"] == 0
    other = c.post("/v1/signup", json={"email": "q@q.q"}).json()["api_key"]
    assert c.get(f"/v1/conversations/{cid}/insights", headers=H(other)).status_code == 404
    assert c.get("/v1/analytics/insights", headers=H(other)).json()["conversations"] == 0


# ---------- scheduled links ----------

def _link(c, key, pid):
    return c.post(f"/v1/personas/{pid}/share", json={}, headers=H(key)).json()["token"]


def test_scheduled_link_window(env):
    c, key, _ = env
    pid = persona_id(c, key)
    tok = _link(c, key, pid)
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    r = c.put(f"/v1/share/{tok}/schedule", json={"starts_at": soon.isoformat(), "duration_minutes": 30, "invitee_name": "Ravi",
                                                  "invitee_email": "r@x.io", "note": "Demo, part 1"}, headers=H(key))
    assert r.status_code == 200 and r.json()["invitee_name"] == "Ravi"
    info = c.get(f"/v1/guest/{tok}/info").json()
    assert info["opens_at"] is not None and info["closes_at"] is not None
    early = c.post(f"/v1/guest/{tok}/conversations")
    assert early.status_code == 425 and early.json()["detail"]["opens_at"]
    # move the window to now -> joinable
    c.put(f"/v1/share/{tok}/schedule", json={"starts_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                                             "duration_minutes": 30}, headers=H(key))
    assert c.post(f"/v1/guest/{tok}/conversations").status_code == 200
    # after the window
    from app.models_gap import ShareSchedule

    with Session(db.engine) as s:
        x = s.get(ShareSchedule, tok)
        x.ends_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.add(x); s.commit()
    assert c.post(f"/v1/guest/{tok}/conversations").status_code == 410
    # cleared -> open again
    assert c.delete(f"/v1/share/{tok}/schedule", headers=H(key)).status_code == 200
    assert c.post(f"/v1/guest/{tok}/conversations").status_code == 200


def test_schedule_validation_ics_ownership(env):
    c, key, _ = env
    pid = persona_id(c, key)
    tok = _link(c, key, pid)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert c.put(f"/v1/share/{tok}/schedule", json={"starts_at": past.isoformat(), "duration_minutes": 30}, headers=H(key)).status_code == 422
    assert c.put(f"/v1/share/{tok}/schedule", json={"starts_at": "2099-01-01T10:00:00", "duration_minutes": 1}, headers=H(key)).status_code == 422
    assert c.get(f"/v1/guest/{tok}/schedule.ics").status_code == 404
    c.put(f"/v1/share/{tok}/schedule", json={"starts_at": "2099-01-01T10:00:00Z", "duration_minutes": 45, "note": "Hi; there, ok"}, headers=H(key))
    ics = c.get(f"/v1/guest/{tok}/schedule.ics")
    assert ics.headers["content-type"].startswith("text/calendar")
    assert "DTSTART:20990101T100000Z" in ics.text and "DTEND:20990101T104500Z" in ics.text and "Hi\\; there\\, ok" in ics.text
    assert f"/guest/{tok}" in ics.text and "BEGIN:VEVENT" in ics.text
    other = c.post("/v1/signup", json={"email": "o@o.o"}).json()["api_key"]
    assert c.put(f"/v1/share/{tok}/schedule", json={"starts_at": "2099-01-01T10:00:00Z"}, headers=H(other)).status_code == 404
    assert c.get(f"/v1/share/{tok}/schedule", headers=H(other)).status_code == 404
