"""Small pure helpers for judging a cloned voice objectively (we cannot listen)."""
import re


def _words(t: str) -> list[str]:
    t = (t or "").lower().replace("’", "'")
    return re.sub(r"[^a-z0-9' ]+", " ", t).split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate (Levenshtein over words, punctuation/case ignored). 0 = identical, can exceed 1."""
    r, h = _words(reference), _words(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return prev[-1] / len(r)
