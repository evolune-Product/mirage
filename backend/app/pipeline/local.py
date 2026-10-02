"""Free, fully local providers: faster-whisper STT and Kokoro TTS (CPU, no API cost)."""
import asyncio
import re
from pathlib import Path

import numpy as np

MODELS = Path(__file__).resolve().parents[3] / "models"
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class WhisperSTT:
    def __init__(self, size: str = "base.en"):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(size, device="cpu", compute_type="int8")

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        def run():
            segs, _ = self.model.transcribe(audio, language="en", vad_filter=True)
            return " ".join(s.text.strip() for s in segs).strip()

        return await asyncio.to_thread(run)


class KokoroTTS:
    sample_rate = 24000

    def __init__(self):
        from kokoro_onnx import Kokoro

        self.k = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))

    async def synthesize(self, text: str, voice: str = "af_heart"):
        voice = "af_heart" if voice == "default" else voice

        def run():
            samples, sr = self.k.create(text, voice=voice, speed=1.0, lang="en-us")
            return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()

        yield await asyncio.to_thread(run)


async def sentences(token_stream):
    """Chunk an LLM token stream into sentences so TTS starts before the LLM finishes
    (the main latency optimization)."""
    buf, first = "", True
    async for tok in token_stream:
        buf += tok
        if first and len(buf.split()) >= 6 and re.search(r"[,;:]\s", buf):
            head, rest = re.split(r"[,;:]\s", buf, maxsplit=1)
            yield head.strip() + ","
            buf, first = rest, False
            continue
        parts = SENTENCE_END.split(buf)
        for s in parts[:-1]:
            if s.strip():
                yield s.strip()
                first = False
        buf = parts[-1]
    if buf.strip():
        yield buf.strip()
