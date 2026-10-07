"""LLM backends (Ollama native, any OpenAI-compatible endpoint), tool/function calling, guardrail output filtering,
and a small non-streaming `complete()` used for summaries, objective judging and script translation.

FeatureLLM implements the same `stream(system, history, user)` interface the Session uses, so features are
layered by wrapping Providers.llm - the Session itself does not know about tools or guardrails.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import AsyncIterator, Awaitable, Callable, Optional

import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_URL", os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
MAX_TOOL_ROUNDS = 3
_SEG_END = re.compile(r"(?<=[.!?])\s+|(?<=[,;:])\s+")


# ---------------- backends ----------------


class OllamaBackend:
    def __init__(self, model: str, host: str = OLLAMA_HOST, num_predict: int = 160):
        self.model, self.host, self.num_predict = model, host, num_predict

    async def chat(self, messages: list[dict], tools: Optional[list[dict]] = None, options: Optional[dict] = None) -> AsyncIterator[tuple]:
        body = {"model": self.model, "messages": messages, "stream": True, "keep_alive": "30m", "think": False,
                "options": {"num_predict": self.num_predict, **(options or {})}}
        if tools:
            body["tools"] = tools
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", f"{self.host}/api/chat", json=body) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"ollama HTTP {r.status_code}: {(await r.aread())[:200]!r}")
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    d = json.loads(line)
                    msg = d.get("message", {})
                    if msg.get("content"):
                        yield "text", msg["content"]
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function", {})
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except ValueError:
                                args = {}
                        yield "tool_call", {"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args}
                    if d.get("done"):
                        break

    # message shapes for the follow-up round
    @staticmethod
    def assistant_tool_msg(calls: list[dict]) -> dict:
        return {"role": "assistant", "content": "",
                "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]}

    @staticmethod
    def tool_result_msg(call: dict, content: str) -> dict:
        return {"role": "tool", "tool_name": call["name"], "content": content}


class OpenAIBackend:
    """Any OpenAI-compatible /chat/completions endpoint (OpenAI, vLLM, LM Studio, Together, Groq, llama.cpp ...)."""

    def __init__(self, base_url: str, model: str, api_key: str = "", max_tokens: int = 160):
        self.base_url, self.model, self.api_key, self.max_tokens = base_url.rstrip("/"), model, api_key, max_tokens

    async def chat(self, messages: list[dict], tools: Optional[list[dict]] = None, options: Optional[dict] = None) -> AsyncIterator[tuple]:
        body = {"model": self.model, "messages": messages, "stream": True, "max_tokens": self.max_tokens}
        if tools:
            body["tools"] = tools
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        acc: dict[int, dict] = {}
        from . import netguard  # custom base_url is user-supplied: SSRF guard (no-op for localhost in dev)

        async with netguard.async_client(timeout=None) as c:
            async with c.stream("POST", f"{self.base_url}/chat/completions", json=body, headers=headers) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"custom llm HTTP {r.status_code}: {(await r.aread())[:200]!r}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get("delta", {})
                    except (ValueError, KeyError, IndexError):
                        continue
                    if delta.get("content"):
                        yield "text", delta["content"]
                    for tc in delta.get("tool_calls") or []:
                        slot = acc.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function", {})
                        slot["name"] += fn.get("name") or ""
                        slot["args"] += fn.get("arguments") or ""
        for slot in acc.values():
            try:
                args = json.loads(slot["args"] or "{}")
            except ValueError:
                args = {}
            yield "tool_call", {"id": slot["id"] or f"call_{slot['name']}", "name": slot["name"], "arguments": args}

    @staticmethod
    def assistant_tool_msg(calls: list[dict]) -> dict:
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
            for c in calls]}

    @staticmethod
    def tool_result_msg(call: dict, content: str) -> dict:
        return {"role": "tool", "tool_call_id": call["id"], "content": content}


def make_backend(llm_spec: str, base_url: str = "", model: str = "", api_key: str = ""):
    """Custom OpenAI-compatible endpoint when base_url is set, else local Ollama (spec like 'ollama/qwen3:8b')."""
    if base_url:
        return OpenAIBackend(base_url, model or llm_spec.split("/", 1)[-1], api_key)
    m = model or os.environ.get("VOCALFACE_LLM") or (llm_spec.split("/", 1)[-1] if llm_spec else "llama3.2:1b")
    return OllamaBackend(m)


# ---------------- tools ----------------


class ToolSpec:
    def __init__(self, name, description, parameters, webhook_url, secret="", timeout_s=8.0):
        self.name, self.description, self.parameters = name, description, parameters or {"type": "object", "properties": {}}
        self.webhook_url, self.secret, self.timeout_s = webhook_url, secret, timeout_s

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                  "parameters": self.parameters}}


ToolPost = Callable[[str, str, dict, float], "tuple[int, str]"]  # (url, body, headers, timeout) -> (status, text)


def _http_post(url: str, body: str, headers: dict, timeout: float) -> tuple[int, str]:
    from . import netguard  # tool webhooks are user-supplied URLs

    r = netguard.post(url, content=body, headers=headers, timeout=timeout)
    return r.status_code, r.text


_tool_post: ToolPost = _http_post


def set_tool_post(f: Optional[ToolPost]) -> None:
    global _tool_post
    _tool_post = f or _http_post


def execute_tool(tool: ToolSpec, args: dict, ctx: dict) -> tuple[bool, str]:
    """POST {tool, arguments, conversation_id, persona_id} to the tool webhook (HMAC-signed when a secret is set).
    Returns (ok, result_text). Result text is whatever the webhook returned (JSON preferred), truncated."""
    from .webhooks import sign

    if tool.webhook_url.startswith("vocalface-internal://"):  # built-in tools (leads.py), no HTTP
        from . import leads

        return leads.run_internal(tool.name, args, ctx)
    body = json.dumps({"tool": tool.name, "arguments": args, **ctx}, separators=(",", ":"))
    headers = {"content-type": "application/json", "user-agent": "VocalFace-Tools/1"}
    if tool.secret:
        headers["VocalFace-Signature"] = sign(tool.secret, body)
    try:
        code, text = _tool_post(tool.webhook_url, body, headers, tool.timeout_s)
    except Exception as e:  # noqa: BLE001
        return False, json.dumps({"error": f"tool call failed: {type(e).__name__}"})
    if not 200 <= code < 300:
        return False, json.dumps({"error": f"tool returned HTTP {code}"})
    return True, text[:2000]


# ---------------- guardrail output filter ----------------


def _global_blocklist():
    try:
        from . import safety

        return safety._blocklist()
    except Exception:  # noqa: BLE001
        return []


def violates(text: str, guardrails: list[dict]) -> Optional[str]:
    """Name of the violated guardrail ('moderation' for the global blocklist) or None."""
    low = text.lower()
    for g in guardrails:
        for ph in g.get("forbidden_phrases") or []:
            if ph and ph.lower() in low:
                return g.get("name") or g.get("rule", "guardrail")[:40]
    for p in _global_blocklist():
        if p.search(text):
            return "moderation"
    return None


# ---------------- FeatureLLM ----------------


TOOL_RULES = ("Tools: call a tool only when the user clearly asks for what it does AND you already have every required "
              "argument (take it from what the user said; if one is missing, ask for it instead of guessing). For small talk, "
              "thanks, goodbyes and general questions do NOT call any tool - just answer briefly.")


class FeatureLLM:
    def __init__(self, backend, tools: list[ToolSpec] | None = None, ctx: dict | None = None,
                 on_tool: Callable[[dict], None] | None = None):
        self.backend, self.tools = backend, {t.name: t for t in (tools or [])}
        self.ctx, self.on_tool = ctx or {}, on_tool

    async def _rounds(self, system: str, history: list[dict], user: str) -> AsyncIterator[str]:
        if self.tools:
            system = system + "\n\n" + TOOL_RULES
        msgs = [{"role": "system", "content": system}, *history, {"role": "user", "content": user}]
        schemas = [t.schema() for t in self.tools.values()]
        be = self.backend
        for rnd in range(MAX_TOOL_ROUNDS + 1):
            calls, spoke = [], False
            use_tools = schemas if (schemas and rnd < MAX_TOOL_ROUNDS) else None
            async for kind, val in be.chat(msgs, use_tools):
                if kind == "text":
                    spoke = True
                    yield val
                else:
                    calls.append(val)
            if not calls:
                if rnd > 0 and not spoke:
                    yield self._last_fallback or "Okay, done."
                return
            msgs.append(be.assistant_tool_msg(calls))
            self._last_fallback = ""
            for c in calls:
                tool = self.tools.get(c["name"])
                t0 = time.monotonic()
                if tool is None:
                    ok, result = False, json.dumps({"error": f"unknown tool {c['name']}"})
                else:
                    ok, result = await asyncio.to_thread(execute_tool, tool, c["arguments"], self.ctx)
                if self.on_tool:
                    try:
                        self.on_tool({"name": c["name"], "arguments": c["arguments"], "result": result, "ok": ok,
                                      "duration_ms": int((time.monotonic() - t0) * 1000)})
                    except Exception:  # noqa: BLE001
                        pass
                msgs.append(be.tool_result_msg(c, result))
                if ok and not self._last_fallback:
                    try:
                        j = json.loads(result)
                        for k in ("speech", "message", "result", "text"):
                            if isinstance(j, dict) and isinstance(j.get(k), str):
                                self._last_fallback = j[k]
                                break
                    except ValueError:
                        self._last_fallback = result[:200]
        return

    _last_fallback = ""

    async def stream(self, system: str, history: list[dict], user: str) -> AsyncIterator[str]:
        async for t in self._rounds(system, history, user):
            yield t


class GuardedLLM:
    """Wraps any object with `stream(system, history, user)`; checks output clause by clause before releasing it.
    On a violation it speaks `fallback` instead and drops the rest of that reply (generation is cancelled)."""

    def __init__(self, inner, guardrails: list[dict], fallback: str = "Sorry, I can't help with that.",
                 on_guardrail: Callable[[str, str], None] | None = None):
        self.inner, self.guardrails, self.fallback, self.on_guardrail = inner, guardrails, fallback, on_guardrail

    async def stream(self, system: str, history: list[dict], user: str) -> AsyncIterator[str]:
        src = self.inner.stream(system, history, user)
        buf, prev = "", ""
        try:
            async for tok in src:
                buf += tok
                parts = _SEG_END.split(buf)
                for seg in parts[:-1]:
                    hit = violates(prev + " " + seg, self.guardrails)
                    if hit:
                        if self.on_guardrail:
                            self.on_guardrail(hit, seg)
                        yield self.fallback + " "
                        return
                    yield seg + " "
                    prev = seg
                buf = parts[-1]
            if buf.strip():
                hit = violates(prev + " " + buf, self.guardrails)
                if hit:
                    if self.on_guardrail:
                        self.on_guardrail(hit, buf)
                    yield self.fallback
                else:
                    yield buf
        finally:
            aclose = getattr(src, "aclose", None)
            if aclose:
                await aclose()


# ---------------- grounding scaffold (small-model discipline) ----------------


# Language of the user's turn: the reminder is written IN that language, otherwise small models answer in English
# (measured: an English reminder dropped Spanish/Hindi language match from 100% to 25% on llama3.2:3b).
_STOPWORDS = {
    "es": "el la los las de que es en un una por para con no se su como cuanto cuanta cual cuando donde puedo tengo qué cuánto aceptan".split(),
    "fr": "le la les des de du est un une pour avec ne pas que qui quel quelle combien comment où puis je vous est-ce".split(),
    "pt": "os as de que é em um uma por para com não se seu como quanto qual quando onde posso você".split(),
    "it": "il lo la gli le di che è in un una per con non si suo come quanto quale quando dove posso".split(),
    "de": "der die das und ist ein eine für mit nicht wie viel was wann wo kann ich sie".split(),
    "en": "the is are what how much do does can i you my your it of to for with".split(),
}
_NOTES = {
    "en": "Reply in at most two short spoken sentences (under 30 words).",
    "es": "Responde en español, en máximo dos frases cortas habladas (menos de 30 palabras).",
    "fr": "Réponds en français, en deux phrases courtes maximum (moins de 30 mots).",
    "pt": "Responda em português, em no máximo duas frases curtas (menos de 30 palavras).",
    "it": "Rispondi in italiano, in al massimo due frasi brevi (meno di 30 parole).",
    "de": "Antworte auf Deutsch in höchstens zwei kurzen gesprochenen Sätzen (unter 30 Wörtern).",
    "hi": "हिंदी में, अधिकतम दो छोटे वाक्यों में जवाब दें (30 शब्दों से कम)।",
    "ja": "日本語で、短く2文以内で答えてください。",
    "zh": "请用中文回答，最多两句简短的话。",
}
_GROUND_NOTES = {
    "en": " Look for the answer in the knowledge excerpts and give it directly; only if it is really not there, say you don't have that information.",
    "es": " Usa solo los fragmentos de conocimiento para datos de la empresa; si no lo dicen, di que no tienes esa información.",
    "fr": " N'utilise que les extraits de connaissance pour les faits de l'entreprise ; sinon dis que tu n'as pas cette information.",
    "pt": " Use apenas os trechos de conhecimento para fatos da empresa; se não constar, diga que não tem essa informação.",
    "it": " Usa solo gli estratti di conoscenza per i fatti aziendali; se non lo dicono, di' che non hai questa informazione.",
    "de": " Nutze nur die Wissensauszüge für Firmenfakten; wenn dort nichts steht, sage, dass du die Information nicht hast.",
    "hi": " कंपनी की जानकारी के लिए केवल दिए गए अंशों का उपयोग करें; यदि उनमें नहीं है तो कहें कि आपके पास यह जानकारी नहीं है।",
    "ja": " 会社の情報は提供された抜粋のみを使い、なければ情報がないと答えてください。",
    "zh": " 公司信息只能使用提供的资料摘录；没有的话就说你没有这方面的信息。",
}
_NAME_TO_CODE = {"spanish": "es", "french": "fr", "portuguese": "pt", "italian": "it", "german": "de", "hindi": "hi",
                 "japanese": "ja", "chinese": "zh", "english": "en"}


def detect_language(text: str, system: str = "") -> str:
    """Best-effort language code of the user's turn. A language forced by the persona/conversation
    ('Always speak and reply in Spanish') wins over detection."""
    m = re.search(r"Always speak and reply in ([A-Za-z]+)", system or "")
    if m and m.group(1).lower() in _NAME_TO_CODE:
        return _NAME_TO_CODE[m.group(1).lower()]
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    n = len(letters)
    if sum("\u0900" <= c <= "\u097f" for c in letters) / n > 0.3:
        return "hi"
    if sum("\u3040" <= c <= "\u30ff" for c in letters) / n > 0.2:
        return "ja"
    if sum("\u4e00" <= c <= "\u9fff" for c in letters) / n > 0.3:
        return "zh"
    words = re.findall(r"[^\W\d_]+", text.lower())
    if "¿" in text or "¡" in text or "ñ" in text.lower():
        return "es"
    scores = {code: sum(w in sw for w in words) for code, sw in _STOPWORDS.items()}
    best = max((c for c in scores if c != "en"), key=lambda c: scores[c])
    # non-English needs two real stop-word hits and must clearly beat English ("a new patient" is not Portuguese)
    return best if scores[best] >= 2 and scores[best] > scores["en"] else "en"


class GroundedLLM:
    """Wraps any `stream(system, history, user)` LLM with the things small models need to stay on-script (measured with
    backend/evals/run_evals.py): a one-line reminder appended to the *user* turn (small models weigh the last message far
    more than the system prompt: voice brevity, answer only from the excerpts, reply in the user's language - written in
    that language) and a trimmed history (the last few messages, long agent replies cut) so the knowledge excerpts and
    the question stay in focus. The reminder is only sent to the model, never stored in the transcript."""

    MAX_MSGS = int(os.environ.get("VOCALFACE_HISTORY_MSGS", "8"))
    ENABLED = os.environ.get("VOCALFACE_GROUNDING", "1") != "0"

    @staticmethod
    def prepare(system: str, history: list[dict], user: str, max_msgs: int | None = None) -> tuple[str, list[dict], str]:
        if not GroundedLLM.ENABLED:
            return system, history, user
        n = max_msgs or GroundedLLM.MAX_MSGS
        hist = [{**m, "content": (m["content"][:400] + "...") if m.get("role") == "assistant" and len(m.get("content") or "") > 400
                 else m.get("content")} for m in history[-n:]]
        while hist and hist[0].get("role") != "user":  # never start with an orphan assistant message
            hist = hist[1:]
        lang = detect_language(user, system)
        note = _NOTES.get(lang, _NOTES["en"])
        if "Relevant knowledge" in system and os.environ.get("VOCALFACE_NOTE_GROUND", "1") != "0":
            note += _GROUND_NOTES.get(lang, _GROUND_NOTES["en"])
        return system, hist, f"{user}\n\n({note})"

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):  # warmup(), model, ...
        return getattr(self.inner, name)

    async def stream(self, system: str, history: list[dict], user: str) -> AsyncIterator[str]:
        system, history, user = self.prepare(system, history, user)
        async for tok in self.inner.stream(system, history, user):
            yield tok


def ground_session(sess) -> None:
    """Wrap the live session's LLM with GroundedLLM - real stack only (tests that inject fake providers are untouched)."""
    from .pipeline import session as S

    if not GroundedLLM.ENABLED or S._factory is not S.default_provider_factory:
        return
    sess.p = S.Providers(sess.p.stt, GroundedLLM(sess.p.llm), sess.p.tts)


# ---------------- non-streaming completion ----------------

Completer = Callable[[str, str], Awaitable[str]]  # (system, prompt) -> text
_completer_override: Optional[Completer] = None


def set_completer(f: Optional[Completer]) -> None:
    """Tests inject a fake; None restores the real backend-based completer."""
    global _completer_override
    _completer_override = f


async def complete(backend, system: str, prompt: str) -> str:
    if _completer_override is not None:
        return await _completer_override(system, prompt)
    out = []
    async for kind, val in backend.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}], None):
        if kind == "text":
            out.append(val)
    return "".join(out).strip()


def parse_json_obj(text: str) -> dict:
    """Tolerant JSON extraction from an LLM answer (code fences, prose around it)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no json object in answer")
    return json.loads(m.group(0))
