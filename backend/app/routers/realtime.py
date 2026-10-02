"""Browser real-time voice: WebSocket /v1/conversations/{cid}/stream?api_key=...

Client -> server: binary int16 16 kHz mono PCM; text JSON {"type":"interrupt"}.
Server -> client: binary int16 24 kHz mono PCM (agent audio); JSON events
  ready, speech_start, transcript{role,text}, agent_start, agent_done, interrupted, error.
Also serves the test client at GET /playground.
"""
import asyncio
import json
import math
from pathlib import Path

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sqlmodel import Session as DBSession, select

from ..db import Account, Conversation, Persona, get_session
from ..pipeline.session import Session, build_system_prompt, get_providers

router = APIRouter()
STATIC = Path(__file__).resolve().parents[1] / "static"


@router.get("/playground", include_in_schema=False)
def playground():
    return HTMLResponse((STATIC / "playground.html").read_text())


@router.websocket("/conversations/{cid}/stream")
async def stream(ws: WebSocket, cid: str, api_key: str = "", db: DBSession = Depends(get_session)):
    acc = db.exec(select(Account).where(Account.api_key == api_key)).first() if api_key else None
    if not acc:
        await ws.close(code=4401, reason="invalid api key")
        return
    conv = db.get(Conversation, cid)
    if not conv or conv.account_id != acc.id:
        await ws.close(code=4404, reason="conversation not found")
        return
    persona = db.get(Persona, conv.persona_id)
    if conv.status == "ended" or acc.credits_seconds <= 0 or not persona:
        await ws.close(code=4402, reason="conversation ended or out of credits")
        return
    await ws.accept()
    try:
        providers = await asyncio.to_thread(get_providers, persona.llm)  # lazy model load
    except Exception as e:
        await ws.send_json({"type": "error", "message": f"provider load failed: {e}"})
        await ws.close(code=1011)
        return

    from ..knowledge import format_context, retrieve
    from .. import jobs

    lipsync = None
    if persona.replica_id and (jobs.replica_dir(persona.replica_id) / "source.mp4").exists():
        from ..pipeline.lipsync import LipsyncClient

        if await LipsyncClient.available():
            lipsync = LipsyncClient(persona.replica_id)

    pid = persona.id
    sess = Session(providers, build_system_prompt(persona), persona.tts_voice, ws.send_json, ws.send_bytes,
                   retriever=lambda q: format_context(retrieve(pid, q, k=3)), lipsync=lipsync)
    base = conv.seconds_used or 0

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
        face_url = f"/v1/files/replicas/{persona.replica_id}/face.png"
    await ws.send_json({"type": "ready", "input_sample_rate": 16000, "output_sample_rate": 24000, "face_url": face_url,
                         "live_face": lipsync is not None})
    if lipsync is not None:  # idle loop lets the browser keep the face alive between answers
        try:
            idle = await lipsync.idle()
            await ws.send_json({"type": "idle_loop", "fps": idle["fps"], "frames": idle["frames"]})
        except Exception:
            sess.lipsync = None
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                await sess.feed(msg["bytes"])
            elif msg.get("text"):
                try:
                    if json.loads(msg["text"]).get("type") == "interrupt":
                        await sess.interrupt()
                except ValueError:
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        tick.cancel()
        await sess.close()
        meter()
