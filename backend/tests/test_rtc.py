"""Realtime-client work: echo reference, transport negotiation, heartbeat, resume, push-to-talk, frame forwarding."""
import base64
import io

import numpy as np

from app import rtc_transport as T
from app.pipeline.echo import EchoReference

from .test_realtime import FakeLLM, QUIET, LOUD, collect, env, speak  # noqa: F401


def _voice(n, seed=1):
    r = np.random.default_rng(seed)
    env_ = np.abs(np.sin(np.arange(n) / 14.0)) * 3000 + 200
    return (r.normal(0, 1, n * 480) * np.repeat(env_, 480)).astype(np.int16)


def _drive(ref, agent24, mic_rms):
    ref.add_agent(agent24.tobytes())
    out = []
    for r in mic_rms:
        ref.push_mic(r)
        out.append(ref.assess()[0])
    return out


def test_echo_reference_explains_delayed_attenuated_echo():
    a = _voice(150)
    env_ = np.sqrt((a.astype(float).reshape(150, 480) ** 2).mean(axis=1))
    mic = np.concatenate([np.full(12, 100.0), env_ * 0.15 + 100])[:150]  # 240 ms delay, -16 dB
    ex = _drive(EchoReference(), a, mic)
    assert sum(ex[60:140]) > 0.9 * 80


def test_echo_reference_detects_user_on_top_of_echo():
    a = _voice(150)
    env_ = np.sqrt((a.astype(float).reshape(150, 480) ** 2).mean(axis=1))
    mic = np.concatenate([np.full(12, 100.0), env_ * 0.15 + 100])[:150]
    mic[90:] += 2500  # user starts talking loudly at frame 90
    ex = _drive(EchoReference(), a, mic)
    assert not any(ex[95:140])


def test_video_pack_roundtrip_and_recode_shrinks():
    from PIL import Image
    r = np.random.default_rng(0)
    im = Image.fromarray((r.integers(0, 255, (64, 96, 3))).astype("uint8"))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=92)
    j = buf.getvalue()
    blob = T.pack_video("video_segment", 25, [j, j], end_phase=7)
    head, frames = T.unpack_video(blob)
    assert head["fps"] == 25 and head["end_phase"] == 7 and frames == [j, j]
    assert len(T.recode([j], 1)[0]) < len(j) and len(T.recode([j], 0)[0]) == len(j)
    small = Image.open(io.BytesIO(T.recode([j], 3)[0]))
    assert small.size[0] < 96


def test_link_adapts_tier_down_fast_up_slow():
    l = T.Link(); l.hello = True
    assert l.note_stats(900) == T.DEFAULT_TIER + 1
    t = l.tier
    for _ in range(7):
        l.note_stats(10)
    assert l.tier == t
    l.note_stats(10)
    assert l.tier == t - 1
    l.bw_bps = 1_000_000  # 1 Mbps cannot carry tier 1 (3.5 Mbps) at 60% utilisation
    l.tier = 1
    assert l.note_stats(0) == 2


def test_hello_ping_and_tagged_audio(env):
    c, key, cid = env
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.send_json({"type": "hello", "framing": "tagged"})
        ws.send_json({"type": "ping", "t": 5})
        seen = {}
        while not {"ready", "hello_ack", "pong"} <= set(seen):
            m = ws.receive_json(); seen[m["type"]] = m
        assert seen["hello_ack"]["framing"] == "tagged" and seen["pong"]["t"] == 5
        speak(ws)
        got = None
        for _ in range(200):
            m = ws.receive()
            if m.get("bytes"):
                got = m["bytes"]; break
        assert got and got[0] == T.TAG_AUDIO


def test_legacy_client_unchanged(env):
    c, key, cid = env
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        assert ws.receive_json()["type"] == "ready"
        speak(ws)
        for _ in range(200):
            m = ws.receive()
            if m.get("bytes"):
                assert m["bytes"][:2] == b"\x01\x00"  # raw PCM, no tag byte
                return
    raise AssertionError("no audio")


def test_reconnect_resumes_history_without_regreeting(env):
    from sqlmodel import Session as S
    from app import db
    c, key, cid = env
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.send_json({"type": "hello", "framing": "tagged"})
        speak(ws); collect(ws, "agent_done")  # no 'end': a dropped connection
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}&resume=1") as ws:
        ws.send_json({"type": "hello", "framing": "tagged", "resume": True})
        r = {}
        while "ready" not in r:
            m = ws.receive_json(); r[m["type"]] = m
        assert r["ready"]["resumed"] is True
        speak(ws); collect(ws, "agent_done")
    assert FakeLLM.seen[1][0]["content"] == "hello there"  # history restored from the stored transcript


def test_ptt_turn_and_idle_frames_ignored(env):
    c, key, cid = env
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json()
        ws.send_json({"type": "ptt", "state": "on"})
        for _ in range(30):
            ws.send_bytes(LOUD)  # not held: ignored, no turn
        ws.send_json({"type": "ptt", "state": "down"})
        for _ in range(30):
            ws.send_bytes(LOUD)
        ws.send_json({"type": "ptt", "state": "up"})
        msgs = collect(ws, "agent_done")
        assert any('"speech_start"' in (m.get("text") or "") for m in msgs)


def test_frame_message_forwarded_to_perception_hook(env, monkeypatch):
    import time
    import app.perception as P
    c, key, cid = env
    got = []

    class PM:
        async def handle_message(self, m):
            got.append(m["source"])

        def close(self):
            pass

    monkeypatch.setattr(P, "attach", lambda *a, **k: PM())
    with c.websocket_connect(f"/v1/conversations/{cid}/stream?api_key={key}") as ws:
        ws.receive_json()
        ws.send_json({"type": "frame", "source": "camera", "jpeg_b64": base64.b64encode(b"x").decode()})
        time.sleep(0.5)
    assert got == ["camera"]
