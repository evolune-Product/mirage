"""Render scheduler for the live lip-sync service (agent "cap").

Before: `POST /render` was an `async def` that ran inference, compositing and JPEG encoding *on the event loop* behind a
plain `threading.Lock`. While one session rendered, the whole service (health checks, /idle, other sessions' requests, even
reading the next request body) was frozen, lock hand-over order was arbitrary, a client that had given up still got its
frames rendered, and nothing bounded the queue.

Now: renders are jobs for a small pool of lane threads (default 2: one lane can composite+encode while the other holds the
GPU lock for inference). Which job runs next is decided here, not by lock luck:

* urgency = seconds waited + piece bonus - fairness penalty
    - piece bonus: the first piece of a reply gates the first lip-synced frame the user sees, so it jumps the queue
      (piece 0: +2.0 s, piece 1: +0.7 s, later pieces +0); aging keeps later pieces from starving.
    - fairness: GPU time a session used in the last ~10 s (exponentially decayed) is subtracted, so one chatty or
      long-answering session cannot monopolise the GPU.
* a queued job whose HTTP client disconnected (barge-in, closed tab) is dropped without rendering;
* a job older than its deadline (the caller's timeout, `deadline_s`) is dropped as stale;
* admission: more than VOCALFACE_LIPSYNC_MAX_QUEUE waiting jobs => 503 + Retry-After, so the backend marks the piece failed
  (the call continues voice-only) instead of building an unbounded backlog.
Within one session pieces are requested one at a time by the backend, so per-session order is preserved.
"""
from __future__ import annotations

import asyncio
import collections
import os
import threading
import time

from fastapi import HTTPException

LANES = int(os.environ.get("VOCALFACE_LIPSYNC_LANES", "2"))
MAX_QUEUE = int(os.environ.get("VOCALFACE_LIPSYNC_MAX_QUEUE", "12"))
PIECE_BONUS_S = (2.0, 0.7)
FAIR_WEIGHT = float(os.environ.get("VOCALFACE_LIPSYNC_FAIR_WEIGHT", "1.0"))
FAIR_DECAY_S = 10.0


class _Job:
    __slots__ = ("sid", "piece", "fn", "fut", "loop", "t_in", "deadline", "state", "t_start", "t_end")

    def __init__(self, sid, piece, fn, fut, loop, deadline):
        self.sid, self.piece, self.fn, self.fut, self.loop = sid, piece, fn, fut, loop
        self.t_in, self.deadline, self.state = time.monotonic(), deadline, "queued"
        self.t_start = self.t_end = 0.0


class Scheduler:
    def __init__(self, lanes: int = LANES, max_queue: int = MAX_QUEUE):
        self.lanes, self.max_queue = max(1, lanes), max(1, max_queue)
        self._q: list[_Job] = []
        self._cv = threading.Condition()
        self._served: dict[str, tuple[float, float]] = {}  # sid -> (decayed GPU-seconds, last update)
        self._threads: list[threading.Thread] = []
        self._running = 0
        self.waits: collections.deque = collections.deque(maxlen=200)  # seconds queued, for /health
        self.count = collections.Counter()

    # ---- fairness ----------------------------------------------------------------------------------------------
    def _debt(self, sid: str, now: float) -> float:
        v = self._served.get(sid)
        if not v:
            return 0.0
        d, t = v
        return d * 0.5 ** ((now - t) / FAIR_DECAY_S)

    def _charge(self, sid: str, seconds: float) -> None:
        now = time.monotonic()
        self._served[sid] = (self._debt(sid, now) + seconds, now)
        if len(self._served) > 256:  # forget sessions that have been idle for a while
            for k in [k for k, (d, t) in self._served.items() if now - t > 120]:
                self._served.pop(k, None)

    def _urgency(self, j: _Job, now: float) -> float:
        bonus = PIECE_BONUS_S[j.piece] if j.piece is not None and 0 <= j.piece < len(PIECE_BONUS_S) else 0.0
        return (now - j.t_in) + bonus - FAIR_WEIGHT * self._debt(j.sid, now)

    # ---- worker lanes ------------------------------------------------------------------------------------------
    def _ensure_threads(self) -> None:
        if self._threads:
            return
        for i in range(self.lanes):
            t = threading.Thread(target=self._lane, name=f"lipsync-lane-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def _take(self) -> _Job:
        with self._cv:
            while True:
                now = time.monotonic()
                live = []
                for j in self._q:  # drop stale / cancelled jobs without rendering them
                    if j.state == "cancelled":
                        continue
                    if j.deadline and now - j.t_in > j.deadline:
                        j.state = "stale"
                        self.count["stale"] += 1
                        j.loop.call_soon_threadsafe(_set_exc, j.fut, HTTPException(504, "render request is stale", headers={"Retry-After": "1"}))
                        continue
                    live.append(j)
                self._q = live
                if self._q:
                    j = max(self._q, key=lambda x: self._urgency(x, now))
                    self._q.remove(j)
                    j.state, j.t_start = "running", now
                    self._running += 1
                    return j
                self._cv.wait(0.5)

    def _lane(self) -> None:
        while True:
            j = self._take()
            self.waits.append(j.t_start - j.t_in)
            try:
                res = j.fn()
                j.loop.call_soon_threadsafe(_set_res, j.fut, res)
            except BaseException as e:  # noqa: BLE001 - includes HTTPException from the render function
                j.loop.call_soon_threadsafe(_set_exc, j.fut, e)
            finally:
                j.t_end = time.monotonic()
                self._charge(j.sid, j.t_end - j.t_start)
                with self._cv:
                    self._running -= 1
                    self.count["done"] += 1

    # ---- API ---------------------------------------------------------------------------------------------------
    async def submit(self, request, sid: str, piece: int | None, fn, deadline_s: float = 0.0):
        with self._cv:
            if len(self._q) >= self.max_queue:
                self.count["rejected"] += 1
                raise HTTPException(503, "lip-sync queue is full", headers={"Retry-After": "1"})
        loop = asyncio.get_running_loop()
        job = _Job(sid, piece, fn, loop.create_future(), loop, deadline_s)
        self._ensure_threads()
        with self._cv:
            self._q.append(job)
            self._cv.notify()
        try:
            while not job.fut.done():
                await asyncio.wait({job.fut}, timeout=0.1)
                if job.state == "queued" and not job.fut.done() and request is not None and await request.is_disconnected():
                    with self._cv:
                        if job.state == "queued":
                            job.state = "cancelled"
                            self.count["cancelled"] += 1
                    if job.state == "cancelled":
                        raise HTTPException(499, "client closed request")
            return job.fut.result()
        except asyncio.CancelledError:  # the request task itself was cancelled (server shutdown)
            with self._cv:
                if job.state == "queued":
                    job.state = "cancelled"
                    self.count["cancelled"] += 1
            raise

    def stats(self) -> dict:
        w = sorted(self.waits)
        pick = (lambda p: round(w[min(len(w) - 1, int(p * (len(w) - 1) + 0.5))] * 1000)) if w else (lambda p: None)
        with self._cv:
            depth = len([j for j in self._q if j.state == "queued"])
        return {"lanes": self.lanes, "queued": depth, "running": self._running, "max_queue": self.max_queue,
                "wait_ms_p50": pick(0.5), "wait_ms_p90": pick(0.9), "wait_ms_max": round(w[-1] * 1000) if w else None,
                **{k: self.count.get(k, 0) for k in ("done", "rejected", "cancelled", "stale")}}


def _set_res(fut, res) -> None:
    if not fut.done():
        fut.set_result(res)


def _set_exc(fut, exc) -> None:
    if not fut.done():
        fut.set_exception(exc)


SCHED = Scheduler()
