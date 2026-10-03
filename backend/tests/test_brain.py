"""Intelligence layer: chunking, ranking, grounding scaffold, perception (fake VLM, no models, no network)."""
import asyncio
import base64
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db, knowledge as kb, llm_backends as lb
from app.main import app
from app.perception import manager as pm


# ---------------- knowledge ----------------

DOC = "# Acme - Pricing\n\n## Plans\nThe Pro plan costs 99 dollars per month.\n\n## Refunds\nYearly plans can be refunded within 30 days.\n\n## Support\nPhone support is open Monday to Friday."


def test_header_chunks_carry_section_path_and_never_emit_bare_headings():
    ch = kb.chunk_text(DOC, 200, 20, headers=True)
    assert any("[Acme - Pricing > Plans]\nThe Pro plan costs 99" in c for c in ch)
    assert any("[Acme - Pricing > Refunds]\nYearly plans" in c for c in ch)   # every section keeps its own label
    assert not any("#" in c for c in ch)
    assert kb.chunk_text("", headers=True) == []


def test_header_chunks_respect_max_chars():
    long = "# T\n## S\n" + " ".join(f"Sentence number {i} is here." for i in range(60))
    assert all(len(c) <= 330 for c in kb.chunk_text(long, 300, 30, headers=True))


def test_bm25_stopwords_and_stemming():
    s = kb.bm25_scores("how are refunds handled", ["Yearly plans can be refunded", "We like cats and dogs", "the is are of"])
    assert s[0] > s[1] and s[0] > s[2]
    assert kb._tokens("9:30 am start, error E40") == ["9:30", "start", "error", "e40"]


class _Emb:
    name = "fake"

    def embed(self, texts):
        v = np.array([[t.lower().count(w) for w in ("refund", "price", "phone")] for t in texts], dtype=np.float32) + 1e-3
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_build_index_ranks_with_fake_embedder():
    f = kb.build_index(DOC, _Emb())
    top = f("can I get a refund", 1)[0]["text"]
    assert "refunded" in top
    assert "[Acme - Pricing > Support]" in f("phone support", 1)[0]["text"]


def test_rank_bm25_bonus_lifts_exact_term():
    texts = ["alpha beta", "gamma error E40 wiring", "delta"]
    vecs = np.array([[1, 0], [0.7, 0.7], [0, 1]], dtype=np.float32)

    class E:
        name = "x"

        def embed(self, t):
            return vecs

        def embed_query(self, q):
            return np.array([1, 0], dtype=np.float32)
    plain, _ = kb.rank("error E40", texts, vecs, E(), "dense")
    fused, method = kb.rank("error E40", texts, vecs, E(), "fuse")
    assert method == "dense" and plain.index(max(plain)) == 0 and fused[1] - fused[0] > plain[1] - plain[0]


def test_format_context_grounds_and_numbers_hits():
    out = kb.format_context([{"text": "A fact.", "title": "T"}, {"text": "B fact."}])
    assert out.startswith("Relevant knowledge") and "[1] (T) A fact." in out and "[2] B fact." in out
    assert "ONLY" in out and kb.format_context([]) == ""


# ---------------- grounding scaffold ----------------

def test_grounded_prepare_trims_history_and_adds_reminder():
    hist = []
    for i in range(10):
        hist += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": "a" * 900}]
    s, h, u = lb.GroundedLLM.prepare("sys\nRelevant knowledge. x", hist, "what?", max_msgs=5)
    assert len(h) <= 5 and h[0]["role"] == "user" and all(len(m["content"]) < 500 for m in h)
    assert u.startswith("what?") and "two short spoken sentences" in u and "knowledge excerpts" in u
    s2, h2, u2 = lb.GroundedLLM.prepare("plain system", [], "hi")
    assert "knowledge excerpts" not in u2 and h2 == []


def test_grounded_llm_passthrough_keeps_original_history_untouched():
    seen = {}

    class Inner:
        model = "m"

        async def stream(self, system, history, user):
            seen.update(system=system, history=history, user=user)
            yield "ok"

    async def go():
        g = lb.GroundedLLM(Inner())
        assert g.model == "m"
        return [t async for t in g.stream("s", [{"role": "user", "content": "a"}], "q")]
    assert asyncio.run(go()) == ["ok"] and seen["user"].startswith("q")


# ---------------- perception ----------------

def jpg(color=(200, 30, 30), size=(320, 240), mark=None) -> bytes:
    im = Image.new("RGB", size, color)
    if mark:
        for x in range(mark, mark + 60):
            for y in range(60, 140):
                im.putpixel((x, y), (255, 255, 255))
    b = io.BytesIO()
    im.save(b, "JPEG")
    return b.getvalue()


class FakeVLM:
    model = "fake-vlm"

    def __init__(self, text="A red wall."):
        self.calls, self.text, self.delay = [], text, 0.0

    async def describe(self, jpeg, prompt, timeout=60.0):
        self.calls.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.text


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def mgr(**cfg):
    base = {"enabled": True, "consent_acknowledged": True, "require_user_consent": False, "interval_s": 1.0}
    clock = Clock()
    sent = []

    async def send(m):
        sent.append(m)
    return pm.PerceptionManager({**base, **cfg}, FakeVLM(), send, clock=clock), clock, sent


def test_no_frames_accepted_without_consent_or_enable():
    for cfg, why in (({"enabled": False}, "rejected_consent"), ({"consent_acknowledged": False}, "rejected_consent"),
                     ({"require_user_consent": True}, "rejected_consent")):
        m, _, _ = mgr(**cfg)
        assert m.submit("camera", jpg()) == why and m.context() == ""


def test_frame_flow_context_and_rate_limit_and_unchanged():
    async def go():
        m, clock, sent = mgr()
        assert m.submit("camera", jpg()) == "accepted"
        await asyncio.sleep(0.05)
        assert "A red wall." in m.context() and "just now" in m.context()
        assert sent[-1]["type"] == "scene" and sent[-1]["source"] == "camera"
        assert m.submit("camera", jpg(mark=10)) == "dropped_rate"       # < interval
        clock.t += 2
        assert m.submit("camera", jpg()) == "dropped_unchanged"          # same scene, recent description
        clock.t += 2
        assert m.submit("camera", jpg(color=(10, 200, 10), mark=100)) == "accepted"   # scene changed
        await asyncio.sleep(0.05)
        assert len(m.vlm.calls) == 2
        clock.t += 200
        assert m.context() == ""                                          # too old: dropped from the prompt
        m.close()
    asyncio.run(go())


def test_stale_context_is_flagged_and_screen_source_labelled():
    async def go():
        m, clock, _ = mgr()
        m.submit("screen", jpg())
        await asyncio.sleep(0.05)
        clock.t += 100
        assert "shared screen" in m.context() and "outdated" in m.context()
        assert m.submit("webcam2", jpg()) == "rejected_source"
        m.close()
    asyncio.run(go())


def test_user_opt_in_out_and_forget():
    async def go():
        m, _, sent = mgr(require_user_consent=True)
        await m.handle_message({"type": "frame", "source": "camera", "jpeg_b64": base64.b64encode(jpg()).decode()})
        assert sent[-1]["type"] == "perception_status" and sent[-1]["enabled"] is False and m.vlm.calls == []
        await m.handle_message({"type": "perception", "enabled": True})
        assert sent[-1]["enabled"] is True
        await m.handle_message({"type": "frame", "source": "camera", "jpeg_b64": base64.b64encode(jpg()).decode()})
        await asyncio.sleep(0.05)
        assert m.context()
        await m.handle_message({"type": "perception", "enabled": False})
        assert m.context() == "" and not m._latest_jpeg                 # everything seen is dropped
        await m.handle_message({"type": "frame", "jpeg_b64": "not base64 !!"})  # garbage never raises
        m.close()
    asyncio.run(go())


def test_bad_and_oversized_frames_rejected():
    m, _, _ = mgr()
    assert m.submit("camera", b"") == "rejected_size"
    assert m.submit("camera", b"x" * (pm.MAX_FRAME_BYTES + 1)) == "rejected_size"
    assert m.submit("camera", b"definitely not a jpeg") == "rejected_image"


def test_gate_defers_description_while_agent_speaks():
    async def go():
        busy = {"v": True}
        m, _, _ = mgr()
        m.gate = lambda: busy["v"]
        m.submit("camera", jpg())
        await asyncio.sleep(0.4)
        assert m.vlm.calls == []          # waiting: never competes with the voice turn
        busy["v"] = False
        await asyncio.sleep(0.5)
        assert len(m.vlm.calls) == 1
        m.close()
    asyncio.run(go())


def test_perceptive_llm_injects_context_and_takes_fresh_look_when_asked():
    async def go():
        m, clock, _ = mgr()
        m.vlm.text = "A person holding a blue mug."
        m.submit("camera", jpg())
        await asyncio.sleep(0.05)
        seen = {}

        class Inner:
            async def stream(self, system, history, user):
                seen["system"] = system
                yield "ok"
        llm = pm.PerceptiveLLM(Inner(), m)
        assert [t async for t in llm.stream("base", [], "what do you see?")] == ["ok"]
        assert "blue mug" in seen["system"] and "base" in seen["system"]
        assert len(m.vlm.calls) == 2 and "what do you see" in m.vlm.calls[-1]   # fresh, question-focused look
        m.vlm.calls.clear()
        _ = [t async for t in llm.stream("base", [], "tell me about pricing")]
        assert m.vlm.calls == []                                              # no look for unrelated turns
        m.close()
    asyncio.run(go())


def test_wants_vision():
    assert pm.wants_vision("Can you see what I'm holding?") and pm.wants_vision("read this error on my screen")
    assert not pm.wants_vision("How much is the Pro plan?")


def test_look_without_frames_returns_empty_and_close_is_idempotent():
    async def go():
        m, _, _ = mgr()
        assert await m.look("what's this") == ""
        m.close(); m.close()
    asyncio.run(go())


# ---------------- API ----------------

@pytest.fixture()
def client():
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)

    def sess():
        with Session(db.engine) as s:
            yield s
    app.dependency_overrides[db.get_session] = sess
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_perception_config_requires_consent_ack(client):
    h = {"x-api-key": client.post("/v1/signup", json={"email": "p@q.com"}).json()["api_key"]}
    pid = client.post("/v1/personas", json={"name": "p", "system_prompt": "x"}, headers=h).json()["id"]
    g = client.get(f"/v1/personas/{pid}/perception", headers=h).json()
    assert g["enabled"] is False and g["store_frames"] is False and g["privacy"]["face_identification"] is False
    assert client.put(f"/v1/personas/{pid}/perception", json={"enabled": True}, headers=h).status_code == 422
    r = client.put(f"/v1/personas/{pid}/perception", json={"enabled": True, "consent_acknowledged": True, "interval_s": 2}, headers=h)
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["privacy"]["active"] and r.json()["require_user_consent"]
    assert client.put(f"/v1/personas/{pid}/perception", json={"interval_s": 0.1}, headers=h).status_code == 422
    h2 = {"x-api-key": client.post("/v1/signup", json={"email": "r@q.com"}).json()["api_key"]}
    assert client.get(f"/v1/personas/{pid}/perception", headers=h2).status_code == 404


def test_describe_endpoint_uses_vlm_and_validates(client, monkeypatch):
    h = {"x-api-key": client.post("/v1/signup", json={"email": "d@q.com"}).json()["api_key"]}

    async def fake(self, jpeg, prompt, timeout=60.0):
        self.last_ms = 12
        return f"seen:{len(jpeg) > 100}:{'screen' in prompt}"
    monkeypatch.setattr("app.perception.vlm.VLM.describe", fake)
    b64 = base64.b64encode(jpg()).decode()
    r = client.post("/v1/perception/describe", json={"jpeg_b64": b64, "source": "screen"}, headers=h)
    assert r.status_code == 200 and r.json()["text"].startswith("seen:True") and r.json()["model"]
    assert client.post("/v1/perception/describe", json={"jpeg_b64": "AAAA"}, headers=h).status_code == 422
    assert client.get("/v1/perception/models", headers=h).json()["models"]


# ---------------- language-aware reminder, tool rules ----------------

def test_detect_language_and_localised_reminder():
    d = lb.detect_language
    assert d("¿Cuánto cuesta el plan Pro?") == "es" and d("प्रो प्लान की कीमत कितनी है?") == "hi"
    assert d("Combien coûte le forfait ?") == "fr" and d("How much is the Pro plan?") == "en" and d("Hi") == "en"
    assert d("hello there", "Always speak and reply in Spanish, whatever language") == "es"   # forced language wins
    _, _, u = lb.GroundedLLM.prepare("sys\nRelevant knowledge. x", [], "¿Cuánto cuesta el plan Pro?")
    assert "Responde en español" in u and "fragmentos" in u
    _, _, u = lb.GroundedLLM.prepare("sys", [], "प्रो प्लान की कीमत कितनी है?")
    assert "हिंदी" in u


def test_feature_llm_adds_tool_rules_only_when_tools_exist():
    seen = []

    class B:
        async def chat(self, msgs, tools=None, options=None):
            seen.append(msgs[0]["content"])
            yield "text", "ok"

    async def run(tools):
        f = lb.FeatureLLM(B(), tools)
        return [t async for t in f.stream("base", [], "hi")]
    asyncio.run(run([lb.ToolSpec("t", "d", None, "http://x")]))
    asyncio.run(run([]))
    assert lb.TOOL_RULES in seen[0] and lb.TOOL_RULES not in seen[1]


def test_english_questions_with_short_words_stay_english():
    for q in ("How early should a new patient arrive?", "What is the late cancellation fee?", "do you ship to canada", "a"):
        assert lb.detect_language(q) == "en"


def test_select_hits_drops_clearly_worse_excerpts_only_for_dense():
    hits = [{"score": 0.80, "method": "dense"}, {"score": 0.75, "method": "dense"}, {"score": 0.50, "method": "dense"}]
    assert len(kb.select_hits(hits, 0.12)) == 2
    b = [{"score": 9.0, "method": "bm25"}, {"score": 1.0, "method": "bm25"}]
    assert kb.select_hits(b, 0.12) == b
