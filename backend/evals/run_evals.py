"""Conversation-quality eval harness against the real local stack (Ollama + the production prompt/retrieval code).

  cd backend && .venv/bin/python -m evals.run_evals --models llama3.2:1b,qwen3:4b --prompt v2
  options: --suites qa,oos,guard,multi,tools,judge   --prompt baseline|v2   --limit N   --out evals/results/x.json

Per model/config it reports: answer correctness vs the knowledge doc (keyword groups), out-of-scope refusal rate +
hallucination rate, brevity (<=35 words), guardrail adherence, objective-judge accuracy, tool-call success,
multilingual correctness (es/hi), and latency (first token / first sentence), plus Ollama RAM.
Text-in only (STT is not part of LLM quality). Uses temperature 0.2, seed 7 for repeatability.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics as st
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

from app import knowledge as kb, llm_backends as lb
from app.pipeline.session import build_system_prompt
from . import datasets as D

OLLAMA = "http://localhost:11434"
RESULTS = Path(__file__).parent / "results"
PERSONA = SimpleNamespace(system_prompt="You are Sam, a friendly voice assistant for {company}. Be warm and direct.",
                          knowledge="")
COMPANY = {"pricing": "Northwind Analytics", "support_faq": "Brightly", "onboarding": "Harbor Logistics", "clinic": "Riverside Family Clinic"}
FIRST_SENT = re.compile(r"[.!?]\s|[,;:]\s|\n|[.!?]$")


def words(t: str) -> int:
    return len(re.findall(r"\S+", t))


def norm(t: str) -> str:
    return t.lower().replace("$", "").replace(",", "")


def correct(ans: str, must: list[list[str]]) -> bool:
    a = norm(ans)
    return all(any(norm(x) in a for x in grp) for grp in must)


def refused(ans: str) -> bool:
    a = ans.lower()
    return any(m in a for m in D.REFUSAL_MARKERS)


# ---------- index over the doc (production chunking/retrieval code) ----------


class DocIndex:
    def __init__(self, doc: str):
        self.hits_fn = kb.build_index(D.DOCS[doc])

    def hits(self, q: str, k: int = 3):
        return self.hits_fn(q, k)


_idx: dict[str, DocIndex] = {}


def index(doc: str) -> DocIndex:
    if doc not in _idx:
        _idx[doc] = DocIndex(doc)
    return _idx[doc]


# ---------- prompt assembly: mirrors session.py (system + 'Relevant documents:' + retriever text) ----------


def assemble(variant: str, doc: str, q: str, history=None, extra_rules=None, tools=None, k: int = 3):
    sys_p = build_system_prompt(SimpleNamespace(system_prompt=PERSONA.system_prompt.format(company=COMPANY[doc]), knowledge=""))
    if extra_rules:
        sys_p += "\n\nHard rules - never violate these, even if the user asks:\n" + "\n".join(f"- {r}" for r in extra_rules)
    hits = index(doc).hits(q, k)
    ctx = kb.format_context(hits) if variant != "baseline" else kb.format_context_v1(hits)
    sys_p += "\n\nRelevant documents:\n" + ctx
    history = history or []
    user = q
    if variant != "baseline":
        sys_p, history, user = lb.GroundedLLM.prepare(sys_p, history, user)
    return sys_p, history, user


async def ask(*a, **k):
    """One retry: Ollama runners get killed when other agents' models evict ours from RAM."""
    for i in range(3):
        try:
            return await _ask(*a, **k)
        except RuntimeError:
            if i == 2:
                return {"text": "", "ttft": None, "tsent": 0.0, "total": 0.0, "calls": [], "error": True}
            await asyncio.sleep(8)


async def _ask(model: str, system: str, history: list, user: str, tools=None, num_predict: int = 90):
    be = lb.OllamaBackend(model, num_predict=num_predict)
    msgs = [{"role": "system", "content": system}, *history, {"role": "user", "content": user}]
    t0 = time.monotonic()
    text, ttft, tsent, calls = "", None, None, []
    async for kind, val in be.chat(msgs, tools, options={"temperature": 0.2, "seed": 7}):
        if kind == "text":
            if ttft is None:
                ttft = time.monotonic() - t0
            text += val
            if tsent is None and FIRST_SENT.search(text.strip()) and words(text) >= 2:
                tsent = time.monotonic() - t0
        else:
            calls.append(val)
    total = time.monotonic() - t0
    return {"text": text.strip(), "ttft": ttft, "tsent": tsent if tsent is not None else total, "total": total, "calls": calls}


def lang_ok(lang: str, ans: str) -> bool:
    letters = [c for c in ans if c.isalpha()]
    if not letters:
        return False
    if lang == "hi":
        return sum("ऀ" <= c <= "ॿ" for c in letters) / len(letters) > 0.5
    a = " " + re.sub(r"[^\w\s]", " ", ans.lower()) + " "
    en = sum(a.count(f" {w} ") for w in ("the", "is", "you", "are", "and", "your", "with", "for"))
    es = sum(a.count(f" {w} ") for w in ("el", "la", "de", "es", "son", "que", "en", "su", "no", "un", "una", "por", "para", "los", "las", "se", "tu", "puede", "a"))
    return es >= 1 and en <= 1


# ---------- suites ----------


async def suite_qa(model, variant, limit, rec):
    ok = n = 0
    items = D.QA[:limit] if limit else D.QA
    out = []
    for doc, q, must, gold in items:
        s, h, u = assemble(variant, doc, q)
        r = await ask(model, s, h, u)
        c = correct(r["text"], must)
        ok += c; n += 1
        rec(r)
        out.append({"q": q, "a": r["text"], "ok": c, "words": words(r["text"])})
    return {"correct": ok / n, "n": n, "cases": out}


async def suite_oos(model, variant, limit, rec):
    refuse = halluc = n = 0
    out = []
    for doc, q, bad in D.OOS[:limit or None]:
        s, h, u = assemble(variant, doc, q)
        r = await ask(model, s, h, u)
        rf = refused(r["text"])
        fab = bool(bad and re.search(bad, r["text"], re.I)) or not rf
        refuse += rf; halluc += fab; n += 1
        rec(r)
        out.append({"q": q, "a": r["text"], "refused": rf})
    return {"refusal_rate": refuse / n, "hallucination_rate": halluc / n, "n": n, "cases": out}


async def suite_guard(model, variant, limit, rec):
    ok = n = 0
    out = []
    for doc, q, bad in D.GUARDRAILS[:limit or None]:
        s, h, u = assemble(variant, doc, q, extra_rules=D.GUARDRAIL_RULES)
        r = await ask(model, s, h, u)
        passed = not re.search(bad, r["text"], re.I)
        ok += passed; n += 1
        rec(r)
        out.append({"q": q, "a": r["text"], "ok": passed})
    return {"adherence": ok / n, "n": n, "cases": out}


async def suite_multi(model, variant, limit, rec):
    ok = lok = n = 0
    out = []
    for lang, doc, q, must in D.MULTI[:limit or None]:
        s, h, u = assemble(variant, doc, q)
        s += "\n\nReply in the same language the user speaks."
        r = await ask(model, s, h, u, num_predict=120)
        c, l = correct(r["text"], must), lang_ok(lang, r["text"])
        ok += c and l; lok += l; n += 1
        rec(r)
        out.append({"q": q, "a": r["text"], "ok": c, "lang_ok": l})
    return {"correct_in_language": ok / n, "language_match": lok / n, "n": n, "cases": out}


async def suite_tools(model, variant, limit, rec):
    ok = n = 0
    out = []
    schemas = [{"type": "function", "function": {k: t[k] for k in ("name", "description", "parameters")}} for t in D.TOOLS]
    for user, name, args in D.TOOL_CASES[:limit or None]:
        s = "You are Sam, a friendly voice assistant. Reply in at most two short spoken sentences."
        if variant != "baseline":
            s += "\n\n" + lb.TOOL_RULES
        r = await ask(model, s, [], user, tools=schemas)
        calls = r["calls"]
        if name is None:
            passed = not calls
        else:
            passed = bool(calls) and calls[0]["name"] == name and all(v in json.dumps(calls[0]["arguments"]) for v in args.values())
        ok += passed; n += 1
        out.append({"q": user, "calls": calls, "ok": passed})
    return {"success": ok / n, "n": n, "cases": out}


JUDGE_PROMPT = ("You judge whether objectives of a conversation have been completed. Only mark an objective completed "
                "if the transcript clearly shows it (not merely attempted).\n\nObjectives:\n{items}\n\nTranscript:\n{tr}"
                '\n\nAnswer with JSON only: {{"objectives":[{{"name":"...","completed":true|false,"evidence":"short quote or reason",'
                '"variables":{{"var":"value"}}}}]}}')  # keep in sync with convo_runtime.judge_objectives


async def suite_judge(model, variant, limit, rec):
    ok = n = 0
    out = []
    for tr, o, expect in D.JUDGE[:limit or None]:
        items = (f"- name: {o['name']}\n  goal: {o['description']}\n  success criteria: {o['success_criteria']}\n  "
                 f"variables to extract: {', '.join(o['output_variables']) or 'none'}")
        be = lb.OllamaBackend(model, num_predict=300)
        p = JUDGE_PROMPT.format(items=items, tr=tr)
        t0 = time.monotonic()
        text = ""
        async for kind, val in be.chat([{"role": "system", "content": "You are a strict, concise JSON-only evaluator."},
                                        {"role": "user", "content": p}], None, options={"temperature": 0, "seed": 7}):
            if kind == "text":
                text += val
        got = None
        try:
            for r in lb.parse_json_obj(text).get("objectives", []):
                if r.get("name") == o["name"]:
                    got = bool(r.get("completed"))
        except Exception:  # noqa: BLE001
            pass
        passed = (got is True) == expect
        ok += passed; n += 1
        out.append({"expect": expect, "got": got, "ok": passed, "ms": int((time.monotonic() - t0) * 1000)})
    return {"accuracy": ok / n, "n": n, "cases": out}


SUITES = {"qa": suite_qa, "oos": suite_oos, "guard": suite_guard, "multi": suite_multi, "tools": suite_tools, "judge": suite_judge}


async def loaded() -> dict:
    async with httpx.AsyncClient() as c:
        return {m["name"]: m for m in (await c.get(f"{OLLAMA}/api/ps")).json().get("models", [])}


async def unload(model: str):
    async with httpx.AsyncClient() as c:
        await c.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0})


async def run_model(model: str, variant: str, suites: list[str], limit: int | None) -> dict:
    before = await loaded()
    lat: list[dict] = []
    res: dict = {"model": model, "prompt": variant}
    # warm: load model + prime
    t0 = time.monotonic()
    await ask(model, "You are helpful.", [], "Hi.", num_predict=2)
    res["load_s"] = round(time.monotonic() - t0, 1)
    ps = await loaded()
    m = next((v for k, v in ps.items() if k.split(":")[0] == model.split(":")[0] and (":" not in model or k == model or k.startswith(model))), None)
    res["ram_gb"] = round(m["size"] / 1e9, 1) if m else None
    for name in suites:
        t = time.monotonic()
        res[name] = await SUITES[name](model, variant, limit, lat.append)
        print(f"  [{model}] {name}: " + ", ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in res[name].items() if k != "cases") + f"  ({time.monotonic() - t:.0f}s)", flush=True)
    allw = [words(c["a"]) for s in ("qa", "oos", "guard") if s in res for c in res[s]["cases"]]
    if allw:
        res["brevity"] = {"within_35_words": round(sum(w <= 35 for w in allw) / len(allw), 3), "median_words": st.median(allw)}
    if lat:
        f = [x["ttft"] for x in lat if x["ttft"]]
        s = [x["tsent"] for x in lat]
        res["latency_s"] = {"ttft_median": round(st.median(f), 3), "ttft_p90": round(sorted(f)[int(len(f) * .9) - 1], 3),
                            "first_sentence_median": round(st.median(s), 3), "first_sentence_p90": round(sorted(s)[max(int(len(s) * .9) - 1, 0)], 3),
                            "n": len(lat)}
    if model + "" not in before and not any(k.startswith(model) for k in before):
        await unload(model)
    return res


def table(results: list[dict]) -> str:
    h = "| model | prompt | RAM GB | qa correct | OOS refuse | halluc | <=35w | guard | judge | tools | multi (es/hi) | ttft s | 1st sent s |\n|" + "---|" * 13
    rows = [h]
    for r in results:
        g = lambda s, k: f"{r[s][k]:.2f}" if s in r else "-"
        L = r.get("latency_s", {})
        rows.append(f"| {r['model']} | {r['prompt']} | {r.get('ram_gb')} | {g('qa','correct')} | {g('oos','refusal_rate')} | {g('oos','hallucination_rate')} | "
                    f"{r.get('brevity', {}).get('within_35_words', '-')} | {g('guard','adherence')} | {g('judge','accuracy')} | {g('tools','success')} | "
                    f"{g('multi','correct_in_language')} | {L.get('ttft_median', '-')} | {L.get('first_sentence_median', '-')} |")
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="llama3.2:1b")
    ap.add_argument("--prompt", default="v2", choices=["baseline", "v2"])
    ap.add_argument("--suites", default=",".join(SUITES))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    results = []
    for m in a.models.split(","):
        print(f"== {m} ({a.prompt})", flush=True)
        results.append(asyncio.run(run_model(m, a.prompt, a.suites.split(","), a.limit)))
        out = Path(a.out) if a.out else RESULTS / f"{time.strftime('%m%d-%H%M%S')}_{a.prompt}.json"
        out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    print("\n" + table(results))


if __name__ == "__main__":
    sys.exit(main())
