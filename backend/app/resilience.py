"""Crash recovery and graceful shutdown for live conversations (agent "cap").

* `recover_orphans()` (startup): a process that was killed (SIGKILL, OOM, power) never ran the teardown that stamps
  `ConversationMeta.last_closed_at`, so its conversations stayed `active` forever: never reaped, never billed beyond the last
  5-second meter tick. On boot every active conversation without that stamp is stamped "closed now": a client that
  reconnects within the grace period resumes it, otherwise the idle reaper ends it and settles the usage like any other.
  Single-API-process architecture (docs/DEPLOY.md); with several API processes set VOCALFACE_RECOVER_ORPHANS=0.
* `graceful_shutdown()` (lifespan exit): closes the live sockets with 1012 ("service restart") and waits for each handler's
  teardown, which commits the final seconds_used (billing), stops the runtime (transcript flush, last_closed_at) and cancels
  the session's tasks. Clients that negotiated `hello` reconnect and resume on the next instance.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger("vocalface.resilience")


def recover_orphans() -> int:
    import os

    if os.environ.get("VOCALFACE_RECOVER_ORPHANS", "1") == "0":
        return 0
    try:
        from sqlmodel import Session as DB, select

        from . import convo_runtime as cr, db
        from .models_features import ConversationMeta

        cr.ensure()
        n = 0
        with DB(db.engine) as s:
            for c in s.exec(select(db.Conversation).where(db.Conversation.status == "active")).all():
                m = s.get(ConversationMeta, c.id)
                if m is not None and m.last_closed_at is None and m.finalized_at is None:
                    m.last_closed_at = datetime.now(timezone.utc)
                    s.add(m)
                    n += 1
            s.commit()
        if n:
            log.warning("recovered %d conversation(s) left active by a previous process; the reaper will settle them", n)
        return n
    except Exception:  # noqa: BLE001 - recovery must never stop the server from starting
        log.exception("orphan recovery failed")
        return 0


async def graceful_shutdown(timeout: float = 8.0) -> int:
    """Close every live conversation socket and wait (bounded) for its teardown. Returns how many were open."""
    from .routers import realtime

    live = list(realtime._ACTIVE.items())
    for _, (ws, _) in live:
        try:
            await ws.close(code=1012, reason="server restarting")
        except Exception:  # noqa: BLE001
            pass
    if live:
        try:
            await asyncio.wait_for(asyncio.gather(*[evt.wait() for _, (_, evt) in live]), timeout)
        except asyncio.TimeoutError:
            log.warning("shutdown: %d conversation(s) did not finish teardown in %.0f s", len(realtime._ACTIVE), timeout)
    return len(live)
