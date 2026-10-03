import asyncio

import httpx
from fastapi import FastAPI, Request

from app.pipeline import lipsync as ls


def make_app(seen):
    app = FastAPI()

    @app.post("/render/{rid}")
    async def render(rid: str, request: Request):
        seen.append((rid, dict(request.query_params), len(await request.body())))
        return {"fps": 25, "frames": ["x"], "ms": 1, "start_phase": 3, "end_phase": 4, "loop_len": 75}

    @app.post("/prepare/{rid}")
    def prepare(rid: str):
        return {"ok": True, "frames": 75}

    @app.get("/health")
    def health():
        return {"ok": True, "device": "cpu"}

    return app


def test_client_passes_phase_and_fade(monkeypatch):
    seen = []
    app = make_app(seen)
    real = httpx.AsyncClient

    class C(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.ASGITransport(app=app)
            super().__init__(*a, **k)

    monkeypatch.setattr(ls.httpx, "AsyncClient", C)

    async def go():
        c = ls.LipsyncClient("r_1", url="http://svc")
        a = await c.render(b"\0\0" * 100)
        b = await c.render(b"\0\0" * 100, phase=12, fade_in=True)
        return a, b, await c.prepare(), await c.health()

    a, b, p, h = asyncio.run(go())
    assert seen[0] == ("r_1", {}, 200)  # old call signature unchanged: no query params
    assert seen[1][1] == {"phase": "12", "fade_in": "1"}
    assert a["loop_len"] == 75 and p["ok"] and h["device"] == "cpu"
