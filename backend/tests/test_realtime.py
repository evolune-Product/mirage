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
