"""Free, fully local providers: faster-whisper STT and Kokoro TTS (CPU, no API cost), plus the LLM->TTS chunker.

Latency notes (measured on an M1 Pro, see docs/overnight/voice.md):
* kokoro-onnx's `create()` calls `phonemizer.phonemize()` which rebuilds the espeak backend on every call
  (~250 ms). A persistent `EspeakBackend` does the same job in <1 ms.
* Kokoro is not incremental inside one utterance, so "streaming" means feeding it small text chunks:
  the first chunk is deliberately tiny (3-8 words) so first audio is ready after ~0.3 s of synthesis.
* The int8 Kokoro build is SLOWER than fp32 on Apple Silicon (no VNNI) and CoreML EP gave no gain: stay on fp32/CPU.
"""
import asyncio
import os
import re
import threading
from pathlib import Path

import numpy as np

MODELS = Path(__file__).resolve().parents[3] / "models"
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")  # kept for backwards compatibility


class WhisperSTT:
    """faster-whisper on CPU/int8. Greedy decoding without timestamps: ~2x faster than the defaults
    (beam 5 + timestamps + a second VAD pass) with the same text on short conversational utterances."""

    def __init__(self, size: str | None = None, cpu_threads: int | None = None):
        from faster_whisper import WhisperModel

        size = size or os.environ.get("MIRAGE_STT_MODEL", "base.en")
        self.size = size
        threads = cpu_threads if cpu_threads is not None else int(os.environ.get("MIRAGE_STT_THREADS", "0"))
        self.model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=threads)
        self.vad_filter = os.environ.get("MIRAGE_STT_VAD_FILTER", "0") == "1"

    def _run(self, audio: np.ndarray) -> str:
        segs, _ = self.model.transcribe(
            audio, language="en", beam_size=1, best_of=1, temperature=0.0, vad_filter=self.vad_filter,
            condition_on_previous_text=False, without_timestamps=True,
        )
        return " ".join(s.text.strip() for s in segs).strip()

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return await asyncio.to_thread(self._run, audio)

    def warmup(self) -> None:
        self._run(np.zeros(16000, dtype=np.float32))


def _trim(a: np.ndarray, thresh: float = 0.004, pad: int = 480) -> np.ndarray:
    """Cut leading/trailing near-silence (keeps ~20 ms) so chunks join cleanly and no latency is wasted on dead air."""
    idx = np.flatnonzero(np.abs(a) > thresh)
    if len(idx) == 0:
        return a
    return a[max(0, idx[0] - pad): idx[-1] + 1 + pad]


class KokoroTTS:
    sample_rate = 24000
    PAUSE_S = {",": 0.10, ";": 0.12, ":": 0.12, ".": 0.14, "!": 0.14, "?": 0.16}  # natural gaps between chunks

    def __init__(self, model: str | None = None):
        from kokoro_onnx import Kokoro

        model = model or os.environ.get("MIRAGE_KOKORO_MODEL", "kokoro-v1.0.onnx")
        self.k = Kokoro(str(MODELS / model), str(MODELS / "voices-v1.0.bin"))
        self._ph_lock = threading.Lock()  # espeak is not thread-safe
        self._be = None
        try:  # must be created after Kokoro() (which points phonemizer at the bundled espeak library)
            from phonemizer.backend import EspeakBackend

            self._be = EspeakBackend("en-us", preserve_punctuation=True, with_stress=True)
        except Exception:
            self._be = None  # falls back to kokoro-onnx's own (slower) phonemizer

    def _phonemize(self, text: str) -> str:
        if self._be is None:
            return self.k.tokenizer.phonemize(text, "en-us")
        with self._ph_lock:
            raw = self._be.phonemize([text])[0]
        vocab = self.k.tokenizer.vocab
        return "".join(c for c in raw if c in vocab).strip()

    def _style(self, voice: str):
        voice = "af_heart" if voice in ("", "default") else voice
        if voice not in self.k.voices:
            voice = "af_heart"
        return self.k.get_voice_style(voice)

    def _infer(self, phonemes: str, style) -> np.ndarray:
        audio, _ = self.k._create_audio(phonemes, style, 1.0)
        return _trim(audio)

    @staticmethod
    def _pcm(a: np.ndarray, pad_s: float = 0.0) -> bytes:
        out = (np.clip(a, -1, 1) * 32767).astype(np.int16)
        if pad_s:
            out = np.concatenate([out, np.zeros(int(pad_s * 24000), dtype=np.int16)])
        return out.tobytes()

    async def synthesize(self, text: str, voice: str = "af_heart"):
        """Yields int16 24 kHz PCM; one yield per phoneme batch (long text is split at punctuation)."""
        style = self._style(voice)
        ph = await asyncio.to_thread(self._phonemize, text)
        batches = self.k._split_phonemes(ph) if ph else []
        tail = text.rstrip()[-1:] if text.strip() else ""
        for i, b in enumerate(batches):
            a = await asyncio.to_thread(self._infer, b, style)
            yield self._pcm(a, self.PAUSE_S.get(tail, 0.0) if i == len(batches) - 1 else 0.0)

    def warmup(self) -> None:
        style = self._style("af_heart")
        for t in ("Hello there.", "Sure, the starter plan costs nineteen dollars a month."):
            self._infer(self._phonemize(t), style)


# ---------------------------------------------------------------------------------------------------------
# LLM token stream -> speakable chunks
# ---------------------------------------------------------------------------------------------------------
FIRST_MIN_WORDS = int(os.environ.get("MIRAGE_FIRST_MIN_WORDS", "3"))  # earliest clause break for the 1st chunk
FIRST_MAX_WORDS = int(os.environ.get("MIRAGE_FIRST_MAX_WORDS", "7"))  # force the 1st chunk out after this many words
LATER_MAX_WORDS = int(os.environ.get("MIRAGE_LATER_MAX_WORDS", "22"))  # split run-on sentences at a clause
ABBREV = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "inc", "jr", "sr", "no", "e.g", "i.e", "approx", "mt"}
_SENT = re.compile(r"""([.!?]+["')\]]*)\s+""")
_CLAUSE = re.compile(r"([,;:—–])\s+|\s+[—–-]\s+")
_MD = re.compile(r"[*_`#]+")


def _find_sentence_end(buf: str) -> int:
    """Index just past the first sentence terminator followed by whitespace (skipping abbreviations), else -1."""
    for m in _SENT.finditer(buf):
        word = re.split(r"\s", buf[: m.start()])[-1].lower().strip("\"'([")
        if m.group(1) == "." and (word in ABBREV or (len(word) == 1 and word.isalpha())):
            continue
        return m.end()
    return -1


def _words_done(buf: str) -> int:
    """Number of words that are certainly complete (followed by whitespace)."""
    return len(buf.split()) - (0 if (not buf or buf[-1].isspace()) else 1)


def _clause_break(buf: str, min_words: int) -> int:
    """Index of a clause boundary (comma/semicolon/colon/dash) with at least `min_words` words before it, else -1."""
    for m in _CLAUSE.finditer(buf):
        if len(buf[: m.start()].split()) >= min_words:
            return m.end() if m.group(1) else m.start()
    return -1


def _cut_after_words(buf: str, n: int) -> int:
    i = 0
    for w in buf.split()[:n]:
        i = buf.index(w, i) + len(w)
    return i


async def sentences(token_stream):
    """Chunk an LLM token stream into pieces TTS can start on before the LLM has finished.

    The first piece is kept short (clause break after >= FIRST_MIN_WORDS words, or FIRST_MAX_WORDS words) because
    time-to-first-audio is dominated by synthesising it; later pieces are whole sentences (better prosody) and only
    very long run-ons are split at a clause."""
    buf, first = "", True
    async for tok in token_stream:
        buf += _MD.sub("", tok)
        while True:
            i = _find_sentence_end(buf)
            if i < 0 and first:
                i = _clause_break(buf, FIRST_MIN_WORDS)
                if i < 0 and _words_done(buf) >= FIRST_MAX_WORDS:
                    i = _cut_after_words(buf, FIRST_MAX_WORDS)
            elif i < 0 and _words_done(buf) >= LATER_MAX_WORDS:
                i = _clause_break(buf, 8)
            if i < 0:
                break
            out, buf = buf[:i].strip(), buf[i:].lstrip()
            if out:
                yield out
                first = False
    if buf.strip():
        yield buf.strip()
