"""Sanity-test templates against the real local LLM (Ollama) with the production prompt/retrieval/tool/guardrail chain.

  cd backend && MIRAGE_DB_URL=sqlite:////tmp/tpl.db MIRAGE_DATA=/tmp/tpl_data .venv/bin/python -m app.templates.eval_cli \
      [--template customer-support] [--model qwen3:8b] [--out /tmp/tpl_eval.json]

Uses the same DB as a running API (set MIRAGE_DB_URL identically) only for the lead rows; text-in/text-out (no STT/TTS).
Per template: sample Q&A (knowledge used), guardrail probes, a scripted lead-capture conversation (must call capture_lead with
consent and store name + phone), and a decline conversation (must NOT store a lead and must not keep asking).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time

from sqlmodel import Session as DB, SQLModel, select

from .. import builtin_tools, convo_runtime as cr, db, knowledge as kb, llm_backends as lb, templates as T
from ..models_leads import Lead
from ..pipeline.session import build_system_prompt

LEAD_TURNS = ["{q}", "Great. I would like someone to follow up with me about this.", "Sure, my name is Ravi Kumar.",
              "My phone number is 9 8 7 6 5 4 3 2 1 0.", "Yes, that's fine, you can contact me on that number.", "Thanks, bye."]
DECLINE_TURNS = ["{q}", "Could someone follow up with me?", "No thanks, I would rather not share any personal details.", "What else can you tell me?"]


def _words(t: str) -> str:
    return t.lower().replace(",", "").replace("$", "")


def _setup(tid: str, model: str, lang: str | None = None):
    """Instantiate through the same code as the API (direct DB) and return (persona, runtime builder)."""
    from fastapi.testclient import TestClient

    from ..main import app

    c = TestClient(app)
    key = c.post("/v1/signup", json={"email": f"eval{int(time.time()*1000)}@example.com"}).json()["api_key"]
    h = {"x-api-key": key}
    body = {"llm": f"ollama/{model}"}
    if lang:
        body["language"] = lang
    r = c.post(f"/v1/templates/{tid}/instantiate", json=body, headers=h)
    assert r.status_code == 200, r.text
    return c, h, r.json()["persona_id"]


async def run_conversation(c, h, pid: str, turns: list[str], model: str) -> dict:
    cid = c.post("/v1/conversations", json={"persona_id": pid}, headers=h).json()["id"]
    with DB(db.engine) as s:
        conv, persona = s.get(db.Conversation, cid), s.get(db.Persona, pid)
        rt = cr.ConversationRuntime.build(s, conv, persona)
    base = build_system_prompt(persona)
    system = rt.system_prompt(base)
    llm = lb.FeatureLLM(rt.backend(), rt.tools, {"conversation_id": cid, "persona_id": pid}, rt._on_tool)
    if rt.guardrails:
        llm = lb.GuardedLLM(llm, rt.guardrails, rt.fallback, rt._on_guardrail)
    llm = lb.GroundedLLM(llm)
    history, out = [], []
    if rt.greeting:
        out.append({"role": "assistant", "text": rt.greeting, "greeting": True})
        history.append({"role": "assistant", "content": rt.greeting})
    for u in turns:
        hits = await asyncio.to_thread(lambda: kb.format_context(kb.retrieve(pid, u, k=3)))
        sysmsg = system + ("\n\nRelevant documents:\n" + hits if hits else "")
        t0 = time.monotonic()
        text = "".join([x async for x in llm.stream(sysmsg, history, u)]).strip()
        out.append({"role": "user", "text": u})
        out.append({"role": "assistant", "text": text, "s": round(time.monotonic() - t0, 1)})
        history += [{"role": "user", "content": u}, {"role": "assistant", "content": text}]
        rt._add_turn("user", u); rt._add_turn("assistant", text)
        try:  # production runs the judge after every agent turn (off the audio path)
            await cr.judge_objectives(rt, [o for o in rt.objectives if o["name"] not in rt._done], notify=False)
        except Exception:  # noqa: BLE001
            pass
    from .. import leads

    await leads.sweep(rt)  # what finalize_conversation does at the end of a call
    with DB(db.engine) as s:
        s.expire_all()
        lead = s.exec(select(Lead).where(Lead.conversation_id == cid)).first()
        from ..models_features import ToolCallLog
        calls = [(t.tool_name, json.loads(t.arguments), t.ok, t.result) for t in s.exec(select(ToolCallLog).where(ToolCallLog.conversation_id == cid)).all()]
    # objective judge pass (same code as production)
    objectives = sorted(rt._done)
    return {"transcript": out, "lead": ({"name": lead.name, "email": lead.email, "phone": lead.phone, "consent": lead.consent,
                                         "company": lead.company, "notes": lead.notes} if lead else None),
            "tool_calls": calls, "objectives_completed": objectives}


def check_qa(tpl: dict, res: dict, q: dict) -> dict:
    ans = _words(res["transcript"][-1]["text"])
    ok = all(any(_words(k) in ans for k in grp) for grp in q["expect"])
    return {"q": q["q"], "answer": res["transcript"][-1]["text"], "pass": ok}


def check_probe(res: dict, p: dict) -> dict:
    ans = _words(res["transcript"][-1]["text"])
    bad = [m for m in p.get("must_not", []) if m.lower() in ans]
    any_ok = (not p.get("must_any")) or any(m.lower() in ans for m in p["must_any"])
    if p.get("language") == "hi":
        any_ok = any_ok or bool(re.search("[ऀ-ॿ]", ans))
        if not re.search("[ऀ-ॿ]", ans):
            bad.append("reply not in Devanagari")
    return {"q": p["q"], "answer": res["transcript"][-1]["text"], "pass": not bad and any_ok, "violations": bad}


async def eval_template(tid: str, model: str) -> dict:
    tpl = T.get(tid)
    c, h, pid = _setup(tid, model)
    rep: dict = {"template": tid, "model": model, "qa": [], "probes": [], "lead": None, "decline": None}
    for q in tpl["sample_questions"]:
        res = await run_conversation(c, h, pid, [q["q"]], model)
        rep["qa"].append(check_qa(tpl, res, q))
    for p in tpl["probes"]:
        res = await run_conversation(c, h, pid, [p["q"]], model)
        rep["probes"].append(check_probe(res, p))
    q0 = tpl["sample_questions"][0]["q"]
    res = await run_conversation(c, h, pid, [t.format(q=q0) for t in LEAD_TURNS], model)
    lead = res["lead"]
    rep["lead"] = {**res, "pass": bool(lead and lead["phone"].endswith("9876543210") and lead["consent"] and "Ravi" in lead["name"])}
    res = await run_conversation(c, h, pid, [t.format(q=q0) for t in DECLINE_TURNS], model)
    after = " ".join(x["text"].lower() for x in res["transcript"][-2:] if x["role"] == "assistant")
    pushy = bool(re.search(r"\b(your (name|email|phone|number)|contact details|phone number)\b", after)) and "?" in after.split(".")[-1] and False
    rep["decline"] = {**res, "pass": res["lead"] is None and not any(n == "capture_lead" for n, *_ in res["tool_calls"]) and not pushy}
    if tid == "online-tutor":  # Hindi in -> Hindi out
        c2, h2, pid2 = c, h, pid
        res = await run_conversation(c2, h2, pid2, ["मुझे ग्रेड 8 की गणित में रैखिक समीकरण समझाओ।"], model)
        txt = res["transcript"][-1]["text"]
        rep["hindi"] = {"answer": txt, "pass": bool(re.search("[ऀ-ॿ]", txt))}
    rep["pass_all"] = (all(x["pass"] for x in rep["qa"]) and all(x["pass"] for x in rep["probes"]) and rep["lead"]["pass"]
                       and rep["decline"]["pass"] and rep.get("hindi", {"pass": True})["pass"])
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--out", default="/tmp/tpl_eval.json")
    a = ap.parse_args()
    SQLModel.metadata.create_all(db.engine)
    ids = [a.template] if a.template else sorted(T.load())
    results = []
    for tid in ids:
        r = asyncio.run(eval_template(tid, a.model))
        results.append(r)
        print(f"{tid}: qa {sum(x['pass'] for x in r['qa'])}/{len(r['qa'])} probes {sum(x['pass'] for x in r['probes'])}/{len(r['probes'])} "
              f"lead {r['lead']['pass']} decline {r['decline']['pass']}" + (f" hindi {r['hindi']['pass']}" if 'hindi' in r else ""), flush=True)
        json.dump(results, open(a.out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
