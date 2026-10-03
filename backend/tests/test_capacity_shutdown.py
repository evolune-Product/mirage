"""Crash recovery (killed process left conversations active) and graceful shutdown (sockets closed, teardown awaited)."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import convo_runtime as cr, db, main as _main, resilience  # noqa: F401  (main registers every table)
from app.models_features import ConversationMeta
from app.routers import realtime


def fresh_db():
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)


def test_orphan_conversations_get_closed_stamp_and_reaper_bills_them():
    fresh_db()
    with Session(db.engine) as s:
        acc = db.Account(email="a@b.c", credits_seconds=100)
        s.add(acc); s.commit(); s.refresh(acc)
        live = db.Conversation(account_id=acc.id, persona_id="p", seconds_used=40)  # process was SIGKILLed mid-call
        done = db.Conversation(account_id=acc.id, persona_id="p", status="ended", seconds_used=5)
        s.add(live); s.add(done); s.commit(); s.refresh(live)
        s.add(ConversationMeta(conversation_id=live.id, account_id=acc.id))
        s.add(ConversationMeta(conversation_id=done.id, account_id=acc.id))
        s.commit()
        lid, aid = live.id, acc.id
    assert cr.reap_idle(grace_s=0) == []  # before recovery the orphan is invisible to the reaper (the bug)
    assert resilience.recover_orphans() == 1
    assert resilience.recover_orphans() == 0  # idempotent: already stamped
    assert cr.reap_idle(grace_s=3600) == []  # inside the grace period a reconnecting client can still resume it
    with Session(db.engine) as s:
        m = s.get(ConversationMeta, lid); m.last_closed_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        s.add(m); s.commit()
    assert cr.reap_idle(grace_s=30) == [lid]  # after the grace it is ended and the metered seconds are charged
    with Session(db.engine) as s:
        c = s.get(db.Conversation, lid)
        assert c.status == "ended" and c.seconds_used == 40
        assert s.get(db.Account, aid).credits_seconds == 60


def test_recovery_can_be_disabled(monkeypatch):
    fresh_db()
    monkeypatch.setenv("MIRAGE_RECOVER_ORPHANS", "0")
    assert resilience.recover_orphans() == 0


class FakeWS:
    def __init__(self, evt, delay=0.05):
        self.closed, self.evt, self.delay = None, evt, delay

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)
        asyncio.get_running_loop().call_later(self.delay, self.evt.set)  # the handler's finally block runs shortly after


def test_graceful_shutdown_closes_sockets_with_1012_and_waits_for_teardown():
    async def go():
        e1, e2 = asyncio.Event(), asyncio.Event()
        w1, w2 = FakeWS(e1), FakeWS(e2, 0.3)
        realtime._ACTIVE.clear()
        realtime._ACTIVE.update({"c1": (w1, e1), "c2": (w2, e2)})
        t0 = asyncio.get_running_loop().time()
        n = await resilience.graceful_shutdown(timeout=3)
        assert n == 2 and w1.closed[0] == 1012 and w2.closed[0] == 1012 and e1.is_set() and e2.is_set()
        assert asyncio.get_running_loop().time() - t0 >= 0.25  # it really waited for the slow teardown
        realtime._ACTIVE.clear()
    asyncio.run(go())


def test_graceful_shutdown_gives_up_after_timeout():
    async def go():
        e = asyncio.Event()
        w = FakeWS(e, delay=60)
        realtime._ACTIVE.clear(); realtime._ACTIVE["c"] = (w, e)
        t0 = asyncio.get_running_loop().time()
        await resilience.graceful_shutdown(timeout=0.3)
        assert asyncio.get_running_loop().time() - t0 < 1.5
        realtime._ACTIVE.clear()
    asyncio.run(go())
