"""End-to-end local voice loop check: text -> TTS -> STT -> LLM -> sentence-streamed TTS.
Measures time-to-first-audio, the latency number that matters."""
import asyncio
import time

from app.pipeline.local import KokoroTTS, WhisperSTT, sentences
from app.pipeline.providers import OllamaLLM


async def main():
    t0 = time.time()
    tts, stt = KokoroTTS(), WhisperSTT()
    llm = OllamaLLM("llama3.2:1b")
    print(f"models loaded {time.time()-t0:.1f}s")

    pcm24 = b"".join([c async for c in tts.synthesize("Hi, can you tell me what a digital twin is?")])
    import numpy as np
    a = np.frombuffer(pcm24, dtype=np.int16).astype(np.float32)
    pcm16 = np.interp(np.linspace(0, len(a), int(len(a) * 16000 / 24000), endpoint=False), np.arange(len(a)), a).astype(np.int16).tobytes()

    t1 = time.time()
    heard = await stt.transcribe(pcm16)
    print(f"STT {time.time()-t1:.2f}s -> {heard!r}")

    t2, first = time.time(), None
    async for sent in sentences(llm.stream("Answer in two short sentences.", [], heard)):
        _ = [c async for c in tts.synthesize(sent)]
        if first is None:
            first = time.time() - t2
            print(f"time to first audio after STT: {first:.2f}s  ({sent!r})")
    print(f"full reply {time.time()-t2:.2f}s")


asyncio.run(main())
