"""Conversation session orchestrator: 16 kHz PCM in -> turn-taking -> STT -> LLM -> TTS -> 24 kHz PCM out.

Transport-agnostic: the caller supplies `send_json` / `send_bytes` coroutines. Providers are created
through a replaceable factory so tests can inject fakes and real models load lazily, once.
"""
import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .local import sentences
from .turn_taking import TurnTaker

FRAME_MS = 20
FRAME_BYTES = 16000 * 2 * FRAME_MS // 1000  # 640
BARGE_IN_FRAMES = 5  # 100 ms of continuous speech while agent talks => interrupt
PREROLL_FRAMES = 10  # 200 ms kept before detected speech onset
MIN_UTTERANCE_MS = 300
MAX_HISTORY = 20


@dataclass
class Providers:
    stt: Any
    llm: Any
    tts: Any


_singletons: dict[str, Any] = {}


def default_provider_factory(llm_spec: str = "") -> Providers:
    """Real local models. STT/TTS are loaded once and shared; LLM client is cheap."""
    from .local import KokoroTTS, WhisperSTT
    from .providers import OllamaLLM

    if "stt" not in _singletons:
        _singletons["stt"] = WhisperSTT()
    if "tts" not in _singletons:
        _singletons["tts"] = KokoroTTS()
    model = os.environ.get("MIRAGE_LLM") or (llm_spec.split("/", 1)[-1] if llm_spec else "llama3.2:1b")
    return Providers(_singletons["stt"], OllamaLLM(model), _singletons["tts"])


_factory: Callable[[str], Providers] = default_provider_factory


def set_provider_factory(f: Callable[[str], Providers] | None) -> None:
    global _factory
    _factory = f or default_provider_factory


def get_providers(llm_spec: str = "") -> Providers:
    return _factory(llm_spec)


def build_system_prompt(persona) -> str:
    sp = persona.system_prompt.strip()
    sp += "\n\nYou are speaking aloud in a live voice conversation. Reply in at most two short spoken sentences (under 35 words total). No markdown, lists or emojis."
    if persona.knowledge.strip():
        sp += "\n\nKnowledge you may use:\n" + persona.knowledge.strip()
    return sp


class Session:
    def __init__(self, providers: Providers, system: str, voice: str,
                 send_json: Callable[[dict], Awaitable[None]],
                 send_bytes: Callable[[bytes], Awaitable[None]],
                 end_of_turn_ms: int = 600, energy_threshold: float = 500.0,
                 retriever: Callable[[str], str] | None = None):
        self.p, self.system, self.voice = providers, system, voice
        self.retriever = retriever  # query -> context text from the persona's knowledge base
        self.send_json, self.send_bytes = send_json, send_bytes
        self.turn = TurnTaker(end_of_turn_ms=end_of_turn_ms, energy_threshold=energy_threshold)
        self.history: list[dict] = []
        self._rem = b""
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        self._utt: list[bytes] = []
        self._speech_run = 0
        self.reply_task: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()
        self.started = time.monotonic()
        self.metrics: dict[str, float] = {}

    # ---- helpers ----
    @property
    def agent_speaking(self) -> bool:
        return self.reply_task is not None and not self.reply_task.done()

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.started

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._jobs.add(t)
        t.add_done_callback(self._jobs.discard)
        return t

    # ---- input ----
    async def feed(self, data: bytes) -> None:
        """Accepts arbitrary-sized int16 16 kHz mono PCM; slices to 20 ms frames."""
        buf = self._rem + data
        n = len(buf) // FRAME_BYTES * FRAME_BYTES
        self._rem = buf[n:]
        for i in range(0, n, FRAME_BYTES):
            await self._frame(buf[i:i + FRAME_BYTES])

    async def _frame(self, f: bytes) -> None:
        was_speaking = self.turn.s.speaking
        ev = self.turn.push(f, FRAME_MS)
        if ev == "speech":
            if not was_speaking:
                self._utt = list(self._preroll)
                await self.send_json({"type": "speech_start"})
            self._utt.append(f)
            self._speech_run += 1
            if self.agent_speaking and self._speech_run >= BARGE_IN_FRAMES:
                await self.interrupt()
        else:
            self._speech_run = 0
            if self.turn.s.speaking or ev == "end_of_turn":
                self._utt.append(f)
            else:
                self._preroll.append(f)
            if ev == "end_of_turn":
                pcm, self._utt = b"".join(self._utt), []
                self._preroll.clear()
                if len(pcm) * 1000 // 32 >= MIN_UTTERANCE_MS:
                    await self._start_turn(pcm)

    async def interrupt(self) -> None:
        """Barge-in: cancel in-flight reply (LLM stream + TTS)."""
        t = self.reply_task
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
            await self.send_json({"type": "interrupted"})

    async def _start_turn(self, pcm: bytes) -> None:
        await self.interrupt()  # a new final user turn always supersedes a running reply
        self.reply_task = asyncio.create_task(self._reply(pcm))

    # ---- reply pipeline ----
    async def _reply(self, pcm: bytes) -> None:
        t0 = time.monotonic()
        spoken: list[str] = []
        user_text = ""
        try:
            user_text = (await self.p.stt.transcribe(pcm, 16000)).strip()
            self.metrics["stt_s"] = time.monotonic() - t0
            if not user_text:
                return
            await self.send_json({"type": "transcript", "role": "user", "text": user_text})
            await self.send_json({"type": "agent_start"})
            first = True
            system = self.system
            if self.retriever:
                try:
                    ctx = await asyncio.to_thread(self.retriever, user_text)
                    if ctx:
                        system += "\n\nRelevant documents:\n" + ctx
                except Exception:  # retrieval must never break a live turn
                    pass
            async for sent in sentences(self.p.llm.stream(system, self.history[-MAX_HISTORY:], user_text)):
                async for chunk in self.p.tts.synthesize(sent, self.voice):
                    if first:
                        self.metrics["ttfa_s"] = time.monotonic() - t0
                        first = False
                    await self.send_bytes(chunk)
                spoken.append(sent)  # only count sentences whose audio was fully sent
                await self.send_json({"type": "transcript", "role": "agent", "text": sent})
            await self.send_json({"type": "agent_done"})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # e.g. LLM model missing: tell the client instead of failing silently
            await self.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"[:300]})
        finally:
            # Called on completion and on cancel: history keeps only what was actually voiced.
            if user_text:
                self.history.append({"role": "user", "content": user_text})
                if spoken:
                    self.history.append({"role": "assistant", "content": " ".join(spoken)})

    async def close(self) -> None:
        await self.interrupt()
        for t in list(self._jobs):
            t.cancel()
