"""Per-conversation perception: sample frames, describe them in the background with a small local VLM, keep a rolling
"what the user sees" context for the LLM prompt, and answer on-demand `look` requests.

Hard rules:
* never blocks the voice turn: frames are analysed by a background task, newest frame wins, nothing queues up;
* frames are held in RAM only (optionally written to disk when the persona's `store_frames` flag is set);
* no face identification: the VLM prompts forbid naming people, and nothing here does recognition;
* nothing is analysed unless the persona enabled it AND the account owner acknowledged consent AND (by default) the
  end user opted in for this conversation.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .vlm import PROMPTS, VLM

log = logging.getLogger("mirage.perception")
MAX_FRAME_BYTES = 1_500_000
MAX_SIDE = 768  # frames are downscaled to this before the VLM (speed); ample for scene + large text
STALE_S = {"camera": 45.0, "screen": 90.0}  # an older observation is described as old, then dropped
UNCHANGED_DIFF = 3.0  # mean abs diff on a 24x24 grayscale thumbnail below which a frame counts as "same scene"
RECHECK_S = 20.0  # an unchanged scene is still re-described this often

# Utterances that need a fresh look right now (fast regex; the rolling context covers everything else).
_VISION = re.compile(
    r"\b(see|look(ing)?\s+(at|like)|show(ing)?|holding|wearing|screen|camera|webcam|read (this|that|it|the)|"
    r"what('s| is) (this|that|on)|can you (see|read)|in front of me|behind me|my (desk|room|hand|face|shirt|background)|"
    r"this (page|document|slide|picture|image|photo|error|code|chart))\b", re.I)


def wants_vision(text: str) -> bool:
    return bool(_VISION.search(text or ""))


@dataclass
class Obs:
    text: str
    t: float
    ms: float
    source: str


def _prep(jpeg: bytes) -> tuple[bytes, bytes]:
    """-> (jpeg downscaled to MAX_SIDE for the VLM, 24x24 grayscale thumbnail bytes for change detection)."""
    from PIL import Image

    im = Image.open(io.BytesIO(jpeg))
    im.load()
    if im.width * im.height > 40_000_000:
        raise ValueError("image too large")
    im = im.convert("RGB")
    thumb = im.resize((24, 24)).convert("L").tobytes()
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=75)
    return b.getvalue(), thumb


def _diff(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(len(a), 1)


class PerceptionManager:
    def __init__(self, cfg: dict, vlm: Any = None, send_json: Optional[Callable[[dict], Awaitable[None]]] = None,
                 gate: Optional[Callable[[], bool]] = None, store_dir: Optional[Path] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = {"enabled": False, "consent_acknowledged": False, "require_user_consent": True, "camera": True,
                    "screen": True, "store_frames": False, "interval_s": 3.0, **cfg}
        self.vlm = vlm or VLM(self.cfg.get("vlm_model") or "")
        self.send_json, self.gate, self.store_dir, self.clock = send_json, gate, store_dir, clock
        self.user_ok = not self.cfg["require_user_consent"]
        self.obs: dict[str, deque[Obs]] = {"camera": deque(maxlen=3), "screen": deque(maxlen=3)}
        self._pending: dict[str, tuple[bytes, bytes]] = {}  # source -> (vlm jpeg, thumb): newest frame only
        self._last_accept: dict[str, float] = {}
        self._last_thumb: dict[str, bytes] = {}
        self._latest_jpeg: dict[str, bytes] = {}  # RAM only; used by look()
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._closed = False
        self._stored = 0
        self.stats = {"accepted": 0, "dropped_rate": 0, "dropped_unchanged": 0, "described": 0, "errors": 0,
                      "vlm_ms": []}

    # ---- policy ----
    @property
    def allowed(self) -> bool:
        return bool(self.cfg["enabled"] and self.cfg["consent_acknowledged"] and self.user_ok and not self._closed)

    def status_reason(self) -> str:
        if not self.cfg["enabled"]:
            return "perception is not enabled for this persona"
        if not self.cfg["consent_acknowledged"]:
            return "persona owner has not acknowledged consent"
        if not self.user_ok:
            return "waiting for the user to opt in"
        return "ok"

    async def _status(self) -> None:
        if self.send_json:
            await self.send_json({"type": "perception_status", "enabled": self.allowed, "reason": self.status_reason()})

    # ---- client messages ----
    async def handle_message(self, m: dict) -> None:
        """Entry point for the WebSocket text messages 'perception' and 'frame'. Never raises."""
        try:
            t = m.get("type")
            if t == "perception":
                on = bool(m.get("enabled"))
                self.user_ok = on
                if not on:
                    self.forget()
                await self._status()
            elif t == "frame":
                try:
                    raw = base64.b64decode(m.get("jpeg_b64") or "", validate=False)
                except (binascii.Error, ValueError):
                    return
                res = self.submit(str(m.get("source") or "camera"), raw)
                if res.startswith("rejected") and not self.allowed:
                    await self._status()
        except Exception:  # noqa: BLE001 - a bad frame must never break the call
            log.exception("perception message failed")

    def forget(self) -> None:
        """User opted out / call ended: drop everything seen."""
        for d in self.obs.values():
            d.clear()
        self._pending.clear()
        self._latest_jpeg.clear()
        self._last_thumb.clear()

    # ---- frame intake ----
    def submit(self, source: str, jpeg: bytes) -> str:
        """Returns accepted | dropped_rate | dropped_unchanged | rejected_<why>. Cheap and synchronous."""
        if source not in self.obs or not self.cfg.get(source, True):
            return "rejected_source"
        if not self.allowed:
            return "rejected_consent"
        if not jpeg or len(jpeg) > MAX_FRAME_BYTES:
            return "rejected_size"
        now = self.clock()
        if now - self._last_accept.get(source, -1e9) < float(self.cfg["interval_s"]):
            self.stats["dropped_rate"] += 1
            return "dropped_rate"
        try:
            small, thumb = _prep(jpeg)
        except Exception:  # noqa: BLE001
            return "rejected_image"
        last = self.obs[source][-1] if self.obs[source] else None
        prev = self._last_thumb.get(source)
        if prev is not None and last is not None and _diff(prev, thumb) < UNCHANGED_DIFF and now - last.t < RECHECK_S:
            self.stats["dropped_unchanged"] += 1
            self._last_accept[source] = now
            return "dropped_unchanged"
        self._last_accept[source] = now
        self._last_thumb[source] = thumb
        self._latest_jpeg[source] = small
        self._pending[source] = (small, thumb)
        self.stats["accepted"] += 1
        if self.cfg["store_frames"] and self.store_dir is not None:
            self._store(source, small)
        self._ensure_task()
        self._wake.set()
        return "accepted"

    def _store(self, source: str, jpeg: bytes) -> None:
        try:
            if self._stored >= 100:
                return
            self.store_dir.mkdir(parents=True, exist_ok=True)
            (self.store_dir / f"{int(time.time())}_{source}_{self._stored}.jpg").write_bytes(jpeg)
            self._stored += 1
        except OSError:
            pass

    # ---- background description ----
    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._worker())

    async def _worker(self) -> None:
        while not self._closed:
            if not self._pending:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 30)
                except asyncio.TimeoutError:
                    return  # idle: the task restarts on the next accepted frame
                continue
            if self.gate is not None and self.gate():  # agent is speaking: do not compete for the GPU
                await asyncio.sleep(0.25)
                continue
            source = next(iter(self._pending))
            small, _ = self._pending.pop(source)
            await self._describe(source, small, PROMPTS[source])

    async def _describe(self, source: str, jpeg: bytes, prompt: str) -> Optional[Obs]:
        t0 = self.clock()
        try:
            text = await self.vlm.describe(jpeg, prompt)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.stats["errors"] += 1
            log.warning("vlm failed: %s", e)
            return None
        text = " ".join((text or "").split())[:600]
        if not text or self._closed or not self.user_ok:  # opted out while we were busy -> discard
            return None
        o = Obs(text, self.clock(), (self.clock() - t0) * 1000, source)
        self.obs[source].append(o)
        self.stats["described"] += 1
        self.stats["vlm_ms"].append(round(o.ms))
        if self.send_json:
            try:
                await self.send_json({"type": "scene", "source": source, "text": text, "ms": round(o.ms)})
            except Exception:  # noqa: BLE001
                pass
        return o

    # ---- reading ----
    def context(self) -> str:
        """Prompt block describing what the user currently shows; '' when nothing (fresh) is known."""
        if not self.allowed:
            return ""
        now, lines = self.clock(), []
        for src, label in (("camera", "the user's camera"), ("screen", "the user's shared screen")):
            if not self.obs[src]:
                continue
            o = self.obs[src][-1]
            age = now - o.t
            if age > STALE_S[src] * 3:
                continue
            ago = "just now" if age < 5 else f"{int(age)} s ago"
            stale = " (may be outdated)" if age > STALE_S[src] else ""
            lines.append(f"- {label} ({ago}){stale}: {o.text}")
        if not lines:
            return ""
        return ("You can see the user through live video. This is what you currently see (an automatic description; it may "
                "contain mistakes):\n" + "\n".join(lines) +
                "\nAnswer questions about what they show or hold from this. Do not identify anyone by name or guess private "
                "traits. If asked something this does not show, say you can't tell from the picture. Do not mention "
                "'descriptions'; speak as if you are looking at them.")

    async def look(self, question: str = "", source: str = "", timeout: float = 8.0) -> str:
        """On-demand fresh look at the newest frame (ignores the rate limiter and the GPU gate). '' if nothing to look at."""
        if not self.allowed:
            return ""
        src = source if source in self.obs else ("screen" if re.search(r"screen|page|slide|document|tab|code|error", question or "", re.I)
                                                 and "screen" in self._latest_jpeg else "camera")
        jpeg = self._latest_jpeg.get(src) or next(iter(self._latest_jpeg.values()), None)
        if jpeg is None:
            return ""
        if jpeg is not self._latest_jpeg.get(src):  # fell back to the other source
            src = "screen" if src == "camera" else "camera"
        prompt = (f"{PROMPTS[src]}\nThe user asks: \"{question[:300]}\". Focus on what is relevant to that question."
                  if question else PROMPTS[src])
        try:
            o = await asyncio.wait_for(self._describe(src, jpeg, prompt), timeout)
        except asyncio.TimeoutError:
            return ""
        return o.text if o else ""

    def close(self) -> None:
        self._closed = True
        self.forget()
        if self._task:
            self._task.cancel()
        self._wake.set()


class PerceptiveLLM:
    """Wraps the live LLM: injects the rolling scene context into the system prompt and, when the user's words ask about
    what is visible, first takes a fresh look (bounded wait) so the answer reflects the current frame."""

    def __init__(self, inner, mgr: PerceptionManager, look_timeout: float = 6.0):
        self.inner, self.mgr, self.look_timeout = inner, mgr, look_timeout

    def __getattr__(self, name):  # warmup(), model, ... pass through
        return getattr(self.inner, name)

    async def stream(self, system: str, history: list[dict], user: str):
        try:
            if self.mgr.allowed and self.mgr._latest_jpeg and wants_vision(user):
                await self.mgr.look(user, timeout=self.look_timeout)
            ctx = self.mgr.context()
        except Exception:  # noqa: BLE001
            ctx = ""
        if not ctx and self.mgr.allowed and wants_vision(user):
            ctx = ("The user's camera/screen feed is on but nothing has been seen yet. If they ask what you see, say you "
                   "cannot see anything clearly right now.")
        if ctx:
            system = system + "\n\n" + ctx
        async for tok in self.inner.stream(system, history, user):
            yield tok


def attach(db_session, persona, sess, send_json, data_dir: Path | None = None, conversation_id: str = "") -> Optional[PerceptionManager]:
    """Build a manager for this conversation if the persona has perception enabled, and wrap the session's LLM.
    Returns None (and changes nothing) otherwise - zero overhead for personas that do not use it."""
    from ..models_perception import PerceptionConfig
    from sqlmodel import SQLModel

    from .. import db as _db

    try:
        SQLModel.metadata.create_all(_db.engine)
        c = db_session.get(PerceptionConfig, persona.id)
    except Exception:  # noqa: BLE001
        return None
    if c is None or not c.enabled:
        return None
    cfg = {k: getattr(c, k) for k in ("enabled", "consent_acknowledged", "require_user_consent", "camera", "screen",
                                      "store_frames", "vlm_model", "interval_s")}
    store = (data_dir / "perception" / conversation_id) if (c.store_frames and data_dir and conversation_id) else None
    mgr = PerceptionManager(cfg, send_json=send_json, gate=lambda: sess.agent_speaking, store_dir=store)
    from ..pipeline.session import Providers

    sess.p = Providers(sess.p.stt, PerceptiveLLM(sess.p.llm, mgr), sess.p.tts)
    return mgr
