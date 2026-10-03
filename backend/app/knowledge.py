"""Per-persona knowledge base: ingest -> chunk -> embed -> retrieve, plus conversation memory.

Embeddings: fastembed (ONNX, CPU, no torch, BAAI/bge-small-en-v1.5, ~130 MB, auto-downloaded
on first use). If fastembed/model is unavailable we silently fall back to BM25 (pure python),
so retrieval always works. Tests inject a fake embedder via `set_embedder`.

Realtime usage:  `retrieve(persona_id, query, k)` -> list[dict(text, score, doc_id, title)].
It opens its own Session on db.engine (looked up at call time) so the voice loop can call it
without FastAPI dependencies. Run it in a thread (asyncio.to_thread) from async code.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import Optional, Protocol

import numpy as np
from sqlmodel import Session, delete, select

from . import db
from .models_extra import KnowledgeChunk, KnowledgeDoc, MemorySummary, ensure_tables

# ---------------- chunking ----------------


_HEAD = re.compile(r"^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$")


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100, headers: bool = False) -> list[str]:
    """Paragraph-aware chunking; long paragraphs are split on sentences, then hard-split.

    headers=True is section-aware: markdown headings ('# Title', '## Section') are not chunks of their own; every chunk
    is prefixed with its heading path ('Title > Section') and never spans two sections unless the section is tiny.
    That context is what makes "what's the Pro price?" find the chunk whose body only says "costs 99 dollars"."""
    text = re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()
    if not text:
        return []
    units: list[tuple[str, str]] = []  # (heading path, text)
    path: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if headers:
            body = []
            for line in para.split("\n"):
                m = _HEAD.match(line)
                if m:
                    lvl = len(m.group(1))
                    path = path[:lvl - 1] + [m.group(2).strip()]
                else:
                    body.append(line)
            para = "\n".join(body).strip()
            if not para:
                continue
        room = max_chars - (len(" > ".join(path)) + 1 if headers else 0)
        room = max(room, 120)
        hp = " > ".join(path) if headers else ""
        if len(para) <= room:
            units.append((hp, para))
            continue
        for sent in re.split(r"(?<=[.!?])\s+|\n", para):
            while len(sent) > room:
                units.append((hp, sent[:room]))
                sent = sent[room - overlap:] if room > overlap else sent[room:]
            if sent.strip():
                units.append((hp, sent))
    # pack units into chunks; with headers=True a chunk may hold several small sections, each introduced by its own
    # "[Title > Section]" label line, so no sentence is ever separated from the heading that explains it
    chunks: list[str] = []
    cur: list[str] = []
    size, last_hp = 0, None
    for hp, u in units:
        label = f"[{hp}]" if headers and hp and hp != last_hp else ""
        add = len(u) + len(label) + 2
        if cur and size + add > max_chars:
            chunks.append("\n".join(cur))
            tail = cur[-1][-overlap:] if overlap and not (headers and hp != last_hp) else ""
            cur, size, last_hp = [], 0, None
            label = f"[{hp}]" if headers and hp else ""
            if tail:
                u = tail + " " + u
            add = len(u) + len(label) + 2
        if label:
            cur.append(label)
        cur.append(u)
        size += add
        last_hp = hp
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def extract_pdf_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader

    return "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


# ---------------- embedders ----------------


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...  # (n, d) L2-normalised float32


EMBED_MODEL = os.environ.get("MIRAGE_EMBED_MODEL", "BAAI/bge-small-en-v1.5")


class FastEmbedder:
    """fastembed ONNX embedder. Queries go through `query_embed` (BGE's retrieval instruction prefix), documents through
    `embed`. The stored `name` keeps the old value for the default model so existing chunk embeddings stay valid."""

    def __init__(self, model: str = EMBED_MODEL):
        from fastembed import TextEmbedding

        self.name = "fastembed:" + model
        self.m = TextEmbedding(model)

    @staticmethod
    def _norm(v):
        v = np.array(list(v), dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)

    def embed(self, texts):
        return self._norm(self.m.embed(texts))

    def embed_query(self, text: str):
        q = getattr(self.m, "query_embed", None)
        return self._norm(q([text]) if q else self.m.embed([text]))[0]


_embedder: Optional[Embedder] = None
_embedder_failed = False


def set_embedder(e: Optional[Embedder]) -> None:
    """Inject an embedder (tests) or None to reset to lazy default."""
    global _embedder, _embedder_failed
    _embedder, _embedder_failed = e, False


def get_embedder() -> Optional[Embedder]:
    global _embedder, _embedder_failed
    if _embedder is None and not _embedder_failed:
        try:
            _embedder = FastEmbedder()
        except Exception:  # not installed / offline first run -> BM25 fallback
            _embedder_failed = True
    return _embedder


# ---------------- BM25 fallback ----------------

_tok = re.compile(r"[a-z0-9]+(?:[.:][0-9]+)?|[^\W\d_]+", re.I)  # words, numbers like 9:30 / 2.4, and non-latin words
_STOP = frozenset("a an the is are was were be been am do does did can could would should will shall may might of to in on at "
                  "for from by with about as into than then so if or and but not no it its this that these those i you he she "
                  "we they me my your our their what which who whom how when where why there here any some much many more "
                  "most very just also have has had get got".split())


def _stem(w: str) -> str:
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("es"):
        return w[:-2]
    if len(w) > 4 and w.endswith(("ed", "ly")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _tokens(s: str) -> list[str]:
    return [_stem(t) for t in (m.lower() for m in _tok.findall(s)) if t not in _STOP]


def bm25_scores(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    toks = [_tokens(d) for d in docs]
    n = len(docs)
    avg = (sum(map(len, toks)) / n) if n else 0.0
    df: Counter = Counter()
    for t in toks:
        df.update(set(t))
    out = []
    for t in toks:
        tf, score = Counter(t), 0.0
        for q in set(_tokens(query)):
            if q not in tf:
                continue
            idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
            score += idf * tf[q] * (k1 + 1) / (tf[q] + k1 * (1 - b + b * len(t) / (avg or 1)))
        out.append(score)
    return out


# ---------------- ranking ----------------

CHUNK_CHARS = int(os.environ.get("MIRAGE_CHUNK_CHARS", "500"))  # evals/retrieval_eval.py: 400-500 beats 800+ on h@1 and MRR
CHUNK_OVERLAP = int(os.environ.get("MIRAGE_CHUNK_OVERLAP", "60"))
RETRIEVAL_MODE = os.environ.get("MIRAGE_RETRIEVAL", "fuse")  # fuse (dense + small BM25 bonus) | dense | bm25
BM25_WEIGHT = float(os.environ.get("MIRAGE_BM25_WEIGHT", "0.04"))


def _embed_query(emb, query: str) -> np.ndarray:
    f = getattr(emb, "embed_query", None)
    return f(query) if f else emb.embed([query])[0]


def rank(query: str, texts: list[str], vecs: Optional[np.ndarray], emb: Optional[Embedder], mode: str | None = None) -> tuple[list[float], str]:
    """Score every chunk for the query. Dense cosine (bge) when vectors are available, plus a small normalised BM25 bonus
    ('fuse': keeps exact terms such as error codes or plan names winning, without letting weak lexical matches override a
    good paraphrase match - a plain 50/50 hybrid measurably hurt paraphrased questions). Falls back to pure BM25."""
    mode = mode or RETRIEVAL_MODE
    dense = None
    if vecs is not None and emb is not None and mode != "bm25":
        try:
            dense = (vecs @ _embed_query(emb, query)).astype(float)
        except Exception:  # noqa: BLE001
            dense = None
    if dense is None:
        return bm25_scores(query, texts), "bm25"
    if mode == "fuse" and BM25_WEIGHT > 0:
        bs = np.array(bm25_scores(query, texts))
        if bs.max() > 0:
            dense = dense + BM25_WEIGHT * bs / bs.max()
    return dense.tolist(), "dense"


def build_index(text: str, embedder: Optional[Embedder] = None):
    """In-memory index over one document (no DB): -> fn(query, k) -> hits. Used by the eval harness; same chunking and
    ranking as the production path."""
    chunks = chunk_text(text, CHUNK_CHARS, CHUNK_OVERLAP, headers=True)
    emb = embedder or get_embedder()
    vecs = emb.embed(chunks) if emb is not None else None

    def search(query: str, k: int = 3) -> list[dict]:
        scores, method = rank(query, chunks, vecs, emb)
        order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[:k]
        return [{"text": chunks[i], "score": float(scores[i]), "doc_id": "", "title": "", "method": method} for i in order]
    return search


# ---------------- ingest / retrieve ----------------


def _sess() -> Session:
    ensure_tables(db.engine)
    return Session(db.engine)


def ingest(persona_id: str, title: str, text: str, source: str = "text", session: Session | None = None) -> KnowledgeDoc:
    chunks = chunk_text(text, CHUNK_CHARS, CHUNK_OVERLAP, headers=True)
    if not chunks:
        raise ValueError("document has no extractable text")
    emb = get_embedder()
    vecs = None
    if emb is not None:
        try:
            vecs = emb.embed(chunks)
        except Exception:
            vecs = None  # keep text; BM25 will serve it
    own = session is None
    s = session or _sess()
    try:
        doc = KnowledgeDoc(persona_id=persona_id, title=title, source=source, n_chunks=len(chunks))
        s.add(doc)
        s.flush()
        for i, c in enumerate(chunks):
            s.add(KnowledgeChunk(
                doc_id=doc.id, persona_id=persona_id, idx=i, text=c,
                embedding=vecs[i].astype(np.float32).tobytes() if vecs is not None else None,
                embed_model=emb.name if vecs is not None else None,
            ))
        s.commit()
        s.refresh(doc)
        return doc
    finally:
        if own:
            s.close()


def delete_doc(doc_id: str, session: Session) -> None:
    session.exec(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id))
    session.exec(delete(KnowledgeDoc).where(KnowledgeDoc.id == doc_id))
    session.commit()


def retrieve(persona_id: str, query: str, k: int = 4, session: Session | None = None) -> list[dict]:
    own = session is None
    s = session or _sess()
    try:
        rows = s.exec(select(KnowledgeChunk).where(KnowledgeChunk.persona_id == persona_id)).all()
        if not rows or not query.strip():
            return []
        titles = {d.id: d.title for d in s.exec(select(KnowledgeDoc).where(KnowledgeDoc.persona_id == persona_id)).all()}
    finally:
        if own:
            s.close()
    emb = get_embedder()
    vecs = None
    if emb is not None and all(r.embedding and r.embed_model == emb.name for r in rows):
        try:
            vecs = np.stack([np.frombuffer(r.embedding, dtype=np.float32) for r in rows])
        except Exception:  # noqa: BLE001
            vecs = None
    scores, method = rank(query, [r.text for r in rows], vecs, emb)
    order = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)[:k]
    return [
        {"text": rows[i].text, "score": float(scores[i]), "doc_id": rows[i].doc_id,
         "title": titles.get(rows[i].doc_id, ""), "method": method}
        for i in order if scores[i] > 0 or method == "dense"
    ]


def format_context_v1(hits: list[dict]) -> str:
    """The original, weak scaffolding (kept for the eval A/B)."""
    if not hits:
        return ""
    return "Relevant knowledge (use if helpful, do not invent beyond it):\n" + "\n---\n".join(h["text"] for h in hits)


GROUNDING_RULES = (
    "Relevant knowledge. Answer ONLY from the numbered excerpts below. If they do not contain the answer, say in one short "
    "sentence that you don't have that information and offer to help with something else or to pass it to the team - "
    "never guess or invent prices, dates, names, numbers or policies, and never use outside knowledge for company facts. "
    "Say the key fact first, in plain words, in the user's language. Do not read out excerpt numbers, headings or the word 'document'; headings in the excerpts are only labels.")


REL_KEEP = float(os.environ.get("MIRAGE_RETRIEVAL_REL", "0.12"))


def select_hits(hits: list[dict], rel: float | None = None) -> list[dict]:
    """Drop excerpts scoring clearly below the best one (dense scores only). Measured: keeps 98.6% of the gold chunks while
    sending 1.8 instead of 3 excerpts on average; a 1B model answers better with fewer distractors and prefill is shorter."""
    rel = REL_KEEP if rel is None else rel
    if not hits or rel <= 0 or hits[0].get("method") != "dense":
        return hits
    top = hits[0]["score"]
    return [h for h in hits if h["score"] >= top - rel] or hits[:1]


def format_context(hits: list[dict]) -> str:
    """Block appended to the system prompt: grounding rules + numbered excerpts (the numbers/titles let logs and the UI
    cite sources; they are not meant to be spoken)."""
    hits = select_hits(hits)
    if not hits:
        return ""
    parts = []
    for i, h in enumerate(hits, 1):
        title = f" ({h['title']})" if h.get("title") else ""
        parts.append(f"[{i}]{title} {h['text']}")
    return GROUNDING_RULES + "\n\n" + "\n\n".join(parts)


# ---------------- conversation memory ----------------


def _extractive_summary(turns: list[dict], max_chars: int = 600) -> str:
    users = [t["content"].strip() for t in turns if t.get("role") == "user" and t.get("content")]
    s = "User said: " + " | ".join(users)
    return s[:max_chars]


def summarize_turns(turns: list[dict], llm=None) -> str:
    """llm: optional callable(prompt)->str (e.g. Ollama). Falls back to an extractive summary."""
    if llm:
        try:
            convo = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
            out = llm("Summarise this conversation in 2-3 sentences, keeping facts about the user "
                      "(name, preferences, goals) for future sessions:\n" + convo)
            if out and out.strip():
                return out.strip()
        except Exception:
            pass
    return _extractive_summary(turns)


def ollama_llm(model: str = "llama3.2:1b", host: str = "http://localhost:11434"):
    def call(prompt: str) -> str:
        import httpx

        r = httpx.post(f"{host}/api/generate", json={"model": model, "prompt": prompt, "stream": False}, timeout=120)
        r.raise_for_status()
        return r.json()["response"]
    return call


def save_memory(persona_id: str, summary: str, conversation_id: str | None = None, session: Session | None = None) -> MemorySummary:
    own = session is None
    s = session or _sess()
    try:
        m = MemorySummary(persona_id=persona_id, conversation_id=conversation_id, summary=summary)
        s.add(m); s.commit(); s.refresh(m)
        return m
    finally:
        if own:
            s.close()


def recent_memories(persona_id: str, limit: int = 5, session: Session | None = None) -> list[MemorySummary]:
    own = session is None
    s = session or _sess()
    try:
        return list(s.exec(select(MemorySummary).where(MemorySummary.persona_id == persona_id)
                           .order_by(MemorySummary.created_at.desc()).limit(limit)).all())
    finally:
        if own:
            s.close()
