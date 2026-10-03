"""Real-model smoke test of the WebSocket stream. Needs: uvicorn running (app.main:app) and Ollama.
Usage: .venv/bin/python scripts_ws_smoke.py [http://localhost:8000]
Synthesizes a question with Kokoro, streams it as 16 kHz PCM, reports time-to-first-audio."""
import asyncio, sys, time, json
import httpx, numpy as np, websockets
from app.pipeline.local import KokoroTTS

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


async def main():
    h = httpx.Client(base_url=BASE + "/v1")
    key = h.post("/signup", json={"email": "smoke@x.io"}).json()["api_key"]
    H = {"x-api-key": key}
    p = h.post("/personas", json={"name": "S", "system_prompt": "You are a friendly assistant.", "llm": "ollama/llama3.2:1b"}, headers=H).json()
    cid = h.post("/conversations", json={"persona_id": p["id"]}, headers=H).json()["id"]
    tk = h.post("/realtime/ticket", json={"conversation_id": cid}, headers=H).json()["ticket"]  # key stays in a header, not the URL
    tts = KokoroTTS()
    pcm24 = b"".join([c async for c in tts.synthesize("Hi, can you tell me what a digital twin is?")])
    a = np.frombuffer(pcm24, np.int16).astype(np.float32)
    pcm16 = np.interp(np.linspace(0, len(a), int(len(a) * 16 / 24), endpoint=False), np.arange(len(a)), a).astype(np.int16).tobytes()
    pcm16 += bytes(32000 * 2)  # 2 s silence ends the turn
    url = BASE.replace("http", "ws") + f"/v1/conversations/{cid}/stream?ticket={tk}"
    async with websockets.connect(url, max_size=None) as ws:
        print(json.loads(await ws.recv()))
        t0, first, nbytes = None, None, 0
        for i in range(0, len(pcm16), 640):
            await ws.send(pcm16[i:i + 640])
            if i == len(pcm16) - 640:
                t0 = time.time()
            await asyncio.sleep(0.02)  # real-time pacing
        t0 = t0 or time.time()
        while True:
            m = await asyncio.wait_for(ws.recv(), 60)
            if isinstance(m, bytes):
                nbytes += len(m)
                if first is None:
                    first = time.time() - t0
                    print(f"time to first audio after last frame: {first:.2f}s")
            else:
                print(m)
                if '"agent_done"' in m:
                    break
        print(f"agent audio: {nbytes/48000:.1f}s")

asyncio.run(main())
