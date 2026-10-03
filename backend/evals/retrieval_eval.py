"""Retrieval eval: hit@k and MRR for embedders / chunking / hybrid / rerank over the eval docs.

  .venv/bin/python -m evals.retrieval_eval [--combined]   (from backend/)

Queries = the 43 in-scope questions + the 8 Spanish/Hindi questions (cross-lingual retrieval). A hit = a retrieved chunk
contains the case's gold substring. `--combined` indexes all four docs in ONE index (harder: realistic distractors).
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from app import knowledge as kb
from . import datasets as D


class FE:
    def __init__(self, model: str, q_prefix: str = "", d_prefix: str = ""):
        from fastembed import TextEmbedding
        self.name, self.m, self.qp, self.dp = model, TextEmbedding(model), q_prefix, d_prefix

    def docs(self, t):
        v = np.array(list(self.m.embed([self.dp + x for x in t])), dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def query(self, q):
        if self.qp == "native" and hasattr(self.m, "query_embed"):
            v = np.array(list(self.m.query_embed([q])), dtype=np.float32)
        else:
            v = np.array(list(self.m.embed([("" if self.qp == "native" else self.qp) + q])), dtype=np.float32)
        return (v / np.linalg.norm(v, axis=1, keepdims=True))[0]


class OllamaEmb:
    def __init__(self, model="nomic-embed-text", q_prefix="search_query: ", d_prefix="search_document: "):
        self.name, self.qp, self.dp = "ollama:" + model, q_prefix, d_prefix
        self.model = model

    def _e(self, texts):
        import httpx
        r = httpx.post("http://localhost:11434/api/embed", json={"model": self.model, "input": texts}, timeout=120)
        v = np.array(r.json()["embeddings"], dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def docs(self, t):
        return self._e([self.dp + x for x in t])

    def query(self, q):
        return self._e([self.qp + q])[0]


def rrf(rank_lists, k=60, weights=None):
    s: dict[int, float] = {}
    for w, rl in zip(weights or [1] * len(rank_lists), rank_lists):
        for r, i in enumerate(rl):
            s[i] = s.get(i, 0) + w / (k + r + 1)
    return sorted(s, key=lambda i: -s[i])


def queries():
    qs = [(q, g, doc) for doc, q, m, g in D.QA]
    qs += [(q, next(g for d2, q2, m2, g in D.QA if d2 == doc and False) if False else None, doc) for lang, doc, q, m in D.MULTI]
    return qs


# gold substrings for the multilingual questions (same fact as the English question about the same topic)
MULTI_GOLD = ["Pro plan costs 99", "2 year limited warranty", "first day starts at 9:30", "do not accept Medicaid",
              "Pro plan costs 99", "2.4 GHz Wi-Fi only", "first day starts at 9:30", "arrive 15 minutes early"]


def run(embedder, max_chars, overlap, mode, combined, rerank=None, k_list=(1, 3, 5), headers=False, bm_w=0.6):
    docs = D.DOCS
    items = []  # (doc, chunk)
    for n, t in docs.items():
        for c in kb.chunk_text(t, max_chars, overlap, headers=headers):
            items.append((n, c))
    texts = [c for _, c in items]
    vecs = embedder.docs(texts) if embedder else None
    qs = [(q, g, doc, "en") for doc, q, m, g in D.QA] + [(q, MULTI_GOLD[i], doc, lang) for i, (lang, doc, q, m) in enumerate(D.MULTI)]
    qs += [(q, g, doc, "para") for doc, q, g in D.PARA]
    res = {"en": [], "xl": [], "para": []}
    lat = []
    for q, gold, doc, lang in qs:
        pool = [i for i, (d, _) in enumerate(items) if combined or d == doc]
        t0 = time.perf_counter()
        sub = [texts[i] for i in pool]
        dense_rank = bm_rank = None
        if vecs is not None and mode in ("dense", "hybrid") and mode != "fuse":
            sc = vecs[pool] @ embedder.query(q)
            dense_rank = list(np.argsort(-sc))
        if mode in ("bm25", "hybrid"):
            bs = kb.bm25_scores(q, sub)
            bm_rank = list(np.argsort(-np.array(bs)))
        if mode == "fuse":
            ds = vecs[pool] @ embedder.query(q)
            bs = np.array(kb.bm25_scores(q, sub))
            order = list(np.argsort(-(ds + bm_w * bs / max(bs.max(), 1e-9))))
        elif mode == "dense":
            order = dense_rank
        elif mode == "bm25":
            order = bm_rank
        else:
            order = rrf([dense_rank, bm_rank], weights=[1.0, bm_w if lang in ("en", "para") else bm_w / 3])
        if rerank:
            top = list(order[:12])
            sc = list(rerank.rerank(q, [sub[i] for i in top]))
            order = [top[i] for i in np.argsort(-np.array(sc))] + list(order[12:])
        lat.append((time.perf_counter() - t0) * 1000)
        hit_rank = next((r for r, i in enumerate(order) if gold in sub[i].replace("\n", " ") or gold in sub[i]), None)
        res[{"en": "en", "para": "para"}.get(lang, "xl")].append(hit_rank)
    out = {}
    for key, ranks in res.items():
        n = len(ranks)
        out[key] = {f"hit@{k}": round(sum(r is not None and r < k for r in ranks) / n, 3) for k in k_list}
        out[key]["mrr"] = round(sum(1 / (r + 1) for r in ranks if r is not None) / n, 3)
    out["chunks"] = len(items)
    out["q_ms"] = round(float(np.median(lat)), 1)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--combined", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--rerank", action="store_true", help="also try the MiniLM cross-encoder (downloads 80 MB)")
    a = ap.parse_args()
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    factories = {
        "bge-small(plain q)": lambda: FE("BAAI/bge-small-en-v1.5"),
        "bge-small(+instr)": lambda: FE("BAAI/bge-small-en-v1.5", "native"),
        "bge-base(+instr)": lambda: FE("BAAI/bge-base-en-v1.5", "native"),
        "arctic-s": lambda: FE("snowflake/snowflake-arctic-embed-s", "Represent this sentence for searching relevant passages: "),
        "multiling-MiniLM": lambda: FE("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
        "nomic-ollama": lambda: OllamaEmb(),
    }
    if a.quick:
        factories = {k: factories[k] for k in ("bge-small(plain q)", "bge-small(+instr)")}
    embs = {}
    for k, f in factories.items():
        try:
            embs[k] = f()
        except Exception as e:  # noqa: BLE001 - model not downloadable offline: skip it
            print("skip", k, type(e).__name__, flush=True)
    rows = []

    def go(name, emb, mc, ov, mode, rr=None, headers=False, bm_w=0.6):
        r = run(emb, mc, ov, mode, a.combined, rr, headers=headers, bm_w=bm_w)
        rows.append((name, mc, ov, mode, headers, bm_w, r))
        tag = f"{mode}{'+H' if headers else ''}{f' w={bm_w}' if mode in ('hybrid', 'fuse') else ''}{' +RR' if rr else ''}"
        print(f"{name:20s} {mc}/{ov:<3} {tag:18s} en h@1={r['en']['hit@1']} h@3={r['en']['hit@3']} mrr={r['en']['mrr']} | para h@1={r['para']['hit@1']} h@3={r['para']['hit@3']} mrr={r['para']['mrr']} | xl h@3={r['xl']['hit@3']} mrr={r['xl']['mrr']} | {r['chunks']}ch {r['q_ms']}ms", flush=True)

    base = embs["bge-small(+instr)"]
    go("bm25", None, 500, 60, "bm25", headers=True)
    for hd in (False, True):
        for mc, ov in [(400, 60), (500, 60), (700, 80)]:
            go("bge-small", base, mc, ov, "dense", headers=hd)
    for w in (0.15, 0.3, 0.6):
        go("bge-small", base, 500, 60, "hybrid", headers=True, bm_w=w)
    for w in (0.03, 0.06, 0.12):
        go("bge-small", base, 500, 60, "fuse", headers=True, bm_w=w)
    for name, emb in embs.items():
        if name not in ("bge-small(plain q)", "bge-small(+instr)"):
            go(name, emb, 500, 60, "dense", headers=True)
            go(name, emb, 500, 60, "hybrid", headers=True, bm_w=0.3)
    if a.rerank:
        rr = TextCrossEncoder("Xenova/ms-marco-MiniLM-L-6-v2")
        go("bge-small", base, 500, 60, "dense", rr, headers=True)
        go("bge-small", base, 500, 60, "hybrid", rr, headers=True, bm_w=0.3)
