"""Admission control: limits, busy close, face/voice budgets, reconnect accounting, guest sessions, metrics."""
import asyncio
import array
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import admission, db
from app.main import app
from app.pipeline import session as sess_mod
from app.pipeline.session import Providers

from .test_realtime import FakeLLM, FakeSTT, FakeTTS, collect, speak


def run(c):
    return asyncio.run(c)


# ------------------------------------------------------------------ pure controller
def test_total_limit_and_release():
    a = admission.Admission(max_total=2, max_face=0, queue_s=0)
    d1 = run(a.admit("c1", False)); d2 = run(a.admit("c2", False)); d3 = run(a.admit("c3", False))
    assert d1.granted and d2.granted and not d3.granted
    assert d3.retry_after_s >= 1 and "busy" in d3.reason
    a.release(d1.ticket)
    assert run(a.admit("c3", False)).granted
    assert a.snapshot()["rejected"] == 1 and a.snapshot()["current"] == 2


def test_face_budget_degrades_to_voice_or_rejects():
    a = admission.Admission(max_total=4, max_face=1, face_overflow="voice")
    f1 = run(a.admit("f1", True)); f2 = run(a.admit("f2", True))
    assert f1.granted and f1.face and f2.granted and not f2.face and f2.degraded  # second face -> voice-only
    assert a.face_current == 1 and a.voice_current == 1
    b = admission.Admission(max_total=4, max_face=1, face_overflow="reject")
    run(b.admit("f1", True))
    assert not run(b.admit("f2", True)).granted
    assert run(b.admit("v1", False)).granted  # voice-only sessions are not blocked by a full face budget


def test_total_limit_applies_to_face_sessions_too():
    a = admission.Admission(max_total=2, max_face=2)
    assert run(a.admit("a", True)).face and run(a.admit("b", True)).face
    assert not run(a.admit("c", True)).granted and not run(a.admit("d", False)).granted


def test_reconnect_same_cid_is_not_counted_twice_and_old_release_is_harmless():
    a = admission.Admission(max_total=1, max_face=1)
    old = run(a.admit("c1", True))
    new = run(a.admit("c1", True))  # the replacing socket
    assert new.granted and new.face and a.current == 1
    a.release(old.ticket)  # the replaced socket tears down late: must not free the new socket's slot
    assert a.current == 1 and not run(a.admit("c2", False)).granted
    a.release(new.ticket)
    assert a.current == 0


def test_unlimited_when_zero():
    a = admission.Admission(max_total=0, max_face=0)
    assert all(run(a.admit(f"c{i}", i % 2 == 0)).granted for i in range(50))


def test_queue_waits_for_a_slot_then_times_out():
    async def go():
        a = admission.Admission(max_total=1, max_face=0, queue_s=1.0)
        d1 = await a.admit("c1", False)
        seen = []

        async def q(pos, w): seen.append(pos)
        async def free_later():
            await asyncio.sleep(0.3); a.release(d1.ticket)
        asyncio.create_task(free_later())
        d2 = await a.admit("c2", False, on_queued=q)
        assert d2.granted and d2.waited_s >= 0.25 and seen == [1]
        t0 = asyncio.get_running_loop().time()
        d3 = await a.admit("c3", False)  # nobody frees a slot: refused after queue_s
        assert not d3.granted and asyncio.get_running_loop().time() - t0 >= 0.9
        assert a.snapshot()["queued"] == 0
    asyncio.run(go())


def test_queue_is_fifo():
    async def go():
        a = admission.Admission(max_total=1, max_face=0, queue_s=3.0)
        d0 = await a.admit("c0", False)
        order = []

        async def waiter(name):
            d = await a.admit(name, False)
            order.append(name)
            await asyncio.sleep(0.2)
            a.release(d.ticket)
        t1 = asyncio.create_task(waiter("w1")); await asyncio.sleep(0.05)
        t2 = asyncio.create_task(waiter("w2")); await asyncio.sleep(0.05)
        a.release(d0.ticket)
        await asyncio.gather(t1, t2)
        assert order == ["w1", "w2"]
    asyncio.run(go())


def test_prometheus_and_capacity_report():
    a = admission.reset(max_total=3, max_face=1)
    try:
        run(a.admit("c1", True)); run(a.admit("c2", False))
        txt = "\n".join(admission.prometheus_lines())
        assert 'vocalface_conversations_live{kind="face"} 1' in txt and 'vocalface_conversations_live{kind="voice"} 1' in txt
        assert 'vocalface_conversations_limit{kind="total"} 3' in txt and "vocalface_process_open_fds" in txt
        rep = admission.capacity_report()
        assert rep["current"] == 2 and rep["limit"] == 3 and rep["accepting"] and "process" in rep
    finally:
        admission.reset()


# ------------------------------------------------------------------ over the real WebSocket route
@pytest.fixture()
def env():
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    sess_mod.set_provider_factory(lambda spec="": Providers(FakeSTT(), FakeLLM(), FakeTTS()))
    c = TestClient(app)
    key = c.post("/v1/signup", json={"email": "a@b.c"}).json()["api_key"]
    h = {"x-api-key": key}
    p = c.post("/v1/personas", json={"name": "P", "system_prompt": "You are Sam."}, headers=h).json()
    cids = [c.post("/v1/conversations", json={"persona_id": p["id"]}, headers=h).json()["id"] for _ in range(3)]
    yield c, key, cids, p
    app.dependency_overrides.clear()
    sess_mod.set_provider_factory(None)
    admission.reset()


def test_websocket_busy_message_and_1013(env):
    c, key, cids, _ = env
    admission.reset(max_total=1, max_face=0, retry_s=7)
    with c.websocket_connect(f"/v1/conversations/{cids[0]}/stream?api_key={key}") as ws1:
        assert ws1.receive_json()["type"] == "ready"
        with c.websocket_connect(f"/v1/conversations/{cids[1]}/stream?api_key={key}") as ws2:
            m = ws2.receive_json()
            assert m["type"] == "busy" and m["limit"] == 1 and m["current"] == 1 and 5 <= m["retry_after_s"] <= 9
            close = ws2.receive()
            assert close["type"] == "websocket.close" and close["code"] == 1013
            assert "retry in" in close["reason"]
        assert admission.CONTROLLER.current == 1  # the refused connection holds no slot
    assert admission.CONTROLLER.current == 0  # and the first one released its slot on disconnect
    with c.websocket_connect(f"/v1/conversations/{cids[1]}/stream?api_key={key}") as ws3:  # free again
        assert ws3.receive_json()["type"] == "ready"


def test_slot_released_after_many_connect_disconnect_cycles(env):
    c, key, cids, _ = env
    admission.reset(max_total=1, max_face=0)
    for _ in range(12):
        with c.websocket_connect(f"/v1/conversations/{cids[0]}/stream?api_key={key}") as ws:
            assert ws.receive_json()["type"] == "ready"
    assert admission.CONTROLLER.current == 0 and admission.CONTROLLER.snapshot()["rejected"] == 0


def test_reconnect_while_old_socket_still_open_keeps_one_slot(env):
    c, key, cids, _ = env
    admission.reset(max_total=1, max_face=0)
    with c.websocket_connect(f"/v1/conversations/{cids[0]}/stream?api_key={key}") as old:
        assert old.receive_json()["type"] == "ready"
        with c.websocket_connect(f"/v1/conversations/{cids[0]}/stream?api_key={key}") as new:
            assert new.receive_json()["type"] == "ready"  # same conversation: replaces the old socket, not refused
    assert admission.CONTROLLER.current == 0


def test_face_session_degrades_to_voice_when_face_budget_full(env, tmp_path, monkeypatch):
    c, key, cids, p = env
    from app import jobs

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    for rid in ("r_face1",):
        (tmp_path / "replicas" / rid).mkdir(parents=True)
        (tmp_path / "replicas" / rid / "source.mp4").write_bytes(b"x")
    with Session(db.engine) as s:
        pp = s.get(db.Persona, p["id"]); pp.replica_id = "r_face1"; s.add(pp); s.commit()
    from app.pipeline import lipsync as ls

    class Up:
        def __init__(self, rid): self.rid = rid
        @staticmethod
        async def available(url=None): return True
        async def prepare(self): return {}
        async def idle(self): return {"fps": 25, "frames": ["QQ=="]}
        async def render(self, pcm, **kw): return {"fps": 25, "frames": ["QQ=="] * 3}
    monkeypatch.setattr(ls, "LipsyncClient", Up)
    admission.reset(max_total=3, max_face=1, face_overflow="voice")
    with c.websocket_connect(f"/v1/conversations/{cids[0]}/stream?api_key={key}") as w1:
        r1 = w1.receive_json()
        with c.websocket_connect(f"/v1/conversations/{cids[1]}/stream?api_key={key}") as w2:
            r2 = w2.receive_json()
    assert r1["type"] == "ready" and r1["live_face"] is True
    assert r2["type"] == "ready" and r2["live_face"] is False  # second face session continues voice-only
    assert admission.CONTROLLER.snapshot()["degraded"] == 1


def test_health_capacity_and_deep_and_metrics(env):
    c, key, cids, _ = env
    admission.reset(max_total=1, max_face=1)
    with c.websocket_connect(f"/v1/conversations/{cids[0]}/stream?api_key={key}") as ws:
        ws.receive_json()
        r = c.get("/health/capacity")
        assert r.status_code == 503 and r.json()["current"] == 1 and r.json()["accepting"] is False
        deep = c.get("/health/deep").json()["checks"]["capacity"]
        assert deep["current"] == 1 and deep["limit"] == 1 and "process" in deep
        assert 'vocalface_conversations_live{kind="voice"} 1' in c.get("/metrics").text
    assert c.get("/health/capacity").status_code == 200


def test_guest_busy_does_not_end_the_guest_session(env):
    c, key, cids, p = env
    h = {"x-api-key": key}
    link = c.post(f"/v1/personas/{p['id']}/share", json={"max_seconds": 60}, headers=h).json()
    tok = link["token"]
    g = c.post(f"/v1/guest/{tok}/conversations").json()
    admission.reset(max_total=1, max_face=0)
    with c.websocket_connect(f"/v1/conversations/{cids[0]}/stream?api_key={key}") as owner:
        owner.receive_json()
        with c.websocket_connect(g["ws_path"]) as gw:
            assert gw.receive_json()["type"] == "busy"  # guests share the same limits
    with Session(db.engine) as s:
        assert s.get(db.Conversation, g["conversation_id"]).status == "active"  # a refusal is not the end of their link
    with c.websocket_connect(g["ws_path"]) as gw:  # retry after the owner left
        assert gw.receive_json()["type"] == "ready"
