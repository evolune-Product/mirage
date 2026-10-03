"""Languages + voices catalogue, plus multilingual STT/TTS wrappers (Whisper multilingual + Kokoro voices)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import numpy as np

MODELS = Path(__file__).resolve().parents[2] / "models"

# code -> (name, whisper language code, kokoro lang code, default voice)
LANGUAGES: dict[str, dict] = {
    "en": {"name": "English (US)", "whisper": "en", "kokoro": "en-us", "default_voice": "af_heart"},
    "en-gb": {"name": "English (UK)", "whisper": "en", "kokoro": "en-gb", "default_voice": "bf_emma"},
    "es": {"name": "Spanish", "whisper": "es", "kokoro": "es", "default_voice": "ef_dora"},
    "fr": {"name": "French", "whisper": "fr", "kokoro": "fr-fr", "default_voice": "ff_siwis"},
    "hi": {"name": "Hindi", "whisper": "hi", "kokoro": "hi", "default_voice": "hf_alpha"},
    "it": {"name": "Italian", "whisper": "it", "kokoro": "it", "default_voice": "if_sara"},
    "pt": {"name": "Portuguese (BR)", "whisper": "pt", "kokoro": "pt-br", "default_voice": "pf_dora"},
    "ja": {"name": "Japanese", "whisper": "ja", "kokoro": "ja", "default_voice": "jf_alpha"},
    "zh": {"name": "Chinese (Mandarin)", "whisper": "zh", "kokoro": "cmn", "default_voice": "zf_xiaobei"},
}
# Languages Whisper understands but Kokoro cannot speak: STT works, replies fall back to the English voice.
STT_ONLY = {"de": "German", "ru": "Russian", "ar": "Arabic", "ko": "Korean", "tr": "Turkish", "nl": "Dutch",
            "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "ur": "Urdu"}
AUTO = "auto"  # whisper detects the language per utterance; replies are spoken in the detected language when supported

_PREFIX = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}
_ACCENT = {"a": "American English", "b": "British English", "e": "Spanish", "f": "French", "h": "Hindi",
           "i": "Italian", "j": "Japanese", "p": "Brazilian Portuguese", "z": "Mandarin Chinese"}
_STATIC_VOICES = ("af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole af_nova af_river af_sarah af_sky "
                  "am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa bf_alice bf_emma "
                  "bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis ef_dora em_alex em_santa ff_siwis "
                  "hf_alpha hf_beta hm_omega hm_psi if_sara im_nicola jf_alpha jf_gongitsune jf_nezumi jf_tebukuro "
                  "jm_kumo pf_dora pm_alex pm_santa zf_xiaobei zf_xiaoni zf_xiaoxiao zf_xiaoyi zm_yunjian zm_yunxi "
                  "zm_yunxia zm_yunyang").split()


# faster-whisper "base" is fine for Latin-script languages; Hindi/CJK/Indic/Arabic need "small" to be usable.
_SMALL = {"hi", "ja", "zh", "ar", "bn", "ta", "te", "ur", "ko", "ru", "tr"}


def default_stt_model(language: str | None) -> str:
    return "small" if language in _SMALL or language == AUTO else "base"


def normalize_language(code: str | None) -> str:
    c = (code or "en").strip().lower().replace("_", "-")
    if c in LANGUAGES or c in STT_ONLY or c == AUTO:
        return c
    base = c.split("-")[0]
    if base in LANGUAGES or base in STT_ONLY:
        return base
    raise ValueError(f"unsupported language '{code}'")


def lang_for_voice(voice: str) -> str:
    """Kokoro lang code for a voice id such as 'ef_dora' (also used by the video renderer)."""
    return _PREFIX.get((voice or "a")[:1], "en-us")


def default_voice(language: str) -> str:
    return LANGUAGES.get(language, LANGUAGES["en"])["default_voice"]


def available_voices() -> list[dict]:
    names = _STATIC_VOICES
    try:
        p = MODELS / "voices-v1.0.bin"
        if p.exists():
            names = sorted(np.load(p).keys())
    except Exception:
        pass
    out = []
    for n in names:
        code = n[0]
        lang = next((k for k, v in LANGUAGES.items() if v["kokoro"] == _PREFIX.get(code)), "en")
        out.append({"id": n, "language": lang, "kokoro_lang": _PREFIX.get(code, "en-us"),
                    "gender": "female" if n[1:2] == "f" else "male", "accent": _ACCENT.get(code, ""),
                    "engine": "kokoro", "default_for_language": LANGUAGES[lang]["default_voice"] == n})
    return out


def catalogue() -> dict:
    return {
        "languages": [{"code": k, "name": v["name"], "stt": True, "tts": True, "default_voice": v["default_voice"]}
                      for k, v in LANGUAGES.items()]
                     + [{"code": k, "name": n, "stt": True, "tts": False, "default_voice": None} for k, n in STT_ONLY.items()]
                     + [{"code": AUTO, "name": "Auto-detect (STT)", "stt": True, "tts": True, "default_voice": None}],
        "voices": available_voices(),
        "stt_engine": "faster-whisper (multilingual model for non-English)",
    }


# ---------------- providers ----------------

_stt_models: dict[str, object] = {}


class MultilingualSTT:
    """faster-whisper multilingual model. language=None/'auto' -> detection per utterance."""

    def __init__(self, language: str = "auto", model_size: str | None = None):
        self.language = None if language in (None, AUTO) else LANGUAGES.get(language, {}).get("whisper", language)
        self.size = model_size or os.environ.get("MIRAGE_STT_MULTI_MODEL") or default_stt_model(language)
        self.last_language: str | None = self.language

    def _model(self):
        if self.size not in _stt_models:
            from faster_whisper import WhisperModel

            _stt_models[self.size] = WhisperModel(self.size, device="cpu", compute_type="int8")
        return _stt_models[self.size]

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        def run():
            segs, info = self._model().transcribe(audio, language=self.language, vad_filter=True)
            text = " ".join(s.text.strip() for s in segs).strip()
            return text, getattr(info, "language", None)

        text, lang = await asyncio.to_thread(run)
        self.last_language = lang or self.last_language
        return text


class LanguageTTS:
    """Wraps the base TTS. English passes straight through to it; other languages use Kokoro with the right
    espeak language. `voice` 'default' resolves to the default voice of the persona language (or the language that
    STT detected when language == auto)."""

    sample_rate = 24000

    def __init__(self, base, language: str = "en", stt=None):
        self.base, self.language, self.stt = base, language, stt
        self._k = None

    def _effective_language(self) -> str:
        if self.language == AUTO and self.stt is not None and getattr(self.stt, "last_language", None):
            code = self.stt.last_language
            return code if code in LANGUAGES else "en"
        return self.language if self.language in LANGUAGES else "en"

    def _kokoro(self):
        k = getattr(self.base, "k", None)
        if k is None:
            if self._k is None:
                from kokoro_onnx import Kokoro

                self._k = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
            k = self._k
        return k

    async def synthesize(self, text: str, voice: str = "default"):
        lang = self._effective_language()
        explicit = voice not in ("default", "", None)
        if explicit:
            kl = lang_for_voice(voice)
        else:
            voice, kl = default_voice(lang), LANGUAGES[lang]["kokoro"]
        if kl == "en-us" and self.base is not None and (explicit or lang == "en"):
            async for c in self.base.synthesize(text, voice if explicit else "default"):
                yield c
            return

        def run():
            samples, _ = self._kokoro().create(text, voice=voice, speed=1.0, lang=kl)
            return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()

        yield await asyncio.to_thread(run)
