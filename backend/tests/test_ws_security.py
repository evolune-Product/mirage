"""WebSocket hardening: tickets, key-in-URL flag, connection/message limits, per-account session cap."""
import array

import pytest
from starlette.websockets import WebSocketDisconnect

from app import wsguard
from tests.test_realtime import env  # noqa: F401  (fixture: client, key, conversation id with fake providers)

LOUD = array.array("h", [8000, -8000] * 160).tobytes()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    wsguard.reset()
    from app.safety import limiter
    limiter.reset()
    for k in ("VOCALFACE_WS_CONNECT_IP", "VOCALFACE_WS_CONNECT_ACCOUNT", "VOCALFACE_WS_AUTH_FAIL_IP", "VOCALFACE_WS_MAX_SESSIONS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("VOCALFACE_ALLOW_KEY_IN_URL", raising=False)
    yield
    wsguard.reset()


def ticket(c, key, cid):
    r = c.post("/v1/realtime/ticket", json={"conversation_id": cid}, headers={"x-api-key": key})
    assert r.status_code == 200, r.text
    return r.json()


def close_code(c, url):
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect(url) as ws:
            ws.receive_json()
    return e.value.code


def test_ticket_connects_once_only(env):
    c, key, cid = env
    t = ticket(c, key, cid)
    assert t["ws_path"].endswith("ticket=" + t["ticket"]) and key not in t["ws_path"]
    with c.websocket_connect(t["ws_path"]) as ws:
        assert ws.receive_json()["type"] == "ready"
    assert close_code(c, t["ws_path"]) == 4401  # single use


def test_ticket_expired_wrong_conversation_and_other_account(env, monkeypatch):
    c, key, cid = env
    t = ticket(c, key, cid)
    assert close_code(c, f"/v1/conversations/c_other/stream?ticket={t['ticket']}") == 4401
    t2 = ticket(c, key, cid)
    wsguard._tickets[t2["ticket"]] = (wsguard._tickets[t2["ticket"]][0], cid, 0.0)
    assert close_code(c, t2["ws_path"]) == 4401
    k2 = c.post("/v1/signup", json={"email": "other@example.org"}).json()["api_key"]
    r = c.post("/v1/realtime/ticket", json={"conversation_id": cid}, headers={"x-api-key": k2})
    assert r.status_code == 404
    assert c.post("/v1/realtime/ticket", json={"conversation_id": cid}).status_code == 422  # header required


def test_key_in_url_refused_by_default_allowed_with_flag(env, monkeypatch):
    c, key, cid = env
    assert close_code(c, f"/v1/conversations/{cid}/stream?api_key={key}") == 4401
    monkeypatch.setenv("VOCALFACE_ALLOW_KEY_IN_URL", "1")
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        assert ws.receive_json()["type"] == "ready"


def test_ticket_not_in_logs(env):
    from app import settings
    assert "ticket=***" in settings.redact("GET /x/stream?ticket=wt_abcdefghijklmnop")


def test_connection_rate_limit_per_ip(env, monkeypatch):
    c, key, cid = env
    monkeypatch.setenv("VOCALFACE_WS_CONNECT_IP", "3/60")
    codes = [close_code(c, f"/v1/conversations/{cid}/stream?ticket=bad") for _ in range(5)]
    assert codes[:3] == [4401] * 3 and codes[3:] == [4429, 4429]


def test_auth_failure_budget(env, monkeypatch):
    c, key, cid = env
    monkeypatch.setenv("VOCALFACE_WS_AUTH_FAIL_IP", "2/60")
    codes = [close_code(c, f"/v1/conversations/{cid}/stream?ticket=bad") for _ in range(4)]
    assert codes == [4401, 4401, 4429, 4429]


def test_account_connection_rate(env, monkeypatch):
    c, key, cid = env
    monkeypatch.setenv("VOCALFACE_WS_CONNECT_ACCOUNT", "1/60")
    with c.websocket_connect(ticket(c, key, cid)["ws_path"]) as ws:
        assert ws.receive_json()["type"] == "ready"
    assert close_code(c, ticket(c, key, cid)["ws_path"]) == 4429


def test_concurrent_session_cap(env, monkeypatch):
    c, key, cid = env
    h = {"x-api-key": key}
    pid = c.get("/v1/personas", headers=h).json()[0]["id"]
    cid2 = c.post("/v1/conversations", json={"persona_id": pid}, headers=h).json()["id"]
    monkeypatch.setenv("VOCALFACE_WS_MAX_SESSIONS", "1")
    with c.websocket_connect(ticket(c, key, cid)["ws_path"]) as ws1:
        assert ws1.receive_json()["type"] == "ready"
        assert close_code(c, ticket(c, key, cid2)["ws_path"]) == 4429
    with c.websocket_connect(ticket(c, key, cid2)["ws_path"]) as ws:  # slot released after close
        assert ws.receive_json()["type"] == "ready"


def test_session_counter_refcounts_reconnect():
    assert wsguard.acquire_session("a", "c1") and wsguard.acquire_session("a", "c1")
    wsguard.release_session("a", "c1")
    assert wsguard.live_sessions("a") == 1
    wsguard.release_session("a", "c1")
    assert wsguard.live_sessions("a") == 0


def test_msg_guard_unit(monkeypatch):
    t = [0.0]
    monkeypatch.setenv("VOCALFACE_WS_MSG_RATE", "10/20")
    g = wsguard.MsgGuard(clock=lambda: t[0])
    assert all(g.check({"bytes": b"x" * 100}) is None for _ in range(20))
    assert g.check({"bytes": b"x"})[0] == 4429
    t[0] += 1.0  # refill 10
    assert g.check({"bytes": b"x"}) is None
    assert g.check({"bytes": b"x" * 70000})[0] == 1009
    assert g.check({"text": "x" * 1_000_001})[0] == 1009
    assert g.check({"type": "websocket.receive"}) is None


def _drain_to_close(ws):
    for _ in range(200):
        m = ws.receive()
        if m["type"] == "websocket.close":
            return m["code"]
    raise AssertionError("socket never closed")


def test_message_flood_closes_socket(env, monkeypatch):
    c, key, cid = env
    monkeypatch.setenv("VOCALFACE_WS_MSG_RATE", "1/5")
    with c.websocket_connect(ticket(c, key, cid)["ws_path"]) as ws:
        assert ws.receive_json()["type"] == "ready"
        for _ in range(20):
            ws.send_bytes(LOUD)
        assert _drain_to_close(ws) == 4429


def test_oversize_frame_closes_socket(env):
    c, key, cid = env
    with c.websocket_connect(ticket(c, key, cid)["ws_path"]) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(b"\0" * 100_000)
        assert _drain_to_close(ws) == 1009
