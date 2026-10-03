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

    def __init__(self, model: str = "llama3.2", host: str | None = None):
        import os

        host = host or os.environ.get("OLLAMA_URL") or "http://localhost:11434"  # same variable as /health/deep and llm_backends
        self.model, self.host = model, host.rstrip("/")
        self.options: dict = {"num_predict": 90}
        if os.environ.get("MIRAGE_OLLAMA_NUM_CTX"):
            self.options["num_ctx"] = int(os.environ["MIRAGE_OLLAMA_NUM_CTX"])
        self.keep_alive = os.environ.get("MIRAGE_OLLAMA_KEEP_ALIVE", "30m")

    def _body(self, system: str, history: list[dict], user: str, stream: bool = True, **opts) -> dict:
        msgs = [{"role": "system", "content": system}, *history, {"role": "user", "content": user}]
        return {"model": self.model, "messages": msgs, "stream": stream, "keep_alive": self.keep_alive,
                "options": {**self.options, **opts}}

    async def warmup(self, system: str = "You are a helpful assistant.") -> None:
        """Load the model into memory and prime Ollama's prompt cache with the system prompt, so the first real
        turn does not pay the model-load or system-prompt prefill cost. Never raises."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=120) as c:
                await c.post(f"{self.host}/api/chat", json=self._body(system, [], "Hi.", stream=False, num_predict=1))
        except Exception:
            pass

    async def stream(self, system: str, history: list[dict], user: str):
        import json

        import httpx

        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", f"{self.host}/api/chat", json=self._body(system, history, user)) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")[:200]
                    raise RuntimeError(f"ollama {r.status_code}: {body}")
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    d = json.loads(line)
                    if d.get("message", {}).get("content"):
                        yield d["message"]["content"]
                    if d.get("done"):
                        break
