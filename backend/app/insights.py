"""Conversation insights: lexicon sentiment per user turn, trend, topics, talk ratio. Local, deterministic, no model
or paid service (an LLM pass can replace `score_text` later; the schema does not change)."""
from __future__ import annotations

import json
import re
from collections import Counter

POS = set("""thanks thank great good awesome perfect love excellent amazing helpful wonderful nice happy cool fantastic
brilliant appreciate pleased yes sure sounds works glad fine best easy clear useful""".split())
NEG = set("""bad terrible awful hate useless angry annoyed frustrated frustrating confusing confused wrong broken slow
worst problem issue complaint disappointed disappointing refund cancel never stupid ridiculous unacceptable expensive
waste poor sucks horrible rude annoying""".split())
NEGATORS = {"not", "no", "never", "dont", "don't", "isn't", "isnt", "wasn't", "cant", "can't", "wont", "won't", "didn't", "didnt", "hardly"}
STOP = set("""a an the and or but if so to of in on at for with about from by is are was were be been am do does did have has had
i me my we our you your it its this that these those there here what which who whom how when where why can could would
should will just not no yes ok okay please hi hello hey thanks thank like also very really get got want need know tell
let us going go one some any more much than then them they he she his her as up out into over can't don't i'm it's""".split())
_W = re.compile(r"[a-z']+")


def score_text(text: str) -> float:
    """-1..1. Negation flips the next sentiment word."""
    words = _W.findall(text.lower())
    score = hits = 0
    for i, w in enumerate(words):
        v = 1 if w in POS else -1 if w in NEG else 0
        if not v:
            continue
        if any(x in NEGATORS for x in words[max(0, i - 2):i]):
            v = -v
        score += v; hits += 1
    if not hits:
        return 0.0
    return max(-1.0, min(1.0, score / (hits + 1)))  # +1 damps one-word extremes


def label_of(x: float) -> str:
    return "positive" if x >= 0.2 else "negative" if x <= -0.2 else "neutral"


def topics_of(texts: list[str], n: int = 5) -> list[str]:
    c = Counter(w for t in texts for w in _W.findall(t.lower()) if len(w) > 3 and w not in STOP and w not in POS and w not in NEG)
    return [w for w, _ in c.most_common(n)]


def analyse(turns: list[dict]) -> dict:
    """turns: [{seq, role, text, interrupted}] -> insight dict."""
    users = [t for t in turns if t["role"] == "user"]
    agents = [t for t in turns if t["role"] == "assistant"]
    per = [{"seq": t["seq"], "score": round(score_text(t["text"]), 3)} for t in users]
    overall = round(sum(p["score"] for p in per) / len(per), 3) if per else 0.0
    trend = "flat"
    if len(per) >= 4:
        h = len(per) // 2
        a = sum(p["score"] for p in per[:h]) / h
        b = sum(p["score"] for p in per[h:]) / (len(per) - h)
        trend = "improving" if b - a >= 0.15 else "declining" if a - b >= 0.15 else "flat"
    return {"sentiment": overall, "label": label_of(overall), "trend": trend,
            "topics": topics_of([t["text"] for t in users]), "turn_sentiments": per,
            "user_words": sum(len(t["text"].split()) for t in users),
            "agent_words": sum(len(t["text"].split()) for t in agents),
            "questions": sum(t["text"].count("?") or (1 if re.match(r"\s*(what|how|why|when|where|who|can|could|do|does|is|are|will)\b", t["text"], re.I) else 0) for t in users),
            "interruptions": sum(1 for t in agents if t.get("interrupted"))}


def compute_and_store(s, cid: str):
    from . import convo_runtime as cr
    from .db import Conversation
    from .models_gap import ConversationInsight

    conv = s.get(Conversation, cid)
    if conv is None:
        return None
    turns = [{"seq": t.seq, "role": t.role, "text": t.text, "interrupted": t.interrupted} for t in cr.conversation_turns(s, cid)]
    d = analyse(turns)
    row = s.get(ConversationInsight, cid) or ConversationInsight(conversation_id=cid, account_id=conv.account_id, persona_id=conv.persona_id)
    row.sentiment, row.label, row.trend = d["sentiment"], d["label"], d["trend"]
    row.topics, row.turn_sentiments = json.dumps(d["topics"]), json.dumps(d["turn_sentiments"])
    row.user_words, row.agent_words, row.questions, row.interruptions = d["user_words"], d["agent_words"], d["questions"], d["interruptions"]
    s.add(row); s.commit(); s.refresh(row)
    return row


def out(r) -> dict:
    tot = r.user_words + r.agent_words
    return {"conversation_id": r.conversation_id, "sentiment": r.sentiment, "label": r.label, "trend": r.trend,
            "topics": json.loads(r.topics), "turn_sentiments": json.loads(r.turn_sentiments),
            "user_words": r.user_words, "agent_words": r.agent_words,
            "user_talk_ratio": round(r.user_words / tot, 3) if tot else 0.0,
            "questions": r.questions, "interruptions": r.interruptions}
