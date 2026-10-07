"""Cloned-voice providers: live (async, sentence by sentence) and offline video (blocking), both with automatic fallback
to the normal Kokoro voices on ANY failure, and both gated by the consent check in voice_clone.service.

Voice ids:  "clone:<replica_id>"  or  "clone:<replica_id>@es"  (language of the text; default en)
            "clone" alone = the video's own replica (offline only)
Chatterbox (MIT) is not incremental inside a sentence, so streaming means one chunk per chunk the session's chunker gives us;
see docs/overnight/voice-cloning.md for the measured latency (use for video, and for live only if the RTF allows)."""
import asyncio
import logging
import os
import time
from pathlib import Path

import numpy as np

from ..voice_clone import service
from ..voice_clone.sidecar import SAMPLE_RATE, CloneUnavailable, default_sidecar

log = logging.getLogger("vocalface.voice_clone")
BREAKER_FAILS = int(os.environ.get("VOCALFACE_CLONE_BREAKER_FAILS", "3"))
BREAKER_COOLDOWN_S = float(os.environ.get("VOCALFACE_CLONE_BREAKER_COOLDOWN_S", "60"))


class _Breaker:
    """After N consecutive failures stop trying the clone for a cooldown (a dead sidecar must not add its start timeout
    to every sentence)."""

    def __init__(self):
        self.fails, self.until = 0, 0.0

    def open(self) -> bool:
        return time.time() < self.until

    def ok(self) -> None:
        self.fails, self.until = 0, 0.0

    def fail(self) -> None:
        self.fails += 1
        if self.fails >= BREAKER_FAILS:
            self.until = time.time() + BREAKER_COOLDOWN_S
            self.fails = 0


class CloneRoutingTTS:
    """Wraps the base TTS (Kokoro, possibly the MLX/Fallback wrapper). Non-clone voices go straight through; unknown
    attributes (start, warmup, k, primary...) are proxied to the base so existing code keeps working."""

    sample_rate = 24000

    def __init__(self, base, sidecar=None, account_of=None):
        self.base = base
        self._sidecar = sidecar
        self.breaker = _Breaker()
        self._announced: set[str] = set()

    def __getattr__(self, name):
        if name in ("base", "_sidecar", "breaker", "_announced"):
            raise AttributeError(name)
        return getattr(self.base, name)

    @property
    def sidecar(self):
        return self._sidecar or default_sidecar()

    async def warm_voice(self, voice: str) -> None:
        """Start the sidecar and pre-compute the speaker conditioning for a persona's clone voice (before the first turn)."""
        p = service.parse_voice(voice)
        if p is None:
            return
        ref = await asyncio.to_thread(service.usable_reference, p[0])
        if ref is None:
            return
        try:
            await asyncio.to_thread(self.sidecar.prepare, ref)
            if voice not in self._announced:
                self._announced.add(voice)
                await asyncio.to_thread(service.audit_use, p[0], "live conversation")
        except Exception as e:  # noqa: BLE001
            log.warning("clone warm-up failed: %s", e)

    async def _fallback(self, text: str, lang: str):
        if lang != "en":
            from .. import languages

            async for c in languages.LanguageTTS(self.base, lang).synthesize(text, "default"):
                yield c
            return
        async for c in self.base.synthesize(text, "af_heart"):
            yield c

    async def synthesize(self, text: str, voice: str = "default"):
        p = service.parse_voice(voice)
        if p is None:
            async for c in self.base.synthesize(text, voice):
                yield c
            return
        rid, lang = p
        got = False
        ref = None if self.breaker.open() else await asyncio.to_thread(service.usable_reference, rid)
        if ref is not None:
            try:
                async for c in self.sidecar.astream(text, ref, lang):
                    got = True
                    yield c
                self.breaker.ok()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.breaker.fail()
                log.warning("cloned voice failed (%s): %s", type(e).__name__, e)
                if got:
                    return  # partial audio already sent: do not double-speak
                await asyncio.to_thread(service.audit_fallback, rid, f"{type(e).__name__}: {e}")
        async for c in self._fallback(text, lang):
            yield c


# ---------------------------------------------------------------------------- offline (video generation)
def _split(text: str, max_chars: int = 220) -> list[str]:
    import re

    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    out: list[str] = []
    for s in sents:
        while len(s) > max_chars:
            cut = max(s.rfind(", ", 0, max_chars), s.rfind(" ", 0, max_chars))
            cut = cut if cut > 40 else max_chars
            out.append(s[:cut].strip(" ,"))
            s = s[cut:].strip(" ,")
        if s:
            out.append(s)
    return out


def _write_wav(path: Path, pcm: bytes) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


class CloneAwareVoice:
    """jobs.VoiceProvider: cloned voice for `clone[:rid][@lang]`, otherwise (or on any failure) the Kokoro preset voice."""

    def __init__(self, base=None, sidecar=None):
        if base is None:
            from ..jobs import KokoroPresetVoice

            base = KokoroPresetVoice()
        self.base, self._sidecar = base, sidecar
        self.breaker = _Breaker()
        self.last_engine = "kokoro"  # what the last call actually used (tests / logs)

    def synthesize(self, text: str, out_wav: Path, voice_ref: Path | None, voice: str = "default") -> None:
        # a video may only speak with ITS OWN replica's cloned voice (rid derived from the replica directory)
        own = voice_ref.parent.name if voice_ref is not None else ""
        p = service.parse_voice(voice, own)
        if p is None:
            self.last_engine = "kokoro"
            return self.base.synthesize(text, out_wav, voice_ref, voice)
        rid, lang = p
        try:
            if not own or rid != own:  # unknown owner (no voice_ref) is refused too: we cannot prove the replica is the video's
                raise service.CloneRefused("a video can only use its own replica's cloned voice")
            if self.breaker.open():
                raise CloneUnavailable("clone engine temporarily disabled after repeated failures")
            ref = service.usable_reference(rid)
            if ref is None:
                raise service.CloneRefused("cloned voice not available (not ready, or consent missing/revoked)")
            sc = self._sidecar or default_sidecar()
            parts = [sc.synth(t, ref, lang) for t in _split(text)]
            gap = np.zeros(int(0.18 * SAMPLE_RATE), dtype=np.int16).tobytes()
            pcm = gap.join(parts)
            if len(pcm) < SAMPLE_RATE:  # < 0.5 s of audio for a real script: something went wrong
                raise CloneUnavailable("clone returned almost no audio")
            _write_wav(out_wav, pcm)
            self.breaker.ok()
            self.last_engine = "clone"
            service.audit_use(rid, f"video {out_wav.stem}")
            return
        except Exception as e:  # noqa: BLE001 - ANY failure: fall back to the preset voice rather than failing the video
            self.breaker.fail()
            log.warning("cloned voice failed for video, using Kokoro: %s: %s", type(e).__name__, e)
            service.audit_fallback(rid, f"video {out_wav.stem}: {type(e).__name__}: {e}")
            self.last_engine = "kokoro"
            from .. import languages

            fb = languages.default_voice(lang) if lang in languages.LANGUAGES else "default"
            return self.base.synthesize(text, out_wav, voice_ref, fb)


def make_default_voice():
    """Default for jobs.Deps.voice."""
    return CloneAwareVoice()


def resolve_persona_voice(persona) -> str:
    """Voice id a live conversation should use for this persona: 'clone:<rid>' only if it belongs to the persona's account
    and is ready with active consent, otherwise 'default'."""
    p = service.parse_voice(persona.tts_voice or "")
    if p is None:
        return persona.tts_voice
    rid, _ = p
    if rid and service.usable_reference(rid, persona.account_id) is not None:
        return persona.tts_voice
    log.warning("persona %s asks for cloned voice %s which is unavailable: using the default voice", persona.id, rid)
    return "default"


def wrap_tts(tts):
    """Provider-selection hook for session._make_tts: adds clone routing in front of the Kokoro engine."""
    if os.environ.get("VOCALFACE_VOICE_CLONE", "1") == "0" or isinstance(tts, CloneRoutingTTS):
        return tts
    return CloneRoutingTTS(tts)
