"""Split a long script into scenes (pure python, no numpy/cv2: imported by the backend too)."""


def split_scenes(script: str, max_chars: int = 900, max_scenes: int = 12) -> list[str]:
    """Paragraphs (blank-line separated) become scenes; over-long paragraphs are split at sentence ends. Tiny paragraphs (<15 characters) are
    merged into the previous scene so no scene is only a few words."""
    import re

    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", script.strip()) if p.strip()]
    scenes: list[str] = []
    for p in paras:
        if len(p) <= max_chars:
            scenes.append(p)
            continue
        cur = ""
        for sent in re.split(r"(?<=[.!?])\s+", p):
            if cur and len(cur) + len(sent) + 1 > max_chars:
                scenes.append(cur)
                cur = sent
            else:
                cur = f"{cur} {sent}".strip()
        if cur:
            scenes.append(cur)
    merged: list[str] = []
    for s in scenes:
        if merged and len(s) < 15:
            merged[-1] = f"{merged[-1]} {s}"
        else:
            merged.append(s)
    if len(merged) > max_scenes:
        raise ValueError(f"script has {len(merged)} scenes; the maximum is {max_scenes}")
    return merged
