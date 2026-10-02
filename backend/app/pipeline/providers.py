"""Pluggable stage providers. Each stage is a Protocol so free local models and
paid APIs are interchangeable per persona/tier (the core of the cost strategy)."""
from typing import AsyncIterator, Protocol


class STT(Protocol):
    async def transcribe(self, pcm: bytes, sample_rate: int) -> str: ...


class LLM(Protocol):
    def stream(self, system: str, history: list[dict], user: str) -> AsyncIterator[str]: ...


class TTS(Protocol):
    def synthesize(self, text: str, voice: str) -> AsyncIterator[bytes]: ...


class Renderer(Protocol):
    def render(self, replica_id: str, audio: AsyncIterator[bytes]) -> AsyncIterator[bytes]: ...


class OllamaLLM:
    """Free local LLM via Ollama's streaming chat API."""

    def __init__(self, model: str = "llama3.2", host: str = "http://localhost:11434"):
        self.model, self.host = model, host

    async def stream(self, system: str, history: list[dict], user: str):
        import json

        import httpx

        msgs = [{"role": "system", "content": system}, *history, {"role": "user", "content": user}]
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream(
                "POST", f"{self.host}/api/chat", json={"model": self.model, "messages": msgs, "stream": True}
            ) as r:
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    d = json.loads(line)
                    if d.get("message", {}).get("content"):
                        yield d["message"]["content"]
                    if d.get("done"):
                        break
