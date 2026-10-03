"""Chaos-style resilience: dead/slow/failing STT, LLM, TTS and lip-sync service; the lip-sync scheduler's priority,
fairness, cancellation and backpressure. Fakes only (no models, no network)."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

from app.pipeline import session as S
from app.pipeline.session import Session

PCM = bytes(32000)  # 1 s of "speech" handed straight to _reply (the fake STT ignores it)


class STT:
    def __init__(self, fail=0): self.fail = fail
    async def transcribe(self, pcm, sr=16000):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("stt boom")
        return "hello there"


class LLM:
    def __init__(self, mode="ok"): self.mode = mode
    async def stream(self, system, history, user):
        if self.mode == "down":
            raise ConnectionError("ollama unreachable")
        if self.mode == "hang":
            await asyncio.sleep(3600)
        if self.mode == "stall":
            yield "One "
            await asyncio.sleep(3600)
        for w in ["Hi, ", "this is ", "a test. "]:
            yield w


class TTS:
    def __init__(self, fail_after=None, always=False):
        self.n, self.fail_after, self.always = 0, fail_after, always
    async def synthesize(self, text, voice="default"):
        self.n += 1
        if self.always or (self.fail_after is not None and self.n > self.fail_after):
            raise RuntimeError("tts boom")
        yield b"\x01\x00" * 2400


def mk(stt=None, llm=None, tts=None, lip=None):
    ev = []

    async def sj(m): ev.append(m)
    async def sb(b): ev.append(len(b))
    prov = type("P", (), {"stt": stt or STT(), "llm": llm or LLM(), "tts": tts or TTS()})()
    return Session(prov, "sys", "v", sj, sb, lipsync=lip), ev


def types(ev): return [e["type"] for e in ev if isinstance(e, dict)]
def audio(ev): return [e for e in ev if isinstance(e, int)]


def test_ollama_down_is_spoken_error_not_a_hang():
    s, ev = mk(llm=LLM("down"))
    asyncio.run(asyncio.wait_for(s._reply(PCM), 5))
    t = types(ev)
    assert "error" in t and t[-1] == "agent_done" and audio(ev)  # error event + an audible apology + turn closed
    assert any(isinstance(e, dict) and e.get("type") == "transcript" and e.get("role") == "agent" and "trouble" in e["text"] for e in ev)
    assert s.history[-1]["role"] == "user" and len(s.history) == 1  # the apology is not remembered as an answer


def test_ollama_hang_times_out(monkeypatch):
    monkeypatch.setattr(S, "LLM_FIRST_TOKEN_S", 0.3)
    s, ev = mk(llm=LLM("hang"))
    t0 = time.monotonic()
    asyncio.run(asyncio.wait_for(s._reply(PCM), 5))
    assert time.monotonic() - t0 < 3 and "error" in types(ev) and types(ev)[-1] == "agent_done" and audio(ev)
    assert any("no first token" in e.get("message", "") for e in ev if isinstance(e, dict))


def test_ollama_stall_mid_reply_times_out(monkeypatch):
    monkeypatch.setattr(S, "LLM_STALL_S", 0.3)
    s, ev = mk(llm=LLM("stall"))
    asyncio.run(asyncio.wait_for(s._reply(PCM), 5))
    assert "error" in types(ev) and types(ev)[-1] == "agent_done"


def test_stt_exception_asks_to_repeat_and_next_turn_works():
    stt = STT(fail=1)
    s, ev = mk(stt=stt)
    asyncio.run(s._reply(PCM))
    assert "error" in types(ev) and types(ev)[-1] == "agent_done" and audio(ev)
    assert any(isinstance(e, dict) and "say it again" in e.get("text", "") for e in ev)
    ev.clear()
    asyncio.run(s._reply(PCM))  # the session is still healthy
    assert "error" not in types(ev) and types(ev)[-1] == "agent_done"


def test_tts_exception_closes_the_turn_and_session_survives():
    tts = TTS(always=True)
    s, ev = mk(tts=tts)
    asyncio.run(asyncio.wait_for(s._reply(PCM), 5))
    assert "error" in types(ev) and types(ev)[-1] == "agent_done" and not audio(ev)  # nothing to play, but no hang
    tts.always = False
    ev.clear()
    asyncio.run(s._reply(PCM))
    assert audio(ev) and "error" not in types(ev)


def test_tts_dies_after_first_sentence_still_ends_cleanly():
    s, ev = mk(tts=TTS(fail_after=1))
    asyncio.run(asyncio.wait_for(s._reply(PCM), 5))
    assert types(ev)[-1] == "agent_done"


# ------------------------------------------------------------------ lip-sync service killed mid-conversation
class FlakyLip:
    def __init__(self): self.up, self.calls, self.health_calls = True, 0, 0
    async def render(self, pcm, phase=None, fade_in=False, piece=None):
        self.calls += 1
        if not self.up:
            raise ConnectionError("lipsync dead")
        return {"fps": 25.0, "frames": ["A"] * 8, "end_phase": 1}
    async def health(self):
        self.health_calls += 1
        if not self.up:
            raise ConnectionError("still dead")
        return {"ok": True}


def test_lipsync_killed_mid_call_voice_continues_then_face_returns(monkeypatch):
    monkeypatch.setattr(S, "LIPSYNC_RETRY_S", 0.0)
    lip = FlakyLip()
    s, ev = mk(lip=lip)

    async def go():
        await s._reply(PCM)
        assert any(e["type"] == "video_segment" for e in ev if isinstance(e, dict)) and s.lipsync is lip
        lip.up = False
        ev.clear()
        for _ in range(S.LIPSYNC_MAX_FAILS + 1):  # a few turns while the service is dead: audio never stops
            await s._reply(PCM)
        assert audio(ev) and s.lipsync is None and types(ev).count("agent_done") == S.LIPSYNC_MAX_FAILS + 1
        assert "error" not in types(ev)
        calls = lip.calls
        await s._reply(PCM)  # still dead: probed, stays off, no more render attempts
        assert s.lipsync is None and lip.calls == calls and lip.health_calls >= 1
        lip.up = True
        ev.clear()
        await s._reply(PCM)  # service is back: face re-enabled at the start of the next reply
        assert s.lipsync is lip and any(e["type"] == "video_segment" for e in ev if isinstance(e, dict))
    asyncio.run(go())


def test_lipsync_timeout_switches_face_off_at_once(monkeypatch):
    class Hang(FlakyLip):
        async def render(self, pcm, phase=None, fade_in=False, piece=None):
            raise asyncio.TimeoutError()
    lip = Hang()
    s, ev = mk(lip=lip)
    asyncio.run(s._reply(PCM))
    assert s.lipsync is None and audio(ev) and types(ev)[-1] == "agent_done"


def test_unbounded_monologue_buffer_is_capped(monkeypatch):
    monkeypatch.setattr(S, "MAX_UTTERANCE_S", 2.0)
    s, _ = mk()
    import array
    loud = array.array("h", [8000, -8000] * 160).tobytes()

    async def go():
        for _ in range(600):  # 12 s of continuous speech
            await s.feed(loud)
    asyncio.run(go())
    assert len(s._utt) * 20 <= 2200


# ------------------------------------------------------------------ lip-sync scheduler (workers/lipsync_sched.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers"))
import lipsync_sched as ls  # noqa: E402


class Req:
    def __init__(self): self.gone = False
    async def is_disconnected(self): return self.gone


def work(order, tag, secs=0.05):
    def f():
        time.sleep(secs)
        order.append(tag)
        return tag
    return f


def test_first_piece_jumps_the_queue_and_fairness_limits_a_hog():
    async def go():
        sch = ls.Scheduler(lanes=1, max_queue=50)
        order = []
        blocker = asyncio.create_task(sch.submit(Req(), "blk", 5, work(order, "blocker", 0.3)))
        await asyncio.sleep(0.05)  # lane busy: everything below queues
        jobs = [asyncio.create_task(sch.submit(Req(), "a", 4, work(order, "a-late"))),
                asyncio.create_task(sch.submit(Req(), "a", 5, work(order, "a-late2"))),
                asyncio.create_task(sch.submit(Req(), "b", 0, work(order, "b-first")))]
        await asyncio.gather(blocker, *jobs)
        assert order[0] == "blocker" and order[1] == "b-first"  # the first piece of a reply is rendered before older later pieces
    asyncio.run(go())


def test_one_session_cannot_starve_another():
    async def go():
        sch = ls.Scheduler(lanes=1, max_queue=50)
        order = []
        sch._charge("hog", 3.0)  # the hog has used a lot of GPU recently
        blocker = asyncio.create_task(sch.submit(Req(), "blk", 5, work(order, "blocker", 0.2)))
        await asyncio.sleep(0.05)
        hog = asyncio.create_task(sch.submit(Req(), "hog", 3, work(order, "hog")))
        await asyncio.sleep(0.02)
        small = asyncio.create_task(sch.submit(Req(), "small", 3, work(order, "small")))  # arrives later, same class
        await asyncio.gather(blocker, hog, small)
        assert order == ["blocker", "small", "hog"]
    asyncio.run(go())


def test_disconnected_client_job_is_dropped_without_rendering():
    from fastapi import HTTPException

    async def go():
        sch = ls.Scheduler(lanes=1, max_queue=50)
        order = []
        blocker = asyncio.create_task(sch.submit(Req(), "blk", 5, work(order, "blocker", 0.5)))
        await asyncio.sleep(0.05)
        r = Req()
        victim = asyncio.create_task(sch.submit(r, "v", 1, work(order, "victim")))
        await asyncio.sleep(0.05)
        r.gone = True  # barge-in: the backend cancelled the request
        with pytest.raises(HTTPException) as e:
            await victim
        assert e.value.status_code == 499
        await blocker
        await asyncio.sleep(0.1)
        assert order == ["blocker"] and sch.stats()["cancelled"] == 1
    asyncio.run(go())


def test_queue_full_is_503_and_stale_jobs_are_504():
    from fastapi import HTTPException

    async def go():
        sch = ls.Scheduler(lanes=1, max_queue=2)
        order = []
        t = [asyncio.create_task(sch.submit(Req(), "blk", 5, work(order, "blocker", 0.6)))]
        await asyncio.sleep(0.05)
        t.append(asyncio.create_task(sch.submit(Req(), "a", 3, work(order, "a"), deadline_s=0.2)))  # will go stale
        t.append(asyncio.create_task(sch.submit(Req(), "b", 3, work(order, "b"))))
        await asyncio.sleep(0.02)
        with pytest.raises(HTTPException) as e:
            await sch.submit(Req(), "c", 3, work(order, "c"))
        assert e.value.status_code == 503 and e.value.headers["Retry-After"] == "1"
        res = await asyncio.gather(*t, return_exceptions=True)
        assert isinstance(res[1], HTTPException) and res[1].status_code == 504 and res[2] == "b"
        assert "a" not in order and sch.stats()["rejected"] == 1 and sch.stats()["stale"] == 1
    asyncio.run(go())


def test_worker_exception_is_returned_to_the_caller_and_lane_survives():
    async def go():
        sch = ls.Scheduler(lanes=1)

        def boom(): raise RuntimeError("cuda out of memory")
        with pytest.raises(RuntimeError):
            await sch.submit(Req(), "s", 0, boom)
        assert await sch.submit(Req(), "s", 1, lambda: "ok") == "ok"  # one failed render does not kill the lane
    asyncio.run(go())


def test_two_lanes_overlap_work():
    async def go():
        sch = ls.Scheduler(lanes=2)
        order = []
        t0 = time.monotonic()
        await asyncio.gather(sch.submit(Req(), "a", 1, work(order, "a", 0.3)), sch.submit(Req(), "b", 1, work(order, "b", 0.3)))
        assert time.monotonic() - t0 < 0.5
    asyncio.run(go())
