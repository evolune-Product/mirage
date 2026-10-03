"""Perception test client: sends webcam/screen frames over the conversation WebSocket, then asks a spoken question.

  .venv/bin/python scripts_perception_client.py --base http://localhost:8290 --image shot.png --source screen \
      --question "What do you see on my screen?" [--llm ollama/llama3.2:1b] [--no-optin] [--frames 5 --interval 2]

Needs the API up (isolated instance recommended), Ollama with a VLM (MIRAGE_VLM_MODEL, default moondream) and the LLM.
This is also the reference implementation of the client message contract (docs/overnight/intelligence.md):
  {"type":"perception","enabled":true}  then  {"type":"frame","source":"camera|screen","jpeg_b64":"..."}
"""
import argparse
import asyncio
import base64
import io
import json
import time

import httpx
import numpy as np
import websockets
from PIL import Image


def to_jpeg_b64(path: str, max_side: int = 960, q: int = 70) -> str:
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q)
    return base64.b64encode(b.getvalue()).decode()


async def main(a):
    h = httpx.Client(base_url=a.base + "/v1", timeout=60)
    key = h.post("/signup", json={"email": f"perc{int(time.time())}@x.io"}).json()["api_key"]
    H = {"x-api-key": key}
    p = h.post("/personas", json={"name": "Seer", "system_prompt": "You are Sam, a friendly voice assistant.", "llm": a.llm}, headers=H).json()
    cfg = {"enabled": True, "consent_acknowledged": True, "require_user_consent": not a.no_optin, "interval_s": 1.0}
    if a.vlm:
        cfg["vlm_model"] = a.vlm
    print("perception config:", h.put(f"/personas/{p['id']}/perception", json=cfg, headers=H).json())
    cid = h.post("/conversations", json={"persona_id": p["id"]}, headers=H).json()["id"]

    from app.pipeline.local import KokoroTTS
    tts = KokoroTTS()
    pcm24 = b"".join([c async for c in tts.synthesize(a.question)])
    x = np.frombuffer(pcm24, np.int16).astype(np.float32)
    pcm16 = np.interp(np.linspace(0, len(x), int(len(x) * 16 / 24), endpoint=False), np.arange(len(x)), x).astype(np.int16).tobytes()
    pcm16 += bytes(32000 * 2)
    frame = json.dumps({"type": "frame", "source": a.source, "jpeg_b64": to_jpeg_b64(a.image)})
    url = a.base.replace("http", "ws") + f"/v1/conversations/{cid}/stream?api_key={key}"
    async with websockets.connect(url, max_size=None) as ws:
        while True:
            m = json.loads(await ws.recv())
            if m.get("type") == "ready":
                break
        if not a.no_optin:
            await ws.send(json.dumps({"type": "perception", "enabled": True}))
        scenes = []

        async def reader(sink):
            while True:
                m = await ws.recv()
                if isinstance(m, bytes):
                    if "first_audio" not in sink:
                        sink["first_audio"] = time.time()
                    continue
                m = json.loads(m)
                if m["type"] == "scene":
                    scenes.append(m)
                    print(f"  scene[{m['source']}] {m['ms']} ms: {m['text']}")
                elif m["type"] == "perception_status":
                    print("  status:", m)
                elif m["type"] == "transcript":
                    print(f"  transcript[{m['role']}]: {m['text']}")
                    sink.setdefault(m["role"], []).append(m["text"])
                elif m["type"] == "agent_done":
                    sink["done"] = time.time()
                    return
        sink: dict = {}
        r = asyncio.create_task(reader(sink))
        t0 = time.time()
        for i in range(a.frames):
            await ws.send(frame)
            if i < a.frames - 1:
                await asyncio.sleep(a.interval)
        if not a.no_wait_scene:
            for _ in range(int(a.scene_wait * 10)):
                if scenes:
                    break
                await asyncio.sleep(0.1)
            print(f"first scene description after {time.time() - t0:.1f}s" if scenes else "no scene description arrived")
        t_q = None
        for i in range(0, len(pcm16), 640):
            await ws.send(pcm16[i:i + 640])
            if i >= len(pcm16) - 32000 * 2 - 640 and t_q is None:
                t_q = time.time()
            await asyncio.sleep(0.02)
        try:
            await asyncio.wait_for(r, 90)
        except asyncio.TimeoutError:
            print("timeout waiting for the agent")
        if sink.get("first_audio") and t_q:
            print(f"question end -> first agent audio: {sink['first_audio'] - t_q:.2f}s")
        print("AGENT SAID:", " ".join(sink.get("assistant", [])))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8290")
    ap.add_argument("--image", required=True)
    ap.add_argument("--source", default="camera", choices=["camera", "screen"])
    ap.add_argument("--question", default="What do you see?")
    ap.add_argument("--llm", default="ollama/llama3.2:1b")
    ap.add_argument("--vlm", default="")
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--interval", type=float, default=1.5)
    ap.add_argument("--scene-wait", type=float, default=30.0, help="seconds to wait for the first description before asking")
    ap.add_argument("--no-wait-scene", action="store_true")
    ap.add_argument("--no-optin", action="store_true", help="persona does not require per-user opt-in")
    asyncio.run(main(ap.parse_args()))
