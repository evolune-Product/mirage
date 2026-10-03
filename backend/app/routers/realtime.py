"""Browser real-time voice: WebSocket /v1/conversations/{cid}/stream?api_key=...

Client -> server: binary int16 16 kHz mono PCM; text JSON {"type":"interrupt"}.
Server -> client: binary int16 24 kHz mono PCM (agent audio); JSON events
  ready, speech_start, transcript{role,text}, agent_start, agent_done, interrupted, error.
Additive extensions (all optional, documented in docs/overnight/realtime-client.md): hello/hello_ack negotiation,
tagged binary framing + binary video, adaptive JPEG tiers, ping/pong heartbeat, resume, end, push-to-talk, echo events,
client_stats, frame/perception forwarding.
Also serves the test client at GET /playground.
"""
import asyncio
import json
import math
import time
from pathlib import Path

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sqlmodel import Session as DBSession, select

from ..auth import account_for_key
from ..db import Account, Conversation, Persona, get_session
from ..pipeline.session import Session, build_system_prompt, get_providers, warmup_providers
from ..rtc_transport import DEAD_AFTER_S, HEARTBEAT_S, TAG_AUDIO, TIERS, Link, b64_frames, pack_video, recode

router = APIRouter()
STATIC = Path(__file__).resolve().parents[1] / "static"


def _make_retriever(pid: str, rt):
    from ..knowledge import format_context, retrieve, select_hits

    def go(q: str) -> str:
        hits = retrieve(pid, q, k=3)
        if rt is not None:
            try:
                rt.note_hits(select_hits(hits))  # what the agent is actually given = what it can be cited for
            except Exception:  # noqa: BLE001
                pass
        return format_context(hits)

    return go


async def _prepare(lipsync) -> None:
    try:
        await lipsync.prepare()
    except Exception:  # optional warm-up
        pass


async def send_idle_loop(ws, sess, lipsync, tx=None, link=None) -> None:
    """Never blocks the microphone loop: pushes `idle_loop` when the (possibly cold) base clip is ready; on failure the
    session just continues voice-only. A reconnecting client that still holds the loop (`hello.idle_cached`) skips it."""
    try:
        if link is not None and link.idle_cached:
            return
        idle = await lipsync.idle()
        await (tx or ws.send_json)({"type": "idle_loop", "fps": idle["fps"], "frames": idle["frames"]})
    except asyncio.CancelledError:
        raise
    except Exception:
        sess.lipsync = None


@router.get("/playground", include_in_schema=False)
def playground():
    return HTMLResponse((STATIC / "playground.html").read_text())


@router.websocket("/conversations/{cid}/stream")
async def stream(ws: WebSocket, cid: str, api_key: str = "", db: DBSession = Depends(get_session)):
    acc = account_for_key(db, api_key) if api_key else None
    if not acc:
        await ws.close(code=4401, reason="invalid api key")
        return
    conv = db.get(Conversation, cid)
    if not conv or conv.account_id != acc.id:
        await ws.close(code=4404, reason="conversation not found")
        return
    persona = db.get(Persona, conv.persona_id)
    from ..billing import session_allowance

    allowance = session_allowance(db, acc)  # credits + capped overage headroom (billing.py)
    if conv.status == "ended" or allowance <= 0 or not persona:
        await ws.close(code=4402, reason="conversation ended or out of credits")
        return
    await ws.accept()
    from .. import admission, jobs as _jobs

    wants_face = bool(persona.replica_id and (_jobs.replica_dir(persona.replica_id) / "source.mp4").exists())
    admission.LAG.ensure()

    async def _queued(pos: int, wait_s: float) -> None:
        await ws.send_json({"type": "queued", "position": pos, "max_wait_s": wait_s})

    dec = await admission.CONTROLLER.admit(cid, wants_face, on_queued=_queued)
    if not dec.granted:  # graceful "server busy": a clear message + the standard 1013 (try again later) close code
        snap = admission.CONTROLLER.snapshot()
        try:
            await ws.send_json({"type": "busy", "message": dec.reason, "retry_after_s": dec.retry_after_s,
                                "current": snap["current"], "limit": snap["limit"]})
            await ws.close(code=1013, reason=f"server busy, retry in {dec.retry_after_s} s")
        except Exception:  # noqa: BLE001
            pass
        return "busy"
    link = Link()
    inbox: asyncio.Queue = asyncio.Queue()
    done_evt = asyncio.Event()
    prev = _ACTIVE.get(cid)
    _ACTIVE[cid] = (ws, done_evt)
    pump_task = dog = None
    try:  # everything after admission is inside this try so the slot is always released, even if we are cancelled early
        if prev:  # a reconnect replaces a half-open predecessor; wait for its teardown so it cannot clobber our state
            try:
                await prev[0].close(code=4409, reason="replaced by a newer connection")
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(prev[1].wait(), 4)
            except Exception:  # noqa: BLE001
                pass
        pump_task = asyncio.create_task(_pump(ws, link, inbox))  # answers hello/ping at once, even while models load
        dog = asyncio.create_task(_watchdog(ws, link))
        return await _run(ws, cid, acc, conv, persona, db, link, inbox, pump_task, allow_face=dec.face or not wants_face)
    finally:
        admission.CONTROLLER.release(dec.ticket)
        for t in (pump_task, dog):
            if t is not None:
                t.cancel()
        if _ACTIVE.get(cid, (None,))[0] is ws:
            _ACTIVE.pop(cid, None)
        done_evt.set()


async def _run(ws, cid, acc, conv, persona, db, link, inbox, pump_task, allow_face=True):
    """Returns 'ended' when the client said goodbye (or hit a limit), 'dropped' when a hello-capable client's socket
    just went away (it may reconnect and resume), None for legacy clients."""
    try:
        providers = await asyncio.to_thread(get_providers, persona.llm)  # lazy model load
    except Exception as e:
        await ws.send_json({"type": "error", "message": f"provider load failed: {e}"})
        await ws.close(code=1011)
        return

    # Pay one-time latency costs (LLM load + prompt prefill, TTS engine start) now, concurrently with the setup below,
    # so the first spoken turn is as fast as the later ones.
    voice = persona.tts_voice
    if voice.startswith("clone"):  # cloned voice: only the owner's, ready, consented voice; otherwise the default voice
        from ..pipeline.providers_clone import resolve_persona_voice

        voice = await asyncio.to_thread(resolve_persona_voice, persona)
    warm = asyncio.create_task(warmup_providers(providers, build_system_prompt(persona), voice))

    from ..knowledge import format_context, retrieve
    from .. import jobs

    lipsync = None
    if allow_face and persona.replica_id and (jobs.replica_dir(persona.replica_id) / "source.mp4").exists():
        from ..pipeline.lipsync import LipsyncClient

        if await LipsyncClient.available():
            lipsync = LipsyncClient(persona.replica_id)
            asyncio.create_task(_prepare(lipsync))  # warm the replica's base clip while models warm up

    pid = persona.id
    from ..convo_runtime import ConversationRuntime  # feature layer: transcripts, greeting, tools, guardrails, i18n

    try:
        rt = ConversationRuntime.build(db, conv, persona)
        providers = rt.wrap_providers(providers)
    except Exception:  # a bad feature config must never block the call
        rt = None
    async def tx_json(msg: dict) -> None:
        """Server->client JSON. Video messages become binary (and are re-encoded for the client's tier) for clients that
        negotiated it; everything else is untouched."""
        t = msg.get("type")
        if link.hello and t in ("video_segment", "idle_loop") and msg.get("frames") and (link.tagged or link.tier):
            jpegs = b64_frames(msg["frames"])
            tier = min(link.tier, 1) if t == "idle_loop" else link.tier  # the idle loop sets the canvas size: keep it sharp
            if tier:
                jpegs = await asyncio.to_thread(recode, jpegs, tier)
            t0 = time.monotonic()
            if link.tagged:
                blob = pack_video(t, msg.get("fps", 25), jpegs, end_phase=msg.get("end_phase"), tier=tier)
                await ws.send_bytes(blob)
                nbytes = len(blob)
            else:  # hello without tagged framing: keep JSON/base64, only the quality tier applies
                import base64

                m2 = dict(msg, frames=[base64.b64encode(j).decode() for j in jpegs])
                await ws.send_json(m2)
                nbytes = sum(len(j) for j in jpegs) * 4 // 3
            link.bytes_video += nbytes
            link.note_send(nbytes, time.monotonic() - t0)
            return
        await ws.send_json(msg)

    async def tx_bytes(b: bytes) -> None:
        link.bytes_audio += len(b)
        await ws.send_bytes(bytes([TAG_AUDIO]) + b if link.tagged else b)

    sess = Session(providers, rt.system_prompt(build_system_prompt(persona)) if rt else build_system_prompt(persona),
                   voice, rt.wrap_send(tx_json) if rt else tx_json, tx_bytes,
                   retriever=_make_retriever(pid, rt), lipsync=lipsync)
    if rt is not None and rt.tuning is not None:  # interruption sensitivity / turn patience (voice-tuning API)
        try:
            from .. import voice_tuning

            voice_tuning.apply(sess, rt.tuning)
        except Exception:  # noqa: BLE001
            pass
    try:  # intelligence layer: grounding scaffold + perception (the agent sees camera/screen frames); never blocks the call
        from ..llm_backends import ground_session
        from ..perception import attach as _attach_perception

        ground_session(sess)
        sess.perception = _attach_perception(db, persona, sess, tx_json, jobs.DATA_DIR, cid)
    except Exception:  # noqa: BLE001
        sess.perception = None
    base = conv.seconds_used or 0
    resumed = False
    if rt is not None:  # reconnect: restore what was said (the runtime already knows the turn count, so no re-greeting)
        resumed = _restore(db, conv, rt, sess)
        if resumed or link.resume:
            rt.greeting = ""
        _mark_open(db, cid)

    def meter():
        conv.seconds_used = base + math.ceil(sess.seconds)
        db.add(conv); db.commit()

    async def ticker():
        while True:
            await asyncio.sleep(5)
            meter()

    tick = asyncio.create_task(ticker())

    face_url = None
    if persona.replica_id and (jobs.replica_dir(persona.replica_id) / "face.png").exists():
        from .. import signing  # signed, expiring link (safety-infra)
        face_url = signing.sign_path(f"/v1/files/replicas/{persona.replica_id}/face.png")
    try:
        await asyncio.wait_for(warm, 60)
    except Exception:  # warm-up is an optimisation: never block or fail the call because of it
        pass
    await ws.send_json({"type": "ready", "input_sample_rate": 16000, "output_sample_rate": 24000, "face_url": face_url,
                         "live_face": lipsync is not None, "resumed": resumed, "tier": link.tier if link.hello else None})
    idle_task = None
    if lipsync is not None:  # idle loop keeps the face alive between answers; fetched in the background (cold: ~8 s)
        idle_task = asyncio.create_task(send_idle_loop(ws, sess, lipsync, tx_json, link))
    if rt:
        from ..billing import session_allowance as _allow

        rt.start(sess, ws, base, _allow(db, acc))  # greeting + time/credit watchdog
    # The pump (needed while models load) hands over: from here on the loop reads the socket itself, which keeps the
    # teardown path as short as it always was. Anything the pump already queued is processed first.
    while link.busy:
        await asyncio.sleep(0)
    pump_task.cancel()
    await asyncio.gather(pump_task, return_exceptions=True)
    try:
        while True:
            msg = inbox.get_nowait() if not inbox.empty() else await ws.receive()
            link.last_rx = time.monotonic()
            if msg is None or msg["type"] == "websocket.disconnect":
                break
            if msg.get("text") and await _control(ws, link, msg["text"]):
                if link.ended:
                    break
                continue
            if msg.get("bytes"):
                await sess.feed(msg["bytes"])
            elif msg.get("text"):
                try:
                    m = json.loads(msg["text"])
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                t = m.get("type")
                if t == "interrupt":
                    await sess.interrupt()
                elif t == "idle_phase":  # client's idle ping-pong index (head-pop-free speech start)
                    sess.set_idle_phase(m.get("phase", 0), m.get("fps", 25))
                elif t == "ptt":  # push-to-talk: state on|off (mode) or down|up (the key / button)
                    st = m.get("state")
                    if st in ("on", "off"):
                        sess.set_ptt(st == "on")
                    elif st == "down":
                        await sess.ptt_down()
                    elif st == "up":
                        await sess.ptt_up()
                elif t == "client_stats":  # decode lag etc.: lets the server step the video quality up or down
                    try:
                        old_tier = link.tier
                        if link.hello and link.note_stats(float(m.get("lag_ms") or 0)) != old_tier:
                            await ws.send_json({"type": "quality", "tier": link.tier, "reason": "lag" if link.lag_ms > 350 else "recovered"})
                    except (TypeError, ValueError):
                        pass
                elif t in ("frame", "perception"):  # webcam / screen frames for the perception feature (opt-in on the client)
                    if t == "frame" and len(msg["text"]) > MAX_FRAME_JSON:
                        continue
                    pm = getattr(sess, "perception", None)
                    if pm is not None and hasattr(pm, "handle_message"):
                        await pm.handle_message(m)
    except WebSocketDisconnect:
        pass
    finally:
        result = "ended" if (link.ended or not link.hello) else "dropped"
        tick.cancel()
        if idle_task:
            idle_task.cancel()
        if getattr(sess, "perception", None) is not None:
            sess.perception.close()  # drops every frame/description held in RAM
        # Cancel everything that can outlive the call *before* the first await: if this handler is itself cancelled
        # (abrupt disconnect) the awaits below may never finish, and the runtime's time-limit watchdog used to leak per call.
        for t in list(getattr(rt, "_tasks", ())) + list(getattr(sess, "_jobs", ())):
            t.cancel()
        try:
            await sess.close()
            if rt:
                await rt.stop()
        finally:
            meter()
    return result


MAX_FRAME_JSON = 900_000  # a webcam/screen frame message above this size (chars) is ignored
_ACTIVE: dict[str, tuple] = {}  # conversation id -> (websocket, teardown-done event): one live socket per conversation


async def _control(ws, link: Link, txt: str) -> bool:
    """hello / ping / end are handled the moment they arrive. True when the message was one of them (consumed)."""
    if not (len(txt) < 4096 and txt.startswith("{") and any(k in txt[:40] for k in ('"hello"', '"ping"', '"end"'))):
        return False
    try:
        m = json.loads(txt)
    except ValueError:
        return False
    t = m.get("type")
    if t == "hello":
        link.apply_hello(m)
        await ws.send_json({"type": "hello_ack", "framing": "tagged" if link.want_tagged else "legacy", "tier": link.tier,
                            "heartbeat_s": HEARTBEAT_S, "tiers": len(TIERS)})
        link.tagged = link.want_tagged
        return True
    if t == "ping":
        await ws.send_json({"type": "pong", "t": m.get("t")})
        return True
    if t == "end":
        link.ended = True
        return True
    return False


async def _pump(ws, link: Link, inbox: asyncio.Queue) -> None:
    """Reads the socket from the moment it is accepted until the session loop takes over, so a client can negotiate and
    heartbeat while models are still loading. Everything that is not a control message is queued for the loop."""
    try:
        while True:
            msg = await ws.receive()
            link.busy = True  # the session loop waits for this to clear before it takes over (never cancel mid-send)
            try:
                link.last_rx = time.monotonic()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text") and await _control(ws, link, msg["text"]):
                    if link.ended:
                        break
                    continue
                inbox.put_nowait(msg)
            finally:
                link.busy = False
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - RuntimeError after disconnect, etc.
        pass
    inbox.put_nowait(None)


async def _watchdog(ws, link: Link) -> None:
    """Dead-connection detection for heartbeat-capable clients: they send ping every few seconds (and audio constantly);
    silence for DEAD_AFTER_S means a half-open socket (sleeping phone, dropped wifi): close so the session is torn down
    and a reconnect can take over."""
    while True:
        await asyncio.sleep(2)
        if link.hello and time.monotonic() - link.last_rx > DEAD_AFTER_S:
            try:
                await ws.close(code=4410, reason="heartbeat timeout")
            except Exception:  # noqa: BLE001
                pass
            return


def _restore(db, conv, rt, sess) -> bool:
    """Rebuild the LLM history from the stored transcript on a reconnect. True when there was earlier conversation."""
    if not getattr(rt, "_seq", 0):
        return False
    try:
        from ..models_features import TranscriptTurn

        rows = db.exec(select(TranscriptTurn).where(TranscriptTurn.conversation_id == conv.id).order_by(TranscriptTurn.seq)).all()
        sess.history = [{"role": "user" if r.role == "user" else "assistant", "content": r.text} for r in rows][-20:]
    except Exception:  # noqa: BLE001
        pass
    return True


def _mark_open(db, cid: str) -> None:
    """The reaper ends a conversation some seconds after the last socket closed; a (re)connected socket cancels that."""
    try:
        from ..models_features import ConversationMeta

        m = db.get(ConversationMeta, cid)
        if m is not None and m.last_closed_at is not None:
            m.last_closed_at = None
            db.add(m); db.commit()
    except Exception:  # noqa: BLE001
        pass
