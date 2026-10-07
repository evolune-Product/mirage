"""Admission control and capacity telemetry for live conversations (agent "cap").

One process can only run so many simultaneous conversations before every one of them gets worse (event-loop CPU for VAD,
the single GPU for the live face, one TTS engine, one Ollama). Rather than let all of them degrade, the WebSocket entry
points (routers/realtime.py, and through it the guest links) ask this module for a slot first:

    dec = await CONTROLLER.admit(cid, wants_face)
    if not dec.granted: -> JSON {"type":"busy","retry_after_s":N,...} + close code 1013 ("try again later")
    ...                      (a face session that finds no face slot is *degraded* to voice-only when voice has room)
    CONTROLLER.release(dec.ticket)

Budgets (env, read when the controller is created; 0 = unlimited):
    VOCALFACE_MAX_CONVOS        total simultaneous conversations per process (voice-only + live-face)   default DEFAULT_MAX_CONVOS
    VOCALFACE_MAX_FACE_CONVOS   how many of those may have the live lip-synced face (the heavy ones)    default DEFAULT_MAX_FACE
    VOCALFACE_FACE_OVERFLOW     voice (default): a face session without a free face slot continues voice-only; reject: refuse it
    VOCALFACE_ADMISSION_QUEUE_S wait this long for a slot before refusing (default 0 = refuse at once)
    VOCALFACE_ADMISSION_QUEUE_MAX  max waiting connections when queueing is on (default 16)
    VOCALFACE_BUSY_RETRY_S      base "retry in N s" hint sent to refused clients (default 8, +-25 % jitter)
The defaults come from the measurements in docs/overnight/capacity.md (M1 Pro, 34 GB). Multi-process deployments get the
limit *per process* and need sticky routing per conversation (reconnects are matched per process).

The module also owns cheap process telemetry (event-loop lag, asyncio tasks, threads, open fds, RSS) because those are the
numbers that reveal saturation and leaks: /health/deep and /metrics expose them next to current/limit.
"""
from __future__ import annotations

import asyncio
import collections
import os
import random
import threading
import time
from dataclasses import dataclass, field

# Measured on an M1 Pro / 34 GB sharing the machine with other jobs; see docs/overnight/capacity.md for the evidence.
DEFAULT_MAX_CONVOS = 6
DEFAULT_MAX_FACE = 3


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(eq=False)
class Ticket:
    cid: str
    face: bool
    since: float = field(default_factory=time.monotonic)


@dataclass
class Decision:
    granted: bool
    face: bool = False          # the session may start the live face
    degraded: bool = False      # it wanted the face but only got voice
    ticket: Ticket | None = None
    reason: str = ""
    retry_after_s: int = 0
    waited_s: float = 0.0


class Admission:
    def __init__(self, max_total: int | None = None, max_face: int | None = None, queue_s: float | None = None,
                 queue_max: int | None = None, face_overflow: str | None = None, retry_s: float | None = None):
        self.max_total = _int_env("VOCALFACE_MAX_CONVOS", DEFAULT_MAX_CONVOS) if max_total is None else max_total
        self.max_face = _int_env("VOCALFACE_MAX_FACE_CONVOS", DEFAULT_MAX_FACE) if max_face is None else max_face
        self.queue_s = _float_env("VOCALFACE_ADMISSION_QUEUE_S", 0.0) if queue_s is None else queue_s
        self.queue_max = _int_env("VOCALFACE_ADMISSION_QUEUE_MAX", 16) if queue_max is None else queue_max
        self.face_overflow = (os.environ.get("VOCALFACE_FACE_OVERFLOW", "voice") if face_overflow is None else face_overflow).lower()
        self.retry_s = _float_env("VOCALFACE_BUSY_RETRY_S", 8.0) if retry_s is None else retry_s
        self._active: dict[str, Ticket] = {}
        self._waiters: collections.deque = collections.deque()
        self.counts = collections.Counter()
        self.durations: collections.deque = collections.deque(maxlen=50)

    # ---- state -----------------------------------------------------------------------------------------------
    @property
    def current(self) -> int:
        return len(self._active)

    @property
    def face_current(self) -> int:
        return sum(1 for t in self._active.values() if t.face)

    @property
    def voice_current(self) -> int:
        return self.current - self.face_current

    def _decide(self, cid: str, wants_face: bool) -> tuple[bool, bool, str]:
        """-> (granted, face, reason). A reconnect for a cid that already holds a slot never counts twice."""
        held = self._active.get(cid)
        total = self.current - (1 if held else 0)
        face_n = self.face_current - (1 if held and held.face else 0)
        if self.max_total and total >= self.max_total:
            return False, False, "server busy: all conversation slots are in use"
        if not wants_face:
            return True, False, ""
        if not self.max_face or face_n < self.max_face:
            return True, True, ""
        if self.face_overflow == "voice":
            return True, False, "live-face slots are full: continuing voice-only"
        return False, False, "server busy: all live-face slots are in use"

    def would_admit(self, wants_face: bool = False) -> bool:
        """Cheap pre-check for HTTP endpoints (guest link start) so a visitor learns about 'busy' before the WebSocket."""
        return self._decide("", wants_face)[0]

    def retry_after(self) -> int:
        base = self.retry_s
        if self.durations:  # sessions that just ended are a hint, but never promise more than we can keep
            base = min(max(base, 1.0), 30.0)
        return max(1, int(round(base * random.uniform(0.75, 1.25))))

    # ---- admission -------------------------------------------------------------------------------------------
    def _grant(self, cid: str, face: bool, degraded: bool, reason: str, waited: float) -> Decision:
        t = Ticket(cid, face)
        self._active[cid] = t
        self.counts["admitted"] += 1
        if degraded or reason:
            self.counts["degraded"] += 1
        return Decision(True, face=face, degraded=bool(reason) and not face, ticket=t, reason=reason, waited_s=waited)

    async def admit(self, cid: str, wants_face: bool, on_queued=None) -> Decision:
        ok, face, reason = self._decide(cid, wants_face)
        if ok:
            return self._grant(cid, face, wants_face and not face, reason, 0.0)
        if self.queue_s <= 0 or len(self._waiters) >= self.queue_max:
            self.counts["rejected"] += 1
            return Decision(False, reason=reason, retry_after_s=self.retry_after())
        # optional bounded wait for a slot, first come first served (polling at 100 ms: the queue is tiny and optional)
        me = object()
        self._waiters.append(me)
        self.counts["queued"] += 1
        t0 = time.monotonic()
        try:
            if on_queued:
                try:
                    await on_queued(len(self._waiters), self.queue_s)
                except Exception:  # noqa: BLE001
                    pass
            while time.monotonic() - t0 < self.queue_s:
                await asyncio.sleep(0.1)
                if self._waiters and self._waiters[0] is me:
                    ok, face, reason = self._decide(cid, wants_face)
                    if ok:
                        self._waiters.popleft()
                        return self._grant(cid, face, wants_face and not face, reason, time.monotonic() - t0)
        finally:
            if me in self._waiters:
                self._waiters.remove(me)
        self.counts["rejected"] += 1
        return Decision(False, reason=reason or "server busy", retry_after_s=self.retry_after(), waited_s=time.monotonic() - t0)

    def release(self, ticket: Ticket | None) -> None:
        if ticket is None:
            return
        if self._active.get(ticket.cid) is ticket:  # a replaced (reconnected) socket must not free its successor's slot
            self._active.pop(ticket.cid, None)
            self.durations.append(time.monotonic() - ticket.since)

    # ---- reporting -------------------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        full = bool(self.max_total and self.current >= self.max_total)
        return {"current": self.current, "limit": self.max_total, "voice_current": self.voice_current,
                "face_current": self.face_current, "face_limit": self.max_face, "accepting": not full,
                "queued": len(self._waiters), "queue_s": self.queue_s,
                "face_overflow": self.face_overflow, **{k: self.counts.get(k, 0) for k in ("admitted", "rejected", "degraded")}, "queued_total": self.counts.get("queued", 0)}


CONTROLLER = Admission()


def reset(**kw) -> Admission:
    """Replace the process-wide controller (tests, or re-reading the env)."""
    global CONTROLLER
    CONTROLLER = Admission(**kw)
    return CONTROLLER


# ---------------------------------------------------------------------------------------------------------------
# process telemetry
# ---------------------------------------------------------------------------------------------------------------
class LoopLag:
    """Sleeps 250 ms in a loop and records how late it wakes up: a blocked event loop (CPU-bound handlers, synchronous
    inference) shows up here before anything else does. Exposes the last value and the max of the last ~30 s."""

    def __init__(self, interval: float = 0.25):
        self.interval = interval
        self.last = 0.0
        self.recent: collections.deque = collections.deque(maxlen=int(30 / interval))
        self.task: asyncio.Task | None = None
        self.by_coro: dict = {}
        self.tasks: int | None = None  # sampled on the loop, so sync (threadpool) endpoints can report it too
        self._loop = None

    def ensure(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self.task is None or self.task.done() or self._loop is not loop:
            self._loop = loop
            self.task = loop.create_task(self._run())

    async def _run(self) -> None:
        while True:
            t0 = time.monotonic()
            await asyncio.sleep(self.interval)
            self.last = max(0.0, time.monotonic() - t0 - self.interval)
            self.recent.append(self.last)
            ts = asyncio.all_tasks()
            self.tasks = len(ts)
            if os.environ.get("VOCALFACE_DEBUG_TASKS"):  # leak hunting: which coroutines are the pending tasks
                c = collections.Counter(getattr(t.get_coro(), "__qualname__", "?") for t in ts)
                self.by_coro = dict(c.most_common(12))

    @property
    def max30(self) -> float:
        return max(self.recent) if self.recent else 0.0


LAG = LoopLag()


def process_stats() -> dict:
    """Cheap leak/saturation indicators for this process (never raises)."""
    out: dict = {"threads": threading.active_count()}
    try:
        out["asyncio_tasks"] = len(asyncio.all_tasks(asyncio.get_running_loop()))
    except RuntimeError:
        out["asyncio_tasks"] = LAG.tasks
    try:
        out["open_fds"] = len(os.listdir("/dev/fd" if os.path.isdir("/dev/fd") else "/proc/self/fd"))
    except OSError:
        out["open_fds"] = None
    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        out["peak_rss_mb"] = round(rss / (1024 * 1024 if os.uname().sysname == "Darwin" else 1024), 1)
    except Exception:  # noqa: BLE001
        out["peak_rss_mb"] = None
    try:  # current (not peak) RSS: ps works on macOS and Linux
        import subprocess

        out["rss_mb"] = round(int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=1).stdout.strip()) / 1024, 1)
    except Exception:  # noqa: BLE001
        out["rss_mb"] = None
    if LAG.by_coro:
        out["tasks_by_coro"] = LAG.by_coro
    out["loop_lag_ms"] = round(LAG.last * 1000, 1)
    out["loop_lag_max30_ms"] = round(LAG.max30 * 1000, 1)
    return out


def capacity_report() -> dict:
    LAG.ensure()
    return {**CONTROLLER.snapshot(), "process": process_stats()}


def prometheus_lines() -> list[str]:
    """Gauges/counters for metrics.render_metrics()."""
    c = CONTROLLER
    p = process_stats()
    L = ["# HELP vocalface_conversations_live Live WebSocket conversations in this process holding an admission slot",
         "# TYPE vocalface_conversations_live gauge",
         f'vocalface_conversations_live{{kind="voice"}} {c.voice_current}', f'vocalface_conversations_live{{kind="face"}} {c.face_current}',
         "# HELP vocalface_conversations_limit Admission limits (0 = unlimited)", "# TYPE vocalface_conversations_limit gauge",
         f'vocalface_conversations_limit{{kind="total"}} {c.max_total}', f'vocalface_conversations_limit{{kind="face"}} {c.max_face}',
         "# HELP vocalface_admission_total Admission decisions since start", "# TYPE vocalface_admission_total counter"]
    L += [f'vocalface_admission_total{{result="{k}"}} {c.counts.get(k, 0)}' for k in ("admitted", "rejected", "degraded", "queued")]
    for name, help_, key in (("vocalface_event_loop_lag_seconds", "Event-loop scheduling delay (max over the last 30 s)", None),
                             ("vocalface_process_open_fds", "Open file descriptors", "open_fds"),
                             ("vocalface_process_threads", "Python threads", "threads"),
                             ("vocalface_asyncio_tasks", "Pending asyncio tasks", "asyncio_tasks"),
                             ("vocalface_process_rss_mb", "Resident memory (MB)", "rss_mb")):
        val = LAG.max30 if key is None else p.get(key)
        if val is not None:
            L += [f"# HELP {name} {help_}", f"# TYPE {name} gauge", f"{name} {val:g}"]
    return L
