"""Burn-in captions (PIL, no libass needed) with word timing from the TTS audio.

Timing sources, best first:
  1. `words` = [{"w","s","e"}...] from Whisper word timestamps (computed in the backend venv, see app/creative_jobs.py), aligned to the
     SCRIPT text with difflib so the caption shows the script's exact spelling/punctuation, not Whisper's transcript;
  2. fallback `uniform_words(script, audio)`: script words spread over the speech part of the audio (leading/trailing silence
     trimmed with an energy gate), weighted by word length. Good to ~100-200 ms for read speech.

Styles (all rendered once per (line, active word) into an RGBA overlay and alpha-blended per frame, ~1 ms/frame):
  classic   white text on a translucent dark box, bottom centre (documentary / lecture)
  bold      big uppercase, heavy outline, 2-3 words at a time, active word in accent colour (shorts / reels)
  minimal   lighter weight white text with soft shadow, no box
  karaoke   full line on a box, words already spoken in accent colour

Everything is positioned relative to the output frame so 16:9, 9:16 and 1:1 all look right (9:16 keeps text inside the
platform safe zone, above the bottom ~18%)."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

STYLES = ("classic", "bold", "minimal", "karaoke")
FONT_CANDIDATES = {
    "bold": ["/System/Library/Fonts/Supplemental/Arial Black.ttf", "/System/Library/Fonts/Supplemental/Impact.ttf",
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    "regular": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
    "semibold": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}


@lru_cache(maxsize=32)
def font(kind: str, size: int):
    for p in FONT_CANDIDATES.get(kind, []):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default(size)


@dataclass
class Word:
    text: str
    s: float
    e: float


# ------------------------------------------------------------------ timing
_norm_re = re.compile(r"[^\w']+", re.UNICODE)


def norm(w: str) -> str:
    return _norm_re.sub("", w.lower())


def script_words(script: str) -> list[str]:
    return [w for w in re.split(r"\s+", script.strip()) if norm(w)]


def speech_span(a16: np.ndarray, sr: int = 16000, db_gate: float = -46.0) -> tuple[float, float]:
    """(start, end) seconds of the audible part of the audio."""
    hop = int(0.02 * sr)
    n = len(a16) // hop
    if n == 0:
        return 0.0, len(a16) / sr
    rms = np.sqrt((a16[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    idx = np.where(db > db_gate)[0]
    if len(idx) == 0:
        return 0.0, len(a16) / sr
    return max(0.0, idx[0] * 0.02 - 0.02), min(len(a16) / sr, (idx[-1] + 1) * 0.02 + 0.04)


def uniform_words(script: str, a16: np.ndarray, sr: int = 16000) -> list[Word]:
    """Fallback timing: spread the script over the audible span, weighted by word length (+pause weight after punctuation)."""
    ws = script_words(script)
    if not ws:
        return []
    t0, t1 = speech_span(a16, sr)
    weights = np.array([len(norm(w)) + 2 + (5 if re.search(r"[.!?]$", w) else 2 if re.search(r"[,;:]$", w) else 0) for w in ws], float)
    edges = np.r_[0, np.cumsum(weights)] / weights.sum() * (t1 - t0) + t0
    return [Word(w, float(edges[i]), float(edges[i + 1])) for i, w in enumerate(ws)]


def align_words(script: str, heard: list[dict], duration: float) -> list[Word]:
    """Map Whisper words (timestamps) onto the script words. Unmatched script words are interpolated between matched ones."""
    ws = script_words(script)
    if not ws or not heard:
        return []
    a = [norm(w) for w in ws]
    b = [norm(h["w"]) for h in heard]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    t_s = [None] * len(ws)
    t_e = [None] * len(ws)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            t_s[blk.a + k], t_e[blk.a + k] = float(heard[blk.b + k]["s"]), float(heard[blk.b + k]["e"])
    if all(t is None for t in t_s):
        return []
    # interpolate runs of unmatched words between the neighbouring anchors
    i = 0
    while i < len(ws):
        if t_s[i] is not None:
            i += 1
            continue
        j = i
        while j < len(ws) and t_s[j] is None:
            j += 1
        lo = t_e[i - 1] if i > 0 else float(heard[0]["s"]) if i == 0 else 0.0
        hi = t_s[j] if j < len(ws) else min(duration, float(heard[-1]["e"]))
        hi = max(hi, lo + 0.05 * (j - i))
        step = (hi - lo) / (j - i)
        for k in range(i, j):
            t_s[k], t_e[k] = lo + (k - i) * step, lo + (k - i + 1) * step
        i = j
    out = [Word(w, t_s[k], max(t_e[k], t_s[k] + 0.04)) for k, w in enumerate(ws)]
    for k in range(len(out) - 1):  # words never overlap
        out[k].e = min(out[k].e, out[k + 1].s) if out[k + 1].s > out[k].s else out[k].e
    return out


def group_lines(words: list[Word], max_chars: int, max_words: int, max_lines: int = 1) -> list[list[Word]]:
    """Split into caption chunks at punctuation / length limits. A chunk = what is on screen at once."""
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        text = " ".join(x.text for x in cur)
        end_punct = bool(re.search(r"[.!?]$", w.text))
        soft = bool(re.search(r"[,;:]$", w.text)) and len(text) > max_chars * 0.55
        if len(text) >= max_chars * max_lines or len(cur) >= max_words * max_lines or end_punct or soft:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    # merge a tiny trailing orphan chunk (<=1 word) into the previous one if it still fits
    if len(chunks) > 1 and len(chunks[-1]) == 1 and len(" ".join(x.text for x in chunks[-2] + chunks[-1])) <= max_chars * max_lines * 1.15:
        last = chunks.pop()
        chunks[-1] = chunks[-1] + last
    return chunks


# ------------------------------------------------------------------ rendering
def _wrap(draw: ImageDraw.ImageDraw, words: list[str], fnt, max_w: int) -> list[list[int]]:
    """Greedy word wrap -> list of lines, each a list of word indices."""
    lines, cur = [], []
    for i, w in enumerate(words):
        trial = " ".join(words[j] for j in cur + [i])
        if cur and draw.textlength(trial, font=fnt) > max_w:
            lines.append(cur)
            cur = [i]
        else:
            cur.append(i)
    if cur:
        lines.append(cur)
    return lines


class CaptionRenderer:
    def __init__(self, style: str, size: tuple[int, int], accent: str = "#ffd23f"):
        if style not in STYLES:
            raise ValueError(f"unknown caption style {style!r} (one of {', '.join(STYLES)})")
        self.style, (self.W, self.H) = style, size
        self.accent = tuple(int(accent.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        vertical = self.H > self.W * 1.2
        short = min(self.W, self.H)
        # text size relative to the short side, so captions look similar in every aspect ratio
        self.fs = int(short * {"classic": 0.058, "bold": 0.092, "minimal": 0.054, "karaoke": 0.056}[style])
        self.max_w = int(self.W * (0.86 if style != "bold" else 0.9))
        self.bottom = int(self.H * (0.80 if vertical else 0.92))  # y of the caption block's bottom edge (safe zone on 9:16)
        self.max_chars = {"classic": 38, "bold": 16, "minimal": 40, "karaoke": 34}[style] if not vertical else \
            {"classic": 26, "bold": 14, "minimal": 28, "karaoke": 24}[style]
        self.max_words = 3 if style == "bold" else 8
        self.max_lines = 2 if style in ("classic", "minimal", "karaoke") else 1
        self._cache: dict = {}
        self.chunks: list[list[Word]] = []

    def set_words(self, words: list[Word]):
        self.chunks = group_lines(words, self.max_chars, self.max_words, self.max_lines)
        self.starts = [c[0].s for c in self.chunks]

    def _active(self, t: float):
        """-> (chunk index, active word index) at time t, or None between chunks (hold a chunk up to 0.35 s after its end)."""
        for ci, c in enumerate(self.chunks):
            nxt = self.chunks[ci + 1][0].s if ci + 1 < len(self.chunks) else 1e9
            if c[0].s - 0.05 <= t < min(c[-1].e + 0.35, nxt):
                ai = 0
                for k, w in enumerate(c):
                    if t >= w.s:
                        ai = k
                return ci, ai
        return None

    def _overlay(self, ci: int, ai: int):
        key = (ci, ai if self.style in ("bold", "karaoke") else -1)
        if key in self._cache:
            return self._cache[key]
        chunk = self.chunks[ci]
        texts = [w.text.upper() if self.style == "bold" else w.text for w in chunk]
        kind = {"bold": "bold", "classic": "semibold", "minimal": "regular", "karaoke": "semibold"}[self.style]
        fnt = font(kind, self.fs)
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        lines = _wrap(d, texts, fnt, self.max_w)[: max(self.max_lines, 1) + (1 if self.style == "bold" else 0)]
        lh = int(self.fs * 1.28)
        block_h = lh * len(lines)
        y = self.bottom - block_h
        widths = [d.textlength(" ".join(texts[i] for i in ln), font=fnt) for ln in lines]
        if self.style in ("classic", "karaoke"):
            pad = int(self.fs * 0.38)
            bx0 = int((self.W - max(widths)) / 2 - pad * 1.4)
            bx1 = int((self.W + max(widths)) / 2 + pad * 1.4)
            d.rounded_rectangle((bx0, y - pad, bx1, y + block_h + pad * 0.6), radius=int(self.fs * 0.35), fill=(8, 10, 18, 168))
        shadow = None
        if self.style == "minimal":
            shadow = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        for li, ln in enumerate(lines):
            x = (self.W - widths[li]) / 2
            ty = y + li * lh
            for i in ln:
                col = (255, 255, 255)
                if self.style == "bold" and i == ai:
                    col = self.accent
                if self.style == "karaoke" and i <= ai:
                    col = self.accent
                if self.style == "bold":
                    d.text((x, ty), texts[i], font=fnt, fill=col, stroke_width=max(2, self.fs // 9), stroke_fill=(0, 0, 0))
                elif self.style == "minimal":
                    ImageDraw.Draw(shadow).text((x + 2, ty + 3), texts[i], font=fnt, fill=(0, 0, 0, 220))
                    d.text((x, ty), texts[i], font=fnt, fill=col)
                else:
                    d.text((x, ty), texts[i], font=fnt, fill=col)
                x += d.textlength(texts[i] + " ", font=fnt)
        if shadow is not None:
            shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, self.fs // 14)))
            img = Image.alpha_composite(shadow, img)
        arr = np.asarray(img)
        # crop to the bounding box of non-zero alpha for a cheap blit
        ys, xs = np.where(arr[..., 3] > 0)
        if len(ys) == 0:
            out = None
        else:
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            rgba = arr[y0:y1, x0:x1].astype(np.float32)
            out = (x0, y0, rgba[..., 2::-1].copy(), rgba[..., 3:4] / 255.0)  # BGR + alpha
        self._cache[key] = out
        return out

    def draw(self, frame: np.ndarray, t: float) -> np.ndarray:
        """Return frame (BGR uint8, size W x H) with the caption active at time t burned in (in place)."""
        act = self._active(t)
        if act is None:
            return frame
        ov = self._overlay(*act)
        if ov is None:
            return frame
        x0, y0, bgr, a = ov
        h, w = bgr.shape[:2]
        roi = frame[y0:y0 + h, x0:x0 + w].astype(np.float32)
        frame[y0:y0 + h, x0:x0 + w] = (roi * (1 - a) + bgr * a).astype(np.uint8)
        return frame


def srt(words: list[Word], max_chars: int = 38, max_words: int = 8) -> str:
    """SRT text for the same chunks (side artefact: lets customers re-use the captions in their own player)."""
    def ts(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    out = []
    for i, c in enumerate(group_lines(words, max_chars, max_words, 2), 1):
        out.append(f"{i}\n{ts(c[0].s)} --> {ts(c[-1].e)}\n{' '.join(w.text for w in c)}\n")
    return "\n".join(out)
