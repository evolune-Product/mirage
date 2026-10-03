"""Connect/disconnect cycles against a running server; compares asyncio tasks / threads / open fds / RSS before and after
(from GET /health/capacity) to catch leaked tasks, sockets and sessions.

    cd backend && .venv/bin/python -m loadtest.leakcheck --base http://localhost:8440 --cycles 100 [--face --db /tmp/cap.db]

Every cycle: new WebSocket conversation, wait for `ready`, stream ~1 s of speech + silence, then leave either politely
(`end`), abruptly (socket dropped) or mid-reply (barge-in by disconnect). Ollama/TTS/lip-sync are real when the server has them.
"""
import argparse
import asyncio
import json
import random
import time

import httpx

from .loadgen import FRAME, SILENCE, make_replica_registrar, question_audio


async def cycle(base, i, mode, face, reg):
    import websockets

    async with httpx.AsyncClient(base_url=base + "/v1", timeout=30) as h:
        sj = (await h.post("/signup", json={"email": f"leak{i}_{int(time.time()*1000)}@x.io"})).json()
        key = sj["api_key"]; H = {"x-api-key": key}
        body = {"name": "L", "system_prompt": "Be brief.", "llm": "ollama/llama3.2:1b"}
        if face:
            body["replica_id"] = reg(sj["account_id"], key)
        p = (await h.post("/personas", json=body, headers=H)).json()
        cid = (await h.post("/conversations", json={"persona_id": p["id"]}, headers=H)).json()["id"]
    url = base.replace("http", "ws", 1) + f"/v1/conversations/{cid}/stream?api_key={key}"
    async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
        await ws.send(json.dumps({"type": "hello", "framing": "tagged", "tier": 1}))
        t0 = time.time()
        while True:
            m = await asyncio.wait_for(ws.recv(), 90)
            m = json.loads(m) if isinstance(m, str) else None
            if isinstance(m, dict) and m.get("type") == "ready":
                break
        q = question_audio(i)
        for k in range(0, len(q), FRAME):
            await ws.send(q[k:k + FRAME].ljust(FRAME, b"\0")); await asyncio.sleep(0.005)
        for _ in range(60):
            await ws.send(SILENCE); await asyncio.sleep(0.005)
        if mode == "polite":
            await ws.send(json.dumps({"type": "end"}))
        elif mode == "midreply":
            try:
                while True:
                    m = await asyncio.wait_for(ws.recv(), 20)
                    if isinstance(m, bytes):
                        break
            except Exception:  # noqa: BLE001
                pass
        # "abrupt": leave the context manager immediately


async def stats(base):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(base + "/health/capacity")
        return r.json()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8440")
    ap.add_argument("--cycles", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--face", action="store_true")
    ap.add_argument("--db", default="/tmp/cap.db"); ap.add_argument("--data", default="/tmp/cap_data")
    ap.add_argument("--replica", default="r_035d420020e3")
    a = ap.parse_args()
    reg = make_replica_registrar(a.db, a.replica, a.data) if a.face else None
    await cycle(a.base, 9999, "polite", a.face, reg)  # warm everything once so the baseline is steady state
    await asyncio.sleep(3)
    before = await stats(a.base)
    sem = asyncio.Semaphore(a.concurrency)
    fails = 0

    async def one(i):
        nonlocal fails
        async with sem:
            try:
                await cycle(a.base, i, random.Random(i).choice(["polite", "abrupt", "midreply"]), a.face, reg)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("cycle", i, "failed:", type(e).__name__, str(e)[:80])
    t0 = time.time()
    await asyncio.gather(*[one(i) for i in range(a.cycles)])
    print(f"{a.cycles} cycles in {time.time()-t0:.0f}s, {fails} failed")
    for wait in (3, 10, 20):
        await asyncio.sleep(wait)
        after = await stats(a.base)
        pb, pa = before["process"], after["process"]
        print(f"+{wait:2d}s  slots {after['current']}  tasks {pb['asyncio_tasks']}->{pa['asyncio_tasks']}  threads {pb['threads']}->{pa['threads']}  "
              f"fds {pb['open_fds']}->{pa['open_fds']}  rss_mb {pb['rss_mb']}->{pa['rss_mb']}  loop_lag_max30_ms {pa['loop_lag_max30_ms']}")


if __name__ == "__main__":
    asyncio.run(main())
