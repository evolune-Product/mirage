import array
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db
from app.main import app
from app.pipeline import session as sess_mod
from app.pipeline.session import Providers

LOUD = array.array("h", [8000, -8000] * 160).tobytes()  # 20 ms
QUIET = bytes(640)


class FakeSTT:
    async def transcribe(self, pcm, sr=16000):
        return "hello there"


class FakeLLM:
    cancelled = False
    seen = None

    async def stream(self, system, history, user):
        FakeLLM.seen = (system, list(history), user)
        try:
            for w in ["Hi, ", "this is ", "a test. ", "Second ", "sentence ", "here. ", "Third ", "one. "]:
                await asyncio.sleep(0.05)
                yield w
        except asyncio.CancelledError:
            FakeLLM.cancelled = True
            raise


class FakeTTS:
    async def synthesize(self, text, voice="default"):
        await asyncio.sleep(0.05)
        yield b"\x01\x00" * 2400


@pytest.fixture()
def env():
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    sess_mod.set_provider_factory(lambda spec="": Providers(FakeSTT(), FakeLLM(), FakeTTS()))
    FakeLLM.cancelled = False
    c = TestClient(app)
    key = c.post("/v1/signup", json={"email": "a@b.c"}).json()["api_key"]
    h = {"x-api-key": key}
    p = c.post("/v1/personas", json={"name": "P", "system_prompt": "You are Sam.", "knowledge": "Sky is blue."}, headers=h).json()
    cid = c.post("/v1/conversations", json={"persona_id": p["id"]}, headers=h).json()["id"]
    yield c, key, cid
    app.dependency_overrides.clear()
    sess_mod.set_provider_factory(None)


def speak(ws, ms=600, silence_ms=800):
    for _ in range(ms // 20):
        ws.send_bytes(LOUD)
    for _ in range(silence_ms // 20):
        ws.send_bytes(QUIET)


def collect(ws, until):
    msgs = []
    while True:
        m = ws.receive()
        msgs.append(m)
        if m.get("text") and until in m["text"]:
            return msgs


def test_auth_rejected(env):
    c, key, cid = env
    with pytest.raises(Exception):
        with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key=bad"):
            pass
    with pytest.raises(Exception):
        with c.websocket_connect(f"/v1/conversations/c_nope/stream?api_key={key}"):
            pass


def test_full_turn_streams_audio_and_meters(env):
    c, key, cid = env
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        assert ws.receive_json()["type"] == "ready"
        speak(ws)
        msgs = collect(ws, "agent_done")
    audio = [m for m in msgs if m.get("bytes")]
    assert len(audio) >= 3 and all(len(m["bytes"]) == 4800 for m in audio)
    texts = [m["text"] for m in msgs if m.get("text")]
    assert any('"role":"user"' in t.replace(" ", "") and "hello there" in t for t in texts)
    assert "Sky is blue." in FakeLLM.seen[0] and "You are Sam." in FakeLLM.seen[0]
    with Session(db.engine) as s:
        assert s.get(db.Conversation, cid).seconds_used >= 1


def test_barge_in_cancels_reply(env):
    c, key, cid = env
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json()
        speak(ws)
        while not ws.receive().get("bytes"):
            pass  # agent is speaking
        for _ in range(10):  # user talks over the agent
            ws.send_bytes(LOUD)
        collect(ws, "interrupted")
    assert FakeLLM.cancelled


def test_explicit_interrupt_message(env):
    c, key, cid = env
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json()
        speak(ws)
        while not ws.receive().get("bytes"):
            pass
        ws.send_json({"type": "interrupt"})
        collect(ws, "interrupted")


def test_playground_served(env):
    c, _, _ = env
    r = c.get("/v1/playground")
    assert r.status_code == 200 and "AudioWorklet" in r.text


def _run_turn(lip):
    import asyncio
    from app.pipeline.session import Session

    events = []

    async def sj(m): events.append(("json", m["type"]))
    async def sb(b): events.append(("bytes", len(b)))

    class Stt:
        async def transcribe(self, pcm, sr): return "hello"

    class Llm:
        async def stream(self, system, history, user):
            yield "Hello there."

    class Tts:
        async def synthesize(self, text, voice):
            yield b"\x01\x00" * 4800

    prov = type("P", (), {"stt": Stt(), "llm": Llm(), "tts": Tts()})()
    sess = Session(prov, "sys", "v", sj, sb, lipsync=lip)
    asyncio.run(sess._reply(b"\x00\x00" * 100))
    return events, sess


class _Lip:
    def __init__(self, fail=False): self.fail = fail
    async def render(self, pcm):
        if self.fail: raise RuntimeError("gpu box down")
        return {"fps": 25.0, "frames": ["AAAA"] * 3}


def test_video_segment_is_sent_before_its_audio():
    events, _ = _run_turn(_Lip())
    kinds = [e for e in events if e[0] == "bytes" or e[1] == "video_segment"]
    assert kinds[0] == ("json", "video_segment") and kinds[1][0] == "bytes"


def test_lipsync_failure_does_not_break_the_voice_turn():
    events, sess = _run_turn(_Lip(fail=True))
    assert any(e[0] == "bytes" for e in events) and ("json", "agent_done") in events
    assert sess.lipsync is None  # disabled after the failure, voice continues


# ---------------- latency work: VAD, endpointing, chunking, lip-sync pieces ----------------
import numpy as np  # noqa: E402
from pathlib import Path  # noqa: E402

from app.pipeline.local import sentences  # noqa: E402
from app.pipeline.turn_taking import EnergyVAD, SileroVAD, TurnTaker, utterance_complete  # noqa: E402

SPEECH = (Path(__file__).parent / "fixtures" / "speech_16k.pcm").read_bytes()


def _frames(b, n=640):
    return [b[i:i + n] for i in range(0, len(b) // n * n, n)]


def test_silero_accepts_speech_rejects_noise_that_fools_energy_gate():
    rng = np.random.default_rng(0)
    hiss = np.clip(rng.normal(0, 1500, 16000 * 3), -32768, 32767).astype(np.int16).tobytes()
    def speech_frames(vad, pcm):
        tk = TurnTaker(vad=vad)
        return sum(tk.push(f) == "speech" for f in _frames(pcm))
    assert speech_frames(SileroVAD(), SPEECH) > 40
    assert speech_frames(SileroVAD(), hiss) == 0
    assert speech_frames(EnergyVAD(500.0), hiss) > 100  # why the energy gate was replaced


def test_turntaker_pause_then_end_and_force_end():
    tk = TurnTaker(end_of_turn_ms=600, pause_ms=100)
    for _ in range(10):
        assert tk.push(LOUD) == "speech"
    evs = [tk.push(QUIET) for _ in range(40)]
    assert evs.count("pause") == 1 and evs.index("pause") == 4 and evs[29] == "end_of_turn"
    tk2 = TurnTaker(end_of_turn_ms=600)
    tk2.push(LOUD)
    assert tk2.force_end() == "end_of_turn" and not tk2.s.speaking


def test_utterance_complete():
    assert utterance_complete("How much does it cost?")
    assert not utterance_complete("I want to know about...")
    assert not utterance_complete("and then the")
    assert not utterance_complete("Tell me about the, ")
    assert not utterance_complete("so what is the.")


def test_chunker_short_first_chunk_then_sentences():
    async def toks(t):
        for w in t.split(" "):
            yield w + " "
    async def run(t):
        return [x async for x in sentences(toks(t))]
    out = asyncio.run(run("Sure, absolutely. The starter plan costs 19.99 dollars per month. Dr. Smith agrees!"))
    assert out == ["Sure, absolutely.", "The starter plan costs 19.99 dollars per month.", "Dr. Smith agrees!"]
    out = asyncio.run(run("Well the starter plan is nineteen dollars a month and pro is seventy nine dollars."))
    assert len(out[0].split()) == 7 and " ".join(out) == "Well the starter plan is nineteen dollars a month and pro is seventy nine dollars."


def test_early_commit_uses_speculative_transcript(monkeypatch):
    monkeypatch.setenv("VOCALFACE_VAD", "energy")  # synthetic square waves are not speech to Silero
    calls = []

    class Stt:
        async def transcribe(self, pcm, sr=16000):
            calls.append(len(pcm))
            return "Is this working?"

    events = []

    async def sj(m): events.append(m["type"])
    async def sb(b): events.append("audio")

    prov = Providers(Stt(), FakeLLM(), FakeTTS())
    from app.pipeline.session import Session as S
    sess = S(prov, "sys", "v", sj, sb, end_of_turn_ms=2000)  # hard timeout far away: only early commit can end the turn

    async def go():
        for _ in range(30):
            await sess.feed(LOUD)
        for _ in range(60):  # 1.2 s of silence
            await sess.feed(QUIET)
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.5)
        await sess.close()
    asyncio.run(go())
    assert "agent_done" in events and len(calls) == 1  # one STT call, reused (no second pass at end of turn)
    assert sess.metrics.get("early_commit") == 1


def test_resumed_speech_drops_speculative_transcript(monkeypatch):
    monkeypatch.setenv("VOCALFACE_VAD", "energy")
    class Stt:
        n = 0
        async def transcribe(self, pcm, sr=16000):
            Stt.n += 1
            return "Hello."
    async def sj(m): pass
    async def sb(b): pass
    from app.pipeline.session import Session as S
    sess = S(Providers(Stt(), FakeLLM(), FakeTTS()), "sys", "v", sj, sb)

    async def go():
        for _ in range(30): await sess.feed(LOUD)
        for _ in range(10): await sess.feed(QUIET)   # 200 ms pause -> speculation starts
        assert sess._spec is not None
        await sess.feed(LOUD)                        # user continues
        assert sess._spec is None
        await sess.close()
    asyncio.run(go())


def test_lipsync_tiny_piece_is_padded_and_phase_passed():
    seen = []

    class Lip:
        async def render(self, pcm, phase=None, fade_in=False):
            seen.append((len(pcm), phase, fade_in))
            return {"fps": 25.0, "frames": ["A"] * 25, "end_phase": 7}

    events = []
    async def sj(m): events.append(m)
    async def sb(b): events.append(len(b))
    prov = type("P", (), {"stt": FakeSTT(), "llm": FakeLLM(), "tts": FakeTTS()})()
    sess = Session.__mro__[0] if False else None
    from app.pipeline.session import Session as S
    sess = S(prov, "s", "v", sj, sb, lipsync=Lip())
    sess.set_idle_phase(10, 25.0)
    asyncio.run(sess._emit_audio(b"\x01\x00" * 2400))  # 0.1 s chunk: shorter than the model minimum
    assert seen[0][0] >= int(0.3 * 24000) * 2 and seen[0][1] >= 10 and seen[0][2] is True
    seg = [e for e in events if isinstance(e, dict)][0]
    assert len(seg["frames"]) == 2 and seg["end_phase"] == 7  # trimmed back to the real audio length


def test_fallback_tts_uses_secondary_when_primary_fails_before_audio():
    from app.pipeline.mlx_tts import FallbackTTS

    class Bad:
        async def synthesize(self, t, v="x"):
            raise RuntimeError("sidecar down")
            yield b""

    class Good:
        async def synthesize(self, t, v="x"):
            yield b"ok"

    f = FallbackTTS(Bad(), lambda: Good())
    async def go(): return [c async for c in f.synthesize("hi")]
    assert asyncio.run(go()) == [b"ok"] and f.broken


def test_idle_loop_fetch_is_background_and_failure_disables_face():
    from app.routers.realtime import send_idle_loop

    sent = []

    class WS:
        async def send_json(self, m): sent.append(m["type"])

    class Slow:
        async def idle(self):
            await asyncio.sleep(0.3)
            return {"fps": 25, "frames": ["A"]}

    class Boom:
        async def idle(self): raise RuntimeError("cold start failed")

    class Sess: lipsync = "x"

    async def go():
        t = asyncio.create_task(send_idle_loop(WS(), Sess, Slow()))
        await asyncio.sleep(0.05)
        assert not t.done() and sent == []  # caller was free to keep reading the mic meanwhile
        await t
        s2 = type("S", (), {"lipsync": "x"})
        await send_idle_loop(WS(), s2, Boom())
        return s2
    s2 = asyncio.run(go())
    assert sent == ["idle_loop"] and s2.lipsync is None
