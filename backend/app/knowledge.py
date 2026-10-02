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
import re
from collections import Counter
from typing import Optional, Protocol

import numpy as np
from sqlmodel import Session, delete, select

from . import db
from .models_extra import KnowledgeChunk, KnowledgeDoc, MemorySummary, ensure_tables

# ---------------- chunking ----------------


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
    """Paragraph-aware chunking; long paragraphs are split on sentences, then hard-split."""
    text = re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()
    if not text:
        return []
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            units.append(para)
            continue
        for sent in re.split(r"(?<=[.!?])\s+", para):
            while len(sent) > max_chars:
                units.append(sent[:max_chars])
                sent = sent[max_chars - overlap:]
            if sent:
                units.append(sent)
    chunks, cur = [], ""
    for u in units:
        if cur and len(cur) + len(u) + 1 > max_chars:
            chunks.append(cur)
            cur = (cur[-overlap:] + " " + u) if overlap else u
        else:
            cur = f"{cur}\n{u}" if cur else u
    if cur.strip():
        chunks.append(cur)
    return chunks


def extract_pdf_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader

    return "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


# ---------------- embedders ----------------


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...  # (n, d) L2-normalised float32


class FastEmbedder:
    name = "fastembed:BAAI/bge-small-en-v1.5"

    def __init__(self):
        from fastembed import TextEmbedding

        self.m = TextEmbedding("BAAI/bge-small-en-v1.5")

    def embed(self, texts):
        v = np.array(list(self.m.embed(texts)), dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


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

_tok = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> list[str]:
    return _tok.findall(s.lower())


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


# ---------------- ingest / retrieve ----------------


def _sess() -> Session:
    ensure_tables(db.engine)
    return Session(db.engine)


def ingest(persona_id: str, title: str, text: str, source: str = "text", session: Session | None = None) -> KnowledgeDoc:
    chunks = chunk_text(text)
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
    scores: list[float] | None = None
    method = "bm25"
    if emb is not None and all(r.embedding and r.embed_model == emb.name for r in rows):
        try:
            q = emb.embed([query])[0]
            mat = np.stack([np.frombuffer(r.embedding, dtype=np.float32) for r in rows])
            scores = (mat @ q).tolist()
            method = "dense"
        except Exception:
            scores = None
    if scores is None:
        scores = bm25_scores(query, [r.text for r in rows])
    order = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)[:k]
    return [
        {"text": rows[i].text, "score": float(scores[i]), "doc_id": rows[i].doc_id,
         "title": titles.get(rows[i].doc_id, ""), "method": method}
        for i in order if scores[i] > 0 or method == "dense"
    ]


def format_context(hits: list[dict]) -> str:
    """Block to append to the system prompt for the LLM."""
    if not hits:
        return ""
    return "Relevant knowledge (use if helpful, do not invent beyond it):\n" + "\n---\n".join(h["text"] for h in hits)


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
