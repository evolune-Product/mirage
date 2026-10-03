"""Small local vision-language model through Ollama: one frame in, a short description / OCR text out.

Privacy: the JPEG bytes go to the local Ollama process only and are never written to disk here. Prompts forbid
identifying people (no names, no face recognition); the model is asked to describe, not to recognise.
"""
from __future__ import annotations

import base64
import os
import time

import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_URL", os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
DEFAULT_MODEL = os.environ.get("MIRAGE_VLM_MODEL", "gemma3:4b")

NO_ID = ("Never identify or name a person and never guess their identity, age, ethnicity or health: describe only what is "
         "visible (clothing, objects, setting, expression, visible text).")
PROMPTS = {
    "camera": "This is a frame from the user's webcam. In 1-2 short sentences say what is visible: the setting, what the "
              "person is doing or holding, and any readable text. " + NO_ID,
    "screen": "This is the user's shared screen. In 1-3 short sentences say which app or page is open and the key text or "
              "numbers visible on it. Quote important text exactly.",
}


class VLM:
    """Protocol-ish: `await describe(jpeg, prompt) -> str`. Tests inject fakes with the same signature."""

    def __init__(self, model: str = "", host: str = OLLAMA_HOST, num_predict: int = 120, keep_alive: str = "10m"):
        self.model, self.host = model or DEFAULT_MODEL, host
        self.num_predict, self.keep_alive = num_predict, keep_alive
        self.last_ms: float = 0.0

    async def describe(self, jpeg: bytes, prompt: str, timeout: float = 60.0) -> str:
        body = {"model": self.model, "stream": False, "keep_alive": self.keep_alive, "think": False,
                "options": {"num_predict": self.num_predict, "temperature": 0.1, "seed": 7},
                "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(jpeg).decode()]}]}
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{self.host}/api/chat", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"vlm HTTP {r.status_code}: {r.text[:200]}")
        self.last_ms = (time.monotonic() - t0) * 1000
        return (r.json().get("message", {}).get("content") or "").strip()
