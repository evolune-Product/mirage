"""Conversation session orchestrator: 16 kHz PCM in -> turn-taking -> STT -> LLM -> TTS -> 24 kHz PCM out.

Transport-agnostic: the caller supplies `send_json` / `send_bytes` coroutines. Providers are created
through a replaceable factory so tests can inject fakes and real models load lazily, once.
"""
import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .echo import EchoReference
from .local import sentences
from .turn_taking import EnergyVAD, SileroVAD, TurnTaker, make_vad, utterance_complete

log = logging.getLogger("mirage.voice")
if os.environ.get("MIRAGE_LOG_VOICE"):  # per-turn stage timings on stderr
    log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)
FRAME_MS = 20
FRAME_BYTES = 16000 * 2 * FRAME_MS // 1000  # 640
BARGE_IN_FRAMES = 5  # energy VAD: 100 ms of continuous speech while agent talks => interrupt
BARGE_IN_FRAMES_SILERO = int(os.environ.get("MIRAGE_BARGE_IN_FRAMES", "5"))  # Silero: ~100 ms of confident speech
BARGE_MIN_RMS = float(os.environ.get("MIRAGE_BARGE_MIN_RMS", "600"))  # echo at -18 dB is ~330 rms; speech ~1500-3000
SPEECH_ON = 0.5  # Silero speech-probability threshold normally ...
SPEECH_ON_WHILE_AGENT_TALKS = float(os.environ.get("MIRAGE_BARGE_IN_PROB", "0.7"))  # ... stricter during agent audio (echo)
BARGE_IN_FRAMES_ECHO = int(os.environ.get("MIRAGE_BARGE_IN_FRAMES_ECHO", "8"))  # longer proof of speech when echo is suspected
ECHO_TAIL_FRAMES = 25  # keep gating this long after the last agent audio should have finished playing
PAUSE_MS = int(os.environ.get("MIRAGE_PAUSE_MS", "150"))  # silence after which STT starts speculatively
MIN_COMMIT_MS = int(os.environ.get("MIRAGE_MIN_COMMIT_MS", "350"))  # earliest early end-of-turn (complete sentence)
EARLY_COMMIT = os.environ.get("MIRAGE_EARLY_COMMIT", "1") != "0"
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
        stt = WhisperSTT()
        try:
            stt.warmup()  # first inference compiles kernels (~1 s): pay it at load, not on the first turn
        except Exception:
            pass
        _singletons["stt"] = stt
    if "tts" not in _singletons:
        _singletons["tts"] = _with_clone(_make_tts())
    model = os.environ.get("MIRAGE_LLM") or (llm_spec.split("/", 1)[-1] if llm_spec else "llama3.2:1b")
    return Providers(_singletons["stt"], OllamaLLM(model), _singletons["tts"])


def _make_tts():
    """MLX Kokoro sidecar (fast, optional) with automatic fallback to CPU/ONNX Kokoro; MIRAGE_TTS=onnx|mlx|auto."""
    from .local import KokoroTTS

    mode = os.environ.get("MIRAGE_TTS", "auto").lower()
    if mode != "onnx":
        try:
            from .mlx_tts import FallbackTTS, MlxKokoroTTS, sidecar_python

            if sidecar_python():
                mlx = MlxKokoroTTS()
                if mode == "mlx":
                    return mlx
                return FallbackTTS(mlx, lambda: _warm(KokoroTTS()))
        except Exception:
            if mode == "mlx":
                raise
    return _warm(KokoroTTS())


def _with_clone(tts):
    """Optional cloned-voice routing (voice ids 'clone:<replica_id>'); pass-through for every other voice."""
    try:
        from .providers_clone import wrap_tts

        return wrap_tts(tts)
    except Exception:  # noqa: BLE001 - cloning is optional, never break the default TTS
        return tts


def _warm(tts):
    try:
        tts.warmup()
    except Exception:
        pass
    return tts


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


PIECE_S = float(os.environ.get("MIRAGE_LIPSYNC_PIECE_S", "1.0"))
# Progressive piece sizes: the first video segment gates the first audio, and Wav2Lip render time is ~40 ms + 0.3x piece
# length, so start tiny (0.35 s => ~130 ms) and grow while earlier pieces are playing (each render < previous piece's length).
FIRST_PIECE_S = float(os.environ.get("MIRAGE_LIPSYNC_FIRST_PIECE_S", "0.35"))
SECOND_PIECE_S = float(os.environ.get("MIRAGE_LIPSYNC_SECOND_PIECE_S", "0.6"))
MIN_PIECE_S = 0.25  # a remainder shorter than this is merged into the previous piece
MIN_RENDER_S = 0.3  # shorter audio is zero-padded for rendering (server errors below ~0.2 s)
LIPSYNC_MAX_FAILS = 3  # consecutive render failures before the face is switched off
LIPSYNC_RETRY_S = float(os.environ.get("MIRAGE_LIPSYNC_RETRY_S", "8"))  # then the service is re-probed this often; when it answers the face comes back
LLM_FIRST_TOKEN_S = float(os.environ.get("MIRAGE_LLM_FIRST_TOKEN_TIMEOUT", "25"))  # a hung/overloaded Ollama must not hang the turn
LLM_STALL_S = float(os.environ.get("MIRAGE_LLM_STALL_TIMEOUT", "15"))
FALLBACK_LLM = os.environ.get("MIRAGE_FALLBACK_TEXT", "Sorry, I'm having trouble answering right now. Please try again in a moment.")
FALLBACK_STT = "Sorry, I didn't catch that. Could you say it again?"
MAX_UTTERANCE_S = float(os.environ.get("MIRAGE_MAX_UTTERANCE_S", "60"))  # bounds the per-session audio buffer (memory)


async def warmup_providers(p, system: str = "", voice: str = "") -> None:
    """Pre-pay one-time costs (LLM load + system-prompt prefill, TTS sidecar start). Call on the *unwrapped* providers
    before the first turn. Never raises."""
    async def llm():
        w = getattr(p.llm, "warmup", None)
        if w:
            await w(system or "You are a helpful assistant.")

    async def tts():
        st = getattr(p.tts, "start", None) or getattr(getattr(p.tts, "primary", None), "start", None)
        if st:
            await st()

    async def clone():
        w = getattr(p.tts, "warm_voice", None)  # cloned persona voice: start the sidecar + speaker conditioning early
        if w and voice.startswith("clone"):
            await w(voice)

    await asyncio.gather(llm(), tts(), clone(), return_exceptions=True)


class LLMTimeout(Exception):
    pass


class Session:
    def __init__(self, providers: Providers, system: str, voice: str,
                 send_json: Callable[[dict], Awaitable[None]],
                 send_bytes: Callable[[bytes], Awaitable[None]],
                 end_of_turn_ms: int = 700, energy_threshold: float = 500.0,
                 retriever: Callable[[str], str] | None = None, lipsync=None):
        self.p, self.system, self.voice = providers, system, voice
        self.retriever = retriever  # query -> context text from the persona's knowledge base
        self.lipsync = lipsync  # optional LipsyncClient-like: render(pcm24k) -> {'fps','frames'}
        self.send_json = send_json
        self._send_bytes_raw = send_bytes
        self.echo = EchoReference()  # what we sent the client to play; lets us tell echo from the user (pipeline/echo.py)
        self.ptt = False  # push-to-talk: the client marks turn boundaries, VAD/echo gating are bypassed
        self._ptt_down = False
        self._echo_flag = False
        # Silero VAD for the real local stack (or when forced via MIRAGE_VAD); unit tests that inject fake providers
        # keep the dependency-free energy gate.
        vad = make_vad(energy_threshold=energy_threshold) if (_factory is default_provider_factory or os.environ.get("MIRAGE_VAD")) else None
        self.turn = TurnTaker(end_of_turn_ms=end_of_turn_ms, energy_threshold=energy_threshold, vad=vad, pause_ms=PAUSE_MS)
        self._silero = isinstance(vad, SileroVAD)
        self.barge_frames = BARGE_IN_FRAMES_SILERO if self._silero else BARGE_IN_FRAMES
        self._spec: asyncio.Task | None = None  # speculative STT of the utterance so far
        self._piece_i = 0
        self._t0 = time.monotonic()
        self._lip_fail, self._lip_ok = 0, False
        self._lip_backup, self._lip_retry_at = None, 0.0  # face switched off by failures: re-probed later (circuit breaker)
        self._idle_phase: tuple[int, float, float] | None = None
        self.history: list[dict] = []
        self._rem = b""
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        self._utt: list[bytes] = []
        self._speech_run = 0
        self.reply_task: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()
        self.started = time.monotonic()
        self.metrics: dict[str, float] = {}

    async def send_bytes(self, b: bytes) -> None:
        self.echo.add_agent(b)
        await self._send_bytes_raw(b)

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
        self.echo.push_mic(EnergyVAD.rms(f))
        if self.ptt:
            if self._ptt_down:
                self._utt.append(f)
            return
        was_speaking = self.turn.s.speaking
        audible = self.echo.agent_audible(ECHO_TAIL_FRAMES)  # may still be playing on the client after we finished sending
        talking = self.agent_speaking or audible  # echo-robust barge-in: stricter + louder speech needed while the agent talks
        explained = False
        if talking:
            explained, _ = self.echo.assess()
            explained = explained or (self._silero and self.echo.warming())  # (energy-VAD unit tests have no echo physics)
            await self._echo_report()
        self.turn.suppress = explained  # mic frame follows the agent's own audio and is not louder than that explains
        self.turn.on = SPEECH_ON_WHILE_AGENT_TALKS if talking else SPEECH_ON
        self.turn.min_rms = BARGE_MIN_RMS if talking and self._silero else 0.0
        ev = self.turn.push(f, FRAME_MS)
        if ev == "silence" and self._can_commit_early():
            ev = self.turn.force_end()  # speculative transcript is a finished sentence: don't wait out the timeout
            self.metrics["early_commit"] = self.metrics.get("early_commit", 0) + 1
        if ev == "speech":
            if not was_speaking:
                self._utt = list(self._preroll)
                await self.send_json({"type": "speech_start"})
            else:
                self._drop_spec()  # user resumed after a pause: the speculative transcript is stale
            self._utt.append(f)
            if len(self._utt) * FRAME_MS > MAX_UTTERANCE_S * 1000:  # a never-ending monologue must not grow RAM without bound
                del self._utt[:len(self._utt) // 2]
            self._speech_run += 1
            need = BARGE_IN_FRAMES_ECHO if self._echo_flag and self.barge_frames < BARGE_IN_FRAMES_ECHO else self.barge_frames
            if talking and self._speech_run >= need:
                await self._barge()
        else:
            self._speech_run = 0
            if self.turn.s.speaking or ev == "end_of_turn":
                self._utt.append(f)
            else:
                self._preroll.append(f)
            if ev == "pause":
                self._spec_start()
            elif ev == "end_of_turn":
                pcm, self._utt = b"".join(self._utt), []
                spec, self._spec = self._spec, None
                self._preroll.clear()
                self.metrics["eot_at"] = time.monotonic()
                if len(pcm) * 1000 // 32 >= MIN_UTTERANCE_MS:
                    await self._start_turn(pcm, spec)
                elif spec:
                    spec.cancel()

    # ---- speculative transcription: STT runs during the endpointing window instead of after it ----
    def _spec_start(self) -> None:
        if self._spec is not None or len(self._utt) * FRAME_MS < MIN_UTTERANCE_MS:
            return
        pcm = b"".join(self._utt)
        self._spec = self._spawn(self.p.stt.transcribe(pcm, 16000))

    def _drop_spec(self) -> None:
        if self._spec is not None:
            self._spec.cancel()
            self._spec = None

    def _can_commit_early(self) -> bool:
        sp = self._spec
        if not (EARLY_COMMIT and sp is not None and sp.done() and not sp.cancelled() and sp.exception() is None):
            return False
        return self.turn.s.silence_ms >= MIN_COMMIT_MS and utterance_complete(sp.result())

    async def _echo_report(self) -> None:
        """Tell the client (additive `echo` event, on change only) when the agent's voice keeps coming back in the mic."""
        e = self.echo
        if e.voiced_frames >= 75:
            flag = e.suspect_fraction >= 0.5
            if flag != self._echo_flag:
                self._echo_flag = flag
                await self.send_json({"type": "echo", "suspected": flag, "score": round(e.score, 2)})
            if e.voiced_frames >= 200:
                e.reset_stats()

    async def _barge(self) -> None:
        """The user is talking over the agent: stop the reply if one is still being produced, and always tell the client
        to drop whatever it has buffered (it may be playing audio long after the server finished sending)."""
        if self.agent_speaking:
            await self.interrupt()
        else:
            self.echo.clear()
            await self.send_json({"type": "interrupted"})

    # ---- push-to-talk (client decides when the user is talking) ----
    def set_ptt(self, on: bool) -> None:
        self.ptt = bool(on)
        self._ptt_down, self._utt = False, []
        self.turn.force_end()

    async def ptt_down(self) -> None:
        if not self.ptt or self._ptt_down:
            return
        if self.agent_speaking or self.echo.agent_audible(ECHO_TAIL_FRAMES):
            await self._barge()
        self._ptt_down, self._utt = True, []
        await self.send_json({"type": "speech_start"})

    async def ptt_up(self) -> None:
        if not (self.ptt and self._ptt_down):
            return
        self._ptt_down = False
        pcm, self._utt = b"".join(self._utt), []
        self.metrics["eot_at"] = time.monotonic()
        if len(pcm) * 1000 // 32 >= MIN_UTTERANCE_MS:
            await self._start_turn(pcm, None)

    async def interrupt(self) -> None:
        """Barge-in: cancel in-flight reply (LLM stream + TTS)."""
        self.echo.clear()
        t = self.reply_task
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
            await self.send_json({"type": "interrupted"})

    async def _start_turn(self, pcm: bytes, spec: asyncio.Task | None = None) -> None:
        await self.interrupt()  # a new final user turn always supersedes a running reply
        self.reply_task = asyncio.create_task(self._reply(pcm, spec))

    # ---- audio out: lip-sync render + send ----
    async def _emit_audio(self, chunk: bytes) -> None:
        """Send one TTS chunk. With a live face it is cut into pieces (first one short so the first lip-synced frame is
        early); each piece's video segment goes first, the client pairs it with the audio that follows."""
        if not self.lipsync:
            await self.send_bytes(chunk)
            self.metrics.setdefault("ttfa_s", time.monotonic() - self._t0)
            return
        i = 0
        while i < len(chunk):
            sec = FIRST_PIECE_S if self._piece_i == 0 else SECOND_PIECE_S if self._piece_i == 1 else PIECE_S
            n = int(sec * 24000) * 2
            if len(chunk) - (i + n) < int(MIN_PIECE_S * 24000) * 2:  # never leave a tiny tail piece: merge it in
                n = len(chunk) - i
            piece = chunk[i:i + n]
            i += n
            self._piece_i += 1
            if self.lipsync:
                try:
                    need = int(MIN_RENDER_S * 24000) * 2  # the lip-sync model needs >= ~0.2 s of mel frames: pad, then trim
                    kw = self._first_piece_kwargs() if self._piece_i == 1 else {}
                    seg = await self._render(piece + bytes(max(0, need - len(piece))), piece=self._piece_i - 1, **kw)
                    frames = seg.get("frames") or []
                    if len(piece) < need:
                        frames = frames[:max(1, round(len(piece) / 48000 * seg.get("fps", 25)))]
                    if frames:
                        msg = {"type": "video_segment", "fps": seg["fps"], "frames": frames}
                        if seg.get("end_phase") is not None:  # lets the client resume its idle loop where the face left off
                            msg["end_phase"] = seg["end_phase"]
                        await self.send_json(msg)
                    self._lip_fail, self._lip_ok = 0, True
                except Exception as e:  # face is optional: never break the voice turn
                    # A service that never worked is switched off at once; one that worked gets a few retries so a
                    # transient render error costs one piece of video, not the face for the rest of the call. A timeout
                    # (hung or saturated service) switches it off at once: every further piece would stall the audio too.
                    self._lip_fail += 1
                    if isinstance(e, TimeoutError) or "Timeout" in type(e).__name__:
                        self._lip_fail = LIPSYNC_MAX_FAILS
                    if not self._lip_ok or self._lip_fail >= LIPSYNC_MAX_FAILS:
                        self._lip_off()
            await self.send_bytes(piece)
            self.metrics.setdefault("ttfa_s", time.monotonic() - self._t0)  # first audio byte on the wire

    def _lip_off(self) -> None:
        """Face unavailable: continue voice-only, remember the client so `_lip_recover` can bring the face back."""
        if self.lipsync is not None:
            self._lip_backup = self.lipsync
        self.lipsync = None
        self._lip_retry_at = time.monotonic() + LIPSYNC_RETRY_S

    async def _lip_recover(self) -> None:
        """Called at the start of each reply: if the face was switched off by failures and the service answers its health
        check again, switch it back on (the browser keeps its idle loop and simply receives video segments again)."""
        b = self._lip_backup
        if self.lipsync is not None or b is None or time.monotonic() < self._lip_retry_at:
            return
        try:
            await asyncio.wait_for(b.health(), 2.0)
        except Exception:  # noqa: BLE001 - still down (or a double without health()): try again later
            self._lip_retry_at = time.monotonic() + LIPSYNC_RETRY_S
            return
        self.lipsync, self._lip_fail = b, 0
        log.info("lip-sync service is back: face re-enabled")

    async def _render(self, pcm: bytes, piece: int, **kw) -> dict:
        """lipsync.render with the piece index (the service schedules a reply's first piece first). Clients without the
        `piece` keyword (test doubles, older services) are called exactly as before."""
        import inspect

        try:
            ok = "piece" in inspect.signature(self.lipsync.render).parameters
        except (TypeError, ValueError):
            ok = False
        return await (self.lipsync.render(pcm, piece=piece, **kw) if ok else self.lipsync.render(pcm, **kw))

    def set_idle_phase(self, phase: int, fps: float = 25.0) -> None:
        """Client reports its idle-loop ping-pong index so the first rendered piece continues from it (no head pop)."""
        self._idle_phase = (int(phase), float(fps), time.monotonic())

    def _first_piece_kwargs(self) -> dict:
        ph = self._idle_phase
        if not ph:
            return {}
        k, fps, t = ph
        # idle advances at `fps` while we synthesise; add ~0.15 s for transfer + decode before the segment is shown
        return {"phase": int(k + (time.monotonic() - t + 0.15) * fps), "fade_in": True}

    # ---- reply pipeline ----
    async def warmup(self) -> None:
        await warmup_providers(self.p, self.system)

    async def _reply(self, pcm: bytes, spec: asyncio.Task | None = None) -> None:
        t0 = self._t0 = time.monotonic()
        self.metrics = {k: v for k, v in self.metrics.items() if not k.endswith("_s")}  # per-turn stage timings
        spoken: list[str] = []
        user_text = ""
        self._piece_i = 0
        agent_started = False
        fallback = FALLBACK_STT
        try:
            await self._lip_recover()
            if spec is not None:
                try:
                    user_text = (await asyncio.shield(spec)).strip()
                except asyncio.CancelledError:
                    if spec.cancelled():  # the speculation was cancelled, not this reply
                        user_text = (await self.p.stt.transcribe(pcm, 16000)).strip()
                    else:
                        raise
                except Exception:
                    user_text = (await self.p.stt.transcribe(pcm, 16000)).strip()
            else:
                user_text = (await self.p.stt.transcribe(pcm, 16000)).strip()
            self.metrics["stt_s"] = time.monotonic() - t0
            if not user_text:
                return
            fallback = FALLBACK_LLM
            await self.send_json({"type": "transcript", "role": "user", "text": user_text})
            await self.send_json({"type": "agent_start"})
            agent_started = True
            system = self.system
            if self.retriever:
                try:
                    ctx = await asyncio.to_thread(self.retriever, user_text)
                    if ctx:
                        system += "\n\nRelevant documents:\n" + ctx
                except Exception:  # retrieval must never break a live turn
                    pass
            # Two overlapped stages joined by a queue: (LLM -> chunker -> TTS) and (lip-sync render -> send), so the
            # next sentence is synthesised while the previous one is still being rendered/sent/played.
            q: asyncio.Queue = asyncio.Queue()
            first = True

            async def tokens():
                it = self.p.llm.stream(system, self.history[-MAX_HISTORY:], user_text).__aiter__()
                n = 0
                try:
                    while True:
                        try:  # first token may legitimately take a while (queue behind other sessions); stalls must not hang
                            tok = await asyncio.wait_for(it.__anext__(), LLM_FIRST_TOKEN_S if n == 0 else LLM_STALL_S)
                        except StopAsyncIteration:
                            return
                        except asyncio.TimeoutError:
                            raise LLMTimeout(f"language model gave no {'first token' if n == 0 else 'further token'} in time") from None
                        n += 1
                        self.metrics.setdefault("llm_ft_s", time.monotonic() - t0)
                        yield tok
                finally:
                    aclose = getattr(it, "aclose", None)
                    if aclose:
                        try:
                            await aclose()
                        except BaseException:  # noqa: BLE001 - never mask the real error
                            pass

            async def synth_stage():
                try:
                    async for sent in sentences(tokens()):
                        self.metrics.setdefault("chunk1_s", time.monotonic() - t0)
                        async for chunk in self.p.tts.synthesize(sent, self.voice):
                            self.metrics.setdefault("tts_ft_s", time.monotonic() - t0)
                            q.put_nowait(("a", chunk))
                        q.put_nowait(("s", sent))
                finally:
                    q.put_nowait(None)

            async def send_stage():
                nonlocal first
                while (item := await q.get()) is not None:
                    kind, val = item
                    if kind == "a":
                        await self._emit_audio(val)
                        if first:
                            first = False
                            log.info("voice turn: %s", {k: round(v, 3) for k, v in self.metrics.items() if k.endswith("_s")})
                    else:
                        spoken.append(val)  # only count sentences whose audio was fully sent
                        await self.send_json({"type": "transcript", "role": "agent", "text": val})

            sender = asyncio.create_task(send_stage())
            try:
                await synth_stage()  # runs in this task so the LLM request starts immediately
                await sender
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            await self.send_json({"type": "agent_done"})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # e.g. LLM model missing: tell the client, and say something so the user is not left hanging
            await self.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"[:300]})
            await self._speak_fallback(fallback, agent_started)
        finally:
            # Called on completion and on cancel: history keeps only what was actually voiced.
            if user_text:
                self.history.append({"role": "user", "content": user_text})
                if spoken:
                    self.history.append({"role": "assistant", "content": " ".join(spoken)})

    async def _speak_fallback(self, text: str, agent_started: bool) -> None:
        """Best-effort spoken apology after a failed turn (LLM down/slow, STT error): voice only, never raises, always ends
        the turn with agent_done so the client leaves its 'thinking' state."""
        try:
            if not agent_started:
                await self.send_json({"type": "agent_start"})
            async for chunk in self.p.tts.synthesize(text, self.voice):
                await self.send_bytes(chunk)
            await self.send_json({"type": "transcript", "role": "agent", "text": text})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - TTS may be what failed
            pass
        try:
            await self.send_json({"type": "agent_done"})
        except Exception:  # noqa: BLE001
            pass

    async def say(self, text: str) -> None:
        """Agent speaks `text` first (persona greeting) with no LLM turn; interruptible like any reply."""
        await self.interrupt()
        self.reply_task = asyncio.create_task(self._say(text))

    async def _say(self, text: str) -> None:
        said = False
        try:
            await self.send_json({"type": "agent_start"})
            async for chunk in self.p.tts.synthesize(text, self.voice):
                if self.lipsync:
                    step = int(PIECE_S * 24000) * 2
                    for off in range(0, len(chunk), step):
                        piece = chunk[off:off + step]
                        if self.lipsync:
                            try:
                                seg = await self.lipsync.render(piece)
                                if seg.get("frames"):
                                    await self.send_json({"type": "video_segment", "fps": seg["fps"], "frames": seg["frames"]})
                            except Exception:  # face is optional
                                self._lip_off()
                        await self.send_bytes(piece)
                else:
                    await self.send_bytes(chunk)
            said = True
            await self.send_json({"type": "transcript", "role": "agent", "text": text})
            await self.send_json({"type": "agent_done"})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"[:300]})
        finally:
            if said:
                self.history.append({"role": "assistant", "content": text})

    async def close(self) -> None:
        await self.interrupt()
        for t in list(self._jobs):
            t.cancel()
