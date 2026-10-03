"""Feature layer for live conversations: persona config, prompt assembly, transcript recording, greeting,
objectives (LLM-judged), tools, guardrails, memory, multilingual, limits, and end-of-conversation finalisation.

routers/realtime.py wires this in with ~10 lines; pipeline/session.py only gained `Session.say()`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlmodel import Session as DB, SQLModel, select

from . import db, languages, llm_backends as lb, webhooks
from .models_features import (ConversationMeta, ConversationMetric, MemoryScope, ObjectiveProgress, PersonaConfig,
                              PersonaTool, ToolCallLog, TranscriptTurn)
from .secretbox import decrypt

_ensured: set[int] = set()
log = logging.getLogger("mirage.runtime")


def ensure() -> None:
    if id(db.engine) not in _ensured:
        SQLModel.metadata.create_all(db.engine)
        _ensured.add(id(db.engine))


def utc(d: Optional[datetime]) -> Optional[datetime]:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render(text: str, variables: dict) -> str:
    return _VAR.sub(lambda m: str(variables.get(m.group(1), m.group(0))), text or "")


def template_vars(text: str) -> list[str]:
    return sorted(set(_VAR.findall(text or "")))


def jload(s: str, default):
    try:
        return json.loads(s) if s else default
    except ValueError:
        return default


# ---------------- memory ----------------


def memories_for(s: DB, persona_id: str, participant_id: str = "", limit: int = 3) -> list[str]:
    from .models_extra import MemorySummary

    rows = s.exec(select(MemorySummary).where(MemorySummary.persona_id == persona_id)
                  .order_by(MemorySummary.created_at.desc()).limit(60)).all()
    scopes = {m.memory_id: m.participant_id for m in s.exec(select(MemoryScope).where(MemoryScope.persona_id == persona_id)).all()}
    return [r.summary for r in rows if scopes.get(r.id, "") == (participant_id or "")][:limit]


# ---------------- runtime ----------------


class ConversationRuntime:
    def __init__(self, conv, persona, cfg: Optional[PersonaConfig], meta: ConversationMeta, tools: list[PersonaTool],
                 memories: list[str]):
        self.cid, self.account_id, self.persona_id = conv.id, conv.account_id, persona.id
        self.persona_llm = persona.llm
        self.cfg, self.meta = cfg, meta
        self.vars = jload(meta.variables, {})
        self.language = languages.normalize_language(meta.language or (cfg.language if cfg else "en"))
        self.greeting = render(cfg.greeting, self.vars).strip() if cfg and cfg.greeting else ""
        self.objectives = jload(cfg.objectives, []) if cfg else []
        self.guardrails = jload(cfg.guardrails, []) if cfg else []
        self.fallback = (cfg.guardrail_fallback if cfg else "") or "Sorry, I can't help with that."
        self.tools = [lb.ToolSpec(t.name, t.description, jload(t.parameters, {}), t.webhook_url,
                                  decrypt(t.secret_enc) if t.secret_enc else "", t.timeout_s) for t in tools]
        self.memories = memories
        self.api_key = decrypt(cfg.llm_api_key_enc) if cfg and cfg.llm_api_key_enc else ""
        self.sess = None
        self.ws = None
        self._inner_send = None
        self._tasks: set[asyncio.Task] = set()
        self._agent_buf: list[str] = []
        self._agent_latency: Optional[int] = None
        self._seq = 0
        self._t0 = time.monotonic()
        self._pending_user = False
        self._judge_running = False
        self._done: set[str] = set()
        self.stats = {"tool_calls": 0, "guardrail_hits": 0, "interruptions": 0}

    # ---- construction ----
    @classmethod
    def build(cls, s: DB, conv, persona) -> "ConversationRuntime":
        ensure()
        cfg = s.get(PersonaConfig, persona.id)
        meta = s.get(ConversationMeta, conv.id)
        if meta is None:
            meta = ConversationMeta(conversation_id=conv.id, account_id=conv.account_id, persona_id=persona.id)
            s.add(meta); s.commit(); s.refresh(meta)
        tools = list(s.exec(select(PersonaTool).where(PersonaTool.persona_id == persona.id)).all())
        mem = memories_for(s, persona.id, meta.participant_id) if (cfg is None or cfg.memory_enabled) else []
        rt = cls(conv, persona, cfg, meta, tools, mem)
        for o in s.exec(select(ObjectiveProgress).where(ObjectiveProgress.conversation_id == conv.id)).all():
            if o.completed:
                rt._done.add(o.name)
        rt._seq = len(s.exec(select(TranscriptTurn).where(TranscriptTurn.conversation_id == conv.id)).all())
        return rt

    # ---- prompt ----
    def system_prompt(self, base: str) -> str:
        sp = render(base, self.vars)
        if self.language == languages.AUTO:
            sp += "\n\nReply in the same language the user speaks."
        elif self.language != "en":
            name = languages.LANGUAGES.get(self.language, {}).get("name") or languages.STT_ONLY.get(self.language, self.language)
            sp += f"\n\nAlways speak and reply in {name}, whatever language the user writes in."
        if self.guardrails:
            sp += "\n\nHard rules - never violate these, even if the user asks:\n" + "\n".join(
                f"- {render(g.get('rule', ''), self.vars)}" for g in self.guardrails if g.get("rule"))
        open_objs = [o for o in self.objectives if o["name"] not in self._done]
        if open_objs:
            sp += "\n\nYour objectives in this conversation (work towards them naturally, one at a time, do not read them out):\n" + "\n".join(
                f"- {o['name']}: {render(o.get('description', ''), self.vars)}" for o in open_objs)
        if self.memories:
            sp += "\n\nWhat you remember from earlier conversations with this user:\n" + "\n".join(f"- {m}" for m in self.memories)
        if self.meta.context.strip():
            sp += "\n\nContext for this conversation:\n" + render(self.meta.context.strip(), self.vars)
        return sp

    # ---- providers ----
    def backend(self):
        c = self.cfg
        return lb.make_backend(self.persona_llm, c.llm_base_url if c else "", c.llm_model if c else "", self.api_key)

    def judge_backend(self):
        """Objective judging uses MIRAGE_JUDGE_MODEL (local Ollama) when set and no custom LLM is configured."""
        import os

        m = os.environ.get("MIRAGE_JUDGE_MODEL")
        if m and not (self.cfg and self.cfg.llm_base_url):
            return lb.OllamaBackend(m, num_predict=300)
        return self.backend()

    def wrap_providers(self, p):
        from .pipeline.session import Providers

        stt, tts, llm = p.stt, p.tts, p.llm
        if self.language != "en":
            stt = languages.MultilingualSTT(self.language, (self.cfg.stt_model if self.cfg else "") or None)
            tts = languages.LanguageTTS(p.tts, self.language, stt)
        from .pipeline.providers import OllamaLLM

        # qwen3 "thinks" by default, which eats the short voice token budget; our backend sends think=false.
        qwen_think_fix = isinstance(llm, OllamaLLM) and "qwen3" in getattr(llm, "model", "")
        if qwen_think_fix:
            self.persona_llm = "ollama/" + llm.model
        if self.tools or qwen_think_fix or (self.cfg and self.cfg.llm_base_url):
            llm = lb.FeatureLLM(self.backend(), self.tools,
                                {"conversation_id": self.cid, "persona_id": self.persona_id}, self._on_tool)
        if self.guardrails:
            llm = lb.GuardedLLM(llm, self.guardrails, self.fallback, self._on_guardrail)
        return Providers(stt, llm, tts)

    def _on_tool(self, info: dict) -> None:
        self.stats["tool_calls"] += 1
        try:
            with DB(db.engine) as s:
                s.add(ToolCallLog(conversation_id=self.cid, tool_name=info["name"], arguments=json.dumps(info["arguments"]),
                                  result=info["result"][:2000], ok=info["ok"], duration_ms=info["duration_ms"]))
                s.commit()
            webhooks.emit(self.account_id, "tool.called", {"conversation_id": self.cid, "tool": info["name"],
                                                           "arguments": info["arguments"], "ok": info["ok"]})
        except Exception:  # noqa: BLE001
            pass

    def _on_guardrail(self, name: str, text: str) -> None:
        self.stats["guardrail_hits"] += 1
        webhooks.emit(self.account_id, "guardrail.triggered", {"conversation_id": self.cid, "guardrail": name, "blocked_text": text[:300]})
        if self._inner_send and self._loop:
            self._loop.create_task(self._inner_send({"type": "guardrail", "name": name}))

    _loop: Any = None

    # ---- transcript recording (wraps the Session's send_json) ----
    def wrap_send(self, inner):
        self._inner_send = inner
        self._loop = asyncio.get_running_loop()

        async def send(msg: dict):
            t = msg.get("type")
            try:
                if t == "transcript":
                    if msg.get("role") == "user":
                        self._flush_agent(False)
                        self._add_turn("user", msg.get("text", ""))
                        self._pending_user = True
                    else:
                        if not self._agent_buf and self._pending_user and self.sess is not None:
                            ttfa = self.sess.metrics.get("ttfa_s")
                            self._agent_latency = int(ttfa * 1000) if ttfa else None
                        self._pending_user = False
                        self._agent_buf.append(msg.get("text", ""))
                elif t == "agent_done":
                    self._flush_agent(False)
                    self._spawn(self._judge())
                elif t == "interrupted":
                    self.stats["interruptions"] += 1
                    self._flush_agent(True)
            except Exception:  # noqa: BLE001 - recording must never break a live turn
                log.exception("transcript recording failed")
            await inner(msg)

        return send

    def _add_turn(self, role: str, text: str, latency: Optional[int] = None, interrupted: bool = False) -> None:
        if not text.strip():
            return
        with DB(db.engine) as s:
            s.add(TranscriptTurn(conversation_id=self.cid, seq=self._seq, role=role, text=text.strip(),
                                 t_ms=int((time.monotonic() - self._t0) * 1000), first_audio_ms=latency, interrupted=interrupted))
            s.commit()
        self._seq += 1

    def _flush_agent(self, interrupted: bool) -> None:
        if self._agent_buf:
            self._add_turn("assistant", " ".join(self._agent_buf), self._agent_latency, interrupted)
        self._agent_buf, self._agent_latency = [], None

    # ---- lifecycle ----
    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def start(self, sess, ws, base_seconds: int = 0, credits: Optional[int] = None) -> None:
        """Call after the 'ready' event has been sent."""
        self.sess, self.ws = sess, ws
        limit = self.meta.max_seconds
        if credits is not None:
            limit = min(limit, credits) if limit else credits
        if limit:
            self._spawn(self._watchdog(max(limit - base_seconds, 1)))
        if self.greeting and self._seq == 0:
            self._spawn(sess.say(self.greeting))

    async def _watchdog(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            await asyncio.sleep(min(1.0, max(end - time.monotonic(), 0.01)))
        try:
            if self._inner_send:
                await self._inner_send({"type": "error", "message": "conversation time limit reached"})
            await self.ws.close(code=4408, reason="time limit reached")
        except Exception:  # noqa: BLE001
            pass

    async def stop(self) -> None:
        self._flush_agent(True if self.sess is not None and self.sess.agent_speaking else False)
        for t in list(self._tasks):
            t.cancel()
        try:
            with DB(db.engine) as s:
                m = s.get(ConversationMeta, self.cid)
                if m:
                    m.last_closed_at = datetime.now(timezone.utc)
                    s.add(m); s.commit()
        except Exception:  # noqa: BLE001
            pass

    # ---- objectives ----
    def _transcript_text(self) -> str:
        with DB(db.engine) as s:
            rows = s.exec(select(TranscriptTurn).where(TranscriptTurn.conversation_id == self.cid)
                          .order_by(TranscriptTurn.seq)).all()
        return "\n".join(f"{'User' if r.role == 'user' else 'Agent'}: {r.text}" for r in rows)

    async def _judge(self) -> None:
        open_objs = [o for o in self.objectives if o["name"] not in self._done]
        if not open_objs or self._judge_running:
            return
        self._judge_running = True
        try:
            await judge_objectives(self, open_objs)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("objective judge failed")
        finally:
            self._judge_running = False


async def judge_objectives(rt: ConversationRuntime, open_objs: list[dict], notify: bool = True) -> list[dict]:
    """Ask the LLM which open objectives the transcript satisfies. Persists + emits for newly completed ones."""
    transcript = rt._transcript_text()
    if not transcript:
        return []
    items = "\n".join(f"- name: {o['name']}\n  goal: {o.get('description', '')}\n  success criteria: "
                      f"{o.get('success_criteria', 'the goal has clearly been achieved in the conversation')}\n  "
                      f"variables to extract: {', '.join(o.get('output_variables') or []) or 'none'}" for o in open_objs)
    prompt = ("You judge whether objectives of a conversation have been completed. Only mark an objective completed "
              "if the transcript clearly shows it (not merely attempted).\n\nObjectives:\n" + items +
              "\n\nTranscript:\n" + transcript[-6000:] +
              '\n\nAnswer with JSON only: {"objectives":[{"name":"...","completed":true|false,"evidence":"short quote or reason",'
              '"variables":{"var":"value"}}]}')
    out = await lb.complete(rt.judge_backend(), "You are a strict, concise JSON-only evaluator.", prompt)
    try:
        data = lb.parse_json_obj(out)
    except ValueError:
        return []
    names = {o["name"]: o for o in open_objs}
    newly = []
    for r in data.get("objectives", []):
        o = names.get(r.get("name"))
        if not o or not r.get("completed"):
            continue
        wanted = o.get("output_variables") or []
        variables = {k: v for k, v in (r.get("variables") or {}).items() if not wanted or k in wanted}
        with DB(db.engine) as s:
            row = s.exec(select(ObjectiveProgress).where(ObjectiveProgress.conversation_id == rt.cid,
                                                         ObjectiveProgress.name == o["name"])).first()
            if row and row.completed:
                continue
            row = row or ObjectiveProgress(conversation_id=rt.cid, name=o["name"])
            evidence = str(r.get("evidence", ""))[:500]
            row.completed, row.evidence = True, evidence
            row.variables, row.completed_at = json.dumps(variables), datetime.now(timezone.utc)
            s.add(row); s.commit()
        rt._done.add(o["name"])
        info = {"name": o["name"], "evidence": evidence, "variables": variables}
        newly.append(info)
        if notify:
            webhooks.emit(rt.account_id, "objective.completed", {"conversation_id": rt.cid, **info})
            if rt._inner_send:
                await rt._inner_send({"type": "objective_completed", **info})
    return newly


# ---------------- end of conversation ----------------


def conversation_turns(s: DB, cid: str) -> list[TranscriptTurn]:
    return list(s.exec(select(TranscriptTurn).where(TranscriptTurn.conversation_id == cid).order_by(TranscriptTurn.seq)).all())


async def finalize_conversation(cid: str, reason: str = "ended") -> bool:
    """Idempotent. Summary -> memory, final objective pass, metrics row, webhooks. Returns False if already done."""
    from . import knowledge as kb
    from .db import Conversation, Persona

    ensure()
    with DB(db.engine) as s:
        conv = s.get(Conversation, cid)
        if conv is None:
            return False
        meta = s.get(ConversationMeta, cid)
        if meta is None:
            meta = ConversationMeta(conversation_id=cid, account_id=conv.account_id, persona_id=conv.persona_id)
            s.add(meta); s.commit(); s.refresh(meta)
        if meta.finalized_at is not None:
            return False
        from sqlalchemy import update

        claimed = s.exec(update(ConversationMeta).where(ConversationMeta.conversation_id == cid,
                                                        ConversationMeta.finalized_at == None).values(  # noqa: E711
            finalized_at=datetime.now(timezone.utc), end_reason=reason))  # atomic claim: concurrent callers bail out
        s.commit()
        if claimed.rowcount != 1:
            return False
        s.refresh(meta)
        persona = s.get(Persona, conv.persona_id)
        turns = conversation_turns(s, cid)
        cfg = s.get(PersonaConfig, conv.persona_id)
        rt = ConversationRuntime.build(s, conv, persona) if persona else None
        account_id, seconds = conv.account_id, conv.seconds_used
        participant = meta.participant_id

    user_turns = [t for t in turns if t.role == "user"]
    agent_turns = [t for t in turns if t.role == "assistant"]
    # final objective pass (the in-call judge may not have run for the last turn)
    objectives = []
    if rt and rt.objectives and turns:
        remaining = [o for o in rt.objectives if o["name"] not in rt._done]
        if remaining:
            try:
                await judge_objectives(rt, remaining)
            except Exception:  # noqa: BLE001
                pass
        with DB(db.engine) as s:
            objectives = [{"name": o.name, "completed": o.completed, "evidence": o.evidence, "variables": jload(o.variables, {})}
                          for o in s.exec(select(ObjectiveProgress).where(ObjectiveProgress.conversation_id == cid)).all()]
    summary = ""
    if user_turns:
        convo = "\n".join(f"{t.role}: {t.text}" for t in turns)
        prompt = ("Summarise this conversation in 2-3 sentences. Keep concrete facts about the user (name, preferences, "
                  "goals, commitments) so a future conversation can pick up where this one ended.\n\n" + convo[-6000:])
        try:
            if rt:
                summary = (await lb.complete(rt.backend(), "You write concise conversation summaries.", prompt)).strip()
        except Exception:  # noqa: BLE001
            summary = ""
        if not summary:
            summary = kb._extractive_summary([{"role": t.role, "content": t.text} for t in turns])
    lat = sorted(t.first_audio_ms for t in agent_turns if t.first_audio_ms)
    with DB(db.engine) as s:
        meta = s.get(ConversationMeta, cid)
        meta.summary = summary
        s.add(meta)
        if summary and (cfg is None or cfg.memory_enabled):
            mem = kb.save_memory(conv.persona_id, summary, cid, session=s)
            s.add(MemoryScope(memory_id=mem.id, persona_id=conv.persona_id, participant_id=participant))
        if s.get(ConversationMetric, cid) is None:
            tool_calls = len(s.exec(select(ToolCallLog).where(ToolCallLog.conversation_id == cid)).all())
            s.add(ConversationMetric(
                conversation_id=cid, account_id=account_id, persona_id=conv.persona_id, user_turns=len(user_turns),
                agent_turns=len(agent_turns), avg_first_audio_ms=(sum(lat) / len(lat)) if lat else None,
                p95_first_audio_ms=float(lat[min(len(lat) - 1, int(len(lat) * 0.95))]) if lat else None,
                interruptions=len([t for t in agent_turns if t.interrupted]), tool_calls=tool_calls))
        s.commit()
        webhooks.emit(account_id, "conversation.ended", {
            "conversation_id": cid, "persona_id": conv.persona_id, "seconds_used": seconds, "reason": reason,
            "summary": summary, "objectives": objectives}, session=s)
        webhooks.emit(account_id, "transcript.ready", {"conversation_id": cid, "turns": len(turns)}, session=s)
    return True


def end_conversation_row(s: DB, acc, c) -> None:
    """Same accounting as POST /conversations/{id}/end (seconds, credits, usage ledger)."""
    from .billing import settle_usage

    c.status, c.ended_at = "ended", datetime.now(timezone.utc)
    started = utc(c.started_at)
    c.seconds_used = c.seconds_used or max(int((c.ended_at - started).total_seconds()), 1)  # WS meter wins if present
    s.add(c); s.commit(); s.refresh(c)
    settle_usage(s, acc, c.seconds_used, f"conv:{c.id}")  # credits first, then capped overage (billing.py)
    s.refresh(c)
    count_share_usage(s, c)


def count_share_usage(s: DB, c) -> None:
    """Guest links: add this conversation's seconds to the link's cost-cap counter (once)."""
    from .models_features import ShareLink, ShareSession

    ss = s.get(ShareSession, c.id)
    if ss is None or ss.counted:
        return
    link = s.get(ShareLink, ss.token)
    if link is not None:
        link.used_seconds += c.seconds_used or 0
        s.add(link)
    ss.counted = True
    s.add(ss); s.commit()


async def reaper_loop(interval: float = 15.0) -> None:
    while True:
        try:
            ids = await asyncio.to_thread(reap_idle)
            for cid in ids:
                await finalize_conversation(cid, "idle")
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(interval)


def reap_idle(grace_s: Optional[float] = None) -> list[str]:
    import os

    from .db import Account, Conversation

    ensure()
    grace = grace_s if grace_s is not None else float(os.environ.get("MIRAGE_END_GRACE_S", "30"))
    now, ended = datetime.now(timezone.utc), []
    with DB(db.engine) as s:
        for c in s.exec(select(Conversation).where(Conversation.status == "active")).all():
            meta = s.get(ConversationMeta, c.id)
            closed = utc(meta.last_closed_at) if meta else None
            if closed is None or now - closed <= timedelta(seconds=grace):
                continue
            end_conversation_row(s, s.get(Account, c.account_id), c)
            ended.append(c.id)
    return ended


def on_conversation_created(s: DB, conv, opts: dict) -> None:
    """Called by POST /conversations before the row is committed. Raises HTTPException(422) on bad options."""
    from fastapi import HTTPException

    ensure()
    variables = opts.get("variables") or {}
    if len(variables) > 50 or any(len(str(k)) > 64 or len(str(v)) > 500 for k, v in variables.items()):
        raise HTTPException(422, "variables: max 50 entries, values up to 500 chars")
    if opts.get("max_seconds") is not None and opts["max_seconds"] <= 0:
        raise HTTPException(422, "max_seconds must be > 0")
    lang = opts.get("language")
    if lang:
        try:
            lang = languages.normalize_language(lang)
        except ValueError as e:
            raise HTTPException(422, str(e))
    if len(opts.get("context") or "") > 4000:
        raise HTTPException(422, "context too long (4000 chars max)")
    s.add(ConversationMeta(conversation_id=conv.id, account_id=conv.account_id, persona_id=conv.persona_id,
                           participant_id=opts.get("participant_id") or "", context=opts.get("context") or "",
                           variables=json.dumps(variables), max_seconds=opts.get("max_seconds"), language=lang))
    webhooks.emit(conv.account_id, "conversation.started", {"conversation_id": conv.id, "persona_id": conv.persona_id}, session=s)
