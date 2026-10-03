"""Vision-language-model eval for the perception feature: speed + quality of small local VLMs through Ollama.

  .venv/bin/python -m evals.vlm_eval --models moondream,gemma3:4b [--face path/to/face.png]

Synthetic test frames are rendered with PIL (screen capture with an invoice, a code/terminal screen, a shapes scene);
an optional real photo (--face) is used for a scene-description sanity check (keywords checked by hand/regex only).
Reports per model: OCR/QA correctness, median latency at the sent resolution, RAM reported by Ollama.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics as st
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.perception.vlm import VLM, PROMPTS

FONT = "/System/Library/Fonts/Helvetica.ttc"


def _font(n):
    try:
        return ImageFont.truetype(FONT, n)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def img_invoice():
    im = Image.new("RGB", (1280, 720), "#f4f5f7")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1280, 60], fill="#2b3a67")
    d.text((24, 14), "Billing - Acme Dashboard", fill="white", font=_font(30))
    d.rectangle([80, 110, 1200, 650], fill="white", outline="#cccccc")
    for i, t in enumerate(["Invoice #4821", "Customer: Acme Corp", "Total due: $1,250.00", "Due date: Nov 14, 2026", "Status: OVERDUE"]):
        d.text((120, 150 + i * 80), t, fill="#b00020" if "OVERDUE" in t else "#111111", font=_font(46))
    return im


def img_code():
    im = Image.new("RGB", (1280, 720), "#1e1e1e")
    d = ImageDraw.Draw(im)
    d.text((20, 20), "main.py - Visual Studio Code", fill="#cccccc", font=_font(26))
    code = ["import numpy as np", "", "def add(a, b):", "    return a + b", "", "print(add(2, 3))"]
    for i, l in enumerate(code):
        d.text((60, 90 + i * 48), l, fill="#9cdcfe", font=_font(38))
    d.rectangle([0, 470, 1280, 720], fill="#000000")
    d.text((40, 490), "$ python main.py", fill="#00ff66", font=_font(36))
    d.text((40, 550), "ModuleNotFoundError: No module named 'numpy'", fill="#ff5555", font=_font(36))
    return im


def img_shapes():
    im = Image.new("RGB", (1024, 640), "#ffffff")
    d = ImageDraw.Draw(im)
    d.ellipse([80, 180, 330, 430], fill="#e02020")
    d.rectangle([700, 180, 950, 430], fill="#1f4fe0")
    d.polygon([(512, 150), (400, 450), (624, 450)], fill="#12a040")
    d.text((330, 520), "SALE 50% OFF", fill="#000000", font=_font(70))
    return im


# (image fn, question prompt, must-groups, kind)
CASES = [
    (img_invoice, "What is the total due and the due date? Quote them exactly.", [["1,250", "1250"], ["nov"]], "ocr"),
    (img_invoice, "What invoice number is shown?", [["4821"]], "ocr"),
    (img_invoice, PROMPTS["screen"], [["invoice", "billing"]], "describe"),
    (img_code, "What error message is in the terminal?", [["numpy"], ["modulenotfound", "no module"]], "ocr"),
    (img_code, PROMPTS["screen"], [["python", "code", "terminal", "vs code", "visual studio", "main.py"]], "describe"),
    (img_shapes, "What colour is the circle and where is it?", [["red"], ["left"]], "scene"),
    (img_shapes, "What does the sign at the bottom say?", [["sale"], ["50"]], "ocr"),
    (img_shapes, "How many shapes are there and what colours are they?", [["red"], ["blue"], ["green"]], "scene"),
]


def jpeg(im: Image.Image, max_side: int = 768, q: int = 70) -> bytes:
    im = im.copy()
    im.thumbnail((max_side, max_side))
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=q)
    return b.getvalue()


def ok(ans, must):
    a = ans.lower().replace(",", "")
    return all(any(x.replace(",", "") in a for x in g) for g in must)


async def run_model(model: str, max_side: int, face: str | None):
    v = VLM(model, num_predict=100)
    out = {"model": model, "max_side": max_side, "cases": []}
    t0 = time.monotonic()
    await v.describe(jpeg(img_shapes(), max_side), "Hi", timeout=300)  # warm / load
    out["load_s"] = round(time.monotonic() - t0, 1)
    async with httpx.AsyncClient() as c:
        ps = (await c.get("http://localhost:11434/api/ps")).json().get("models", [])
    m = next((x for x in ps if x["name"].startswith(model.split(":")[0])), None)
    out["ram_gb"] = round(m["size"] / 1e9, 1) if m else None
    lat = []
    cache = {}
    for fn, prompt, must, kind in CASES:
        im = cache.setdefault(fn, fn())
        t = time.monotonic()
        a = await v.describe(jpeg(im, max_side), prompt, timeout=300)
        ms = (time.monotonic() - t) * 1000
        lat.append(ms)
        out["cases"].append({"kind": kind, "q": prompt[:60], "a": a, "ok": ok(a, must), "ms": int(ms)})
    if face:
        t = time.monotonic()
        a = await v.describe(jpeg(Image.open(face), max_side), PROMPTS["camera"], timeout=300)
        out["face"] = {"a": a, "ms": int((time.monotonic() - t) * 1000)}
    out["accuracy"] = round(sum(c["ok"] for c in out["cases"]) / len(out["cases"]), 2)
    out["by_kind"] = {k: round(sum(c["ok"] for c in out["cases"] if c["kind"] == k) / sum(1 for c in out["cases"] if c["kind"] == k), 2)
                      for k in {c["kind"] for c in out["cases"]}}
    out["latency_ms_median"] = int(st.median(lat))
    out["latency_ms_max"] = int(max(lat))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="moondream")
    ap.add_argument("--max-side", type=int, default=768)
    ap.add_argument("--face", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    res = []
    for m in a.models.split(","):
        r = asyncio.run(run_model(m, a.max_side, a.face or None))
        res.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "cases"}, ensure_ascii=False), flush=True)
        for c in r["cases"]:
            print("   ", "OK " if c["ok"] else "BAD", c["ms"], "ms |", c["q"], "->", c["a"][:150].replace("\n", " "), flush=True)
    if a.out:
        open(a.out, "w").write(json.dumps(res, indent=1, ensure_ascii=False))
