from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlmodel import Session

from .db import Account, get_session, init_db
from .routers import resources


@asynccontextmanager
async def lifespan(_):
    init_db()
    from . import resilience as _res

    _res.recover_orphans()  # conversations left active by a killed process get an end time + billing (resilience.py)
    import asyncio
    import os as _os

    from . import webhooks as _wh

    from . import convo_runtime as _cr

    tasks = []
    if _os.environ.get("MIRAGE_WEBHOOK_LOOP", "1") != "0":
        tasks = [asyncio.create_task(_wh.run_loop()), asyncio.create_task(_cr.reaper_loop())]
        if _os.environ.get("MIRAGE_BILLING_LOOP", "1") != "0":
            from . import billing as _bl

            tasks.append(asyncio.create_task(_bl.reset_loop()))  # monthly plan lapse/expiry; idempotent
    if _os.environ.get("MIRAGE_PRELOAD", "1") != "0":
        async def _preload():  # background: the server accepts requests immediately; the first call is then warm
            try:
                from .pipeline.session import get_providers, warmup_providers

                p = await asyncio.to_thread(get_providers, _os.environ.get("MIRAGE_PRELOAD_LLM", "ollama/llama3.2:3b"))
                await warmup_providers(p, "")
            except Exception:  # noqa: BLE001 - an optimisation, never fatal
                pass

        tasks.append(asyncio.create_task(_preload()))
    try:
        yield
    finally:
        try:
            await _res.graceful_shutdown()  # close live sockets, wait for teardown (final metering, runtime stop)
        except Exception:  # noqa: BLE001
            pass
        for t in tasks:
            t.cancel()


app = FastAPI(title="Mirage API", version="0.1.0", lifespan=lifespan)

from . import hardening as _hardening

_hardening.install(app)  # rate limits / size limits / security headers / signed file URLs; keep BEFORE CORS (CORS must be outermost)

import os

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("MIRAGE_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
from . import metrics as _metrics

_metrics.install(app)  # request ids, JSON logs, /metrics, /health/deep (outermost middleware: added after CORS)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
app.include_router(resources.router, prefix="/v1")

# Auto-register every app/routers/*.py that exposes `router` (and optional `PREFIX`).
import importlib
import pkgutil

from . import routers as _routers_pkg

for _m in pkgutil.iter_modules(_routers_pkg.__path__):
    if _m.name == "resources":
        continue
    _mod = importlib.import_module(f"{__package__}.routers.{_m.name}")
    if hasattr(_mod, "router"):
        app.include_router(_mod.router, prefix=getattr(_mod, "PREFIX", "/v1"))


class SignupIn(BaseModel):
    email: str


@app.post("/v1/signup")
def signup(body: SignupIn, s: Session = Depends(get_session)):
    acc = Account(email=body.email)
    s.add(acc); s.commit(); s.refresh(acc)
    return {"account_id": acc.id, "api_key": acc.api_key, "credits_seconds": acc.credits_seconds}


@app.get("/health")
def health():
    return {"ok": True}
