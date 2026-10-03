"""Per-persona pronunciation glossary: the text sent to TTS has terms swapped for their respelling; the transcript
and captions still show the original wording."""
from __future__ import annotations

import re

MAX_ENTRIES = 200


def compile_rules(entries) -> list[tuple[re.Pattern, str]]:
    rules = []
    for e in sorted(entries, key=lambda e: -len(e.term)):  # longest term first so "AI Labs" beats "AI"
        flags = 0 if e.case_sensitive else re.IGNORECASE
        pat = r"(?<![\w])" + re.escape(e.term) + r"(?![\w])"
        rules.append((re.compile(pat, flags), e.replacement))
    return rules


def apply(text: str, rules: list[tuple[re.Pattern, str]]) -> str:
    # single pass over the original text so one replacement is never re-matched by another rule
    if not rules or not text:
        return text
    spans: list[tuple[int, int, str]] = []
    for pat, rep in rules:
        for m in pat.finditer(text):
            if not any(m.start() < b and m.end() > a for a, b, _ in spans):
                spans.append((m.start(), m.end(), rep))
    out, pos = [], 0
    for a, b, rep in sorted(spans):
        out.append(text[pos:a]); out.append(rep); pos = b
    out.append(text[pos:])
    return "".join(out)


class PronunciationTTS:
    def __init__(self, base, rules):
        self.base, self.rules = base, rules

    def __getattr__(self, name):  # sample_rate etc.
        return getattr(self.base, name)

    async def synthesize(self, text: str, voice: str = "default"):
        async for chunk in self.base.synthesize(apply(text, self.rules), voice):
            yield chunk
