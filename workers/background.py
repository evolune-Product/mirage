"""Custom backgrounds: person segmentation (MediaPipe selfie segmentation, CPU) + replacement with a colour, gradient or image.

A background *spec* is a small dict (JSON friendly, stored per replica / video):
    {"type": "color",    "color": "#101828"}
    {"type": "gradient", "colors": ["#0f2027", "#2c5364"], "angle": 90}      # angle 0 = left->right, 90 = top->bottom
    {"type": "image",    "path": "/abs/path.jpg", "blur": 0}                 # blur radius px (0 = sharp)
    {"type": "blur",     "radius": 25}                                       # blur the ORIGINAL background
    None / {"type": "none"}                                                  # keep the original background

Design: replacement is applied ONCE to the (short) base clip when it is prepared (see face_render.prepare_base), and the
result is cached with the base. Live frames therefore cost nothing extra (Wav2Lip only pastes a mouth on top of the already
replaced base frames) and offline renders reuse the same frames. Segmentation is ~6-10 ms/frame on the M1 CPU.

Quality tricks: the mask is temporally smoothed (EMA) so edges do not shimmer, thresholded softly (smoothstep) with a small
erosion to avoid a halo of the old background around hair, and edges are feathered. A "fill hole" pass removes speckle.
Known limits: low-res selfie model (256 px mask upscaled) -> fine hair strands are not preserved, glasses with a bright
background behind them can leak a little."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import cv2
import numpy as np

_seg = None


def spec_key(spec: dict | None) -> str:
    """Stable short hash of a spec (for cache directories). Image backgrounds include the file's size+mtime."""
    spec = normalize(spec)
    if not spec:
        return "none"
    s = dict(spec)
    if s.get("type") == "image" and s.get("path"):
        p = Path(s["path"])
        s["_f"] = f"{p.stat().st_size}:{int(p.stat().st_mtime)}" if p.exists() else "missing"
    return hashlib.sha1(json.dumps(s, sort_keys=True).encode()).hexdigest()[:10]


_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_color(c: str) -> tuple[int, int, int]:
    """'#rrggbb' -> BGR tuple."""
    m = _HEX.match(c.strip())
    if not m:
        raise ValueError(f"bad colour {c!r}: use #rrggbb")
    v = m.group(1)
    return int(v[4:6], 16), int(v[2:4], 16), int(v[0:2], 16)


def normalize(spec: dict | None) -> dict | None:
    """Validate + canonicalise a spec. Raises ValueError with a user-readable message."""
    if not spec or spec.get("type") in (None, "none"):
        return None
    t = spec["type"]
    if t == "color":
        parse_color(spec.get("color", ""))
        return {"type": "color", "color": "#" + spec["color"].lstrip("#").lower()}
    if t == "gradient":
        cols = spec.get("colors") or []
        if not 2 <= len(cols) <= 4:
            raise ValueError("gradient needs 2-4 colours")
        for c in cols:
            parse_color(c)
        return {"type": "gradient", "colors": ["#" + c.lstrip("#").lower() for c in cols], "angle": float(spec.get("angle", 90))}
    if t == "image":
        if not spec.get("path"):
            raise ValueError("image background needs a path")
        return {"type": "image", "path": str(spec["path"]), "blur": int(spec.get("blur", 0))}
    if t == "blur":
        return {"type": "blur", "radius": max(3, int(spec.get("radius", 25)))}
    raise ValueError(f"unknown background type {t!r} (color|gradient|image|blur|none)")


def make_plate(spec: dict, w: int, h: int, frame0: np.ndarray | None = None) -> np.ndarray:
    """Background image (BGR uint8, h x w) for a spec. For 'blur' the plate is per-frame, see apply()."""
    t = spec["type"]
    if t == "color":
        return np.full((h, w, 3), parse_color(spec["color"]), np.uint8)
    if t == "gradient":
        cols = np.array([parse_color(c) for c in spec["colors"]], np.float32)
        a = np.deg2rad(spec.get("angle", 90))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = (xx / max(w - 1, 1) - 0.5) * np.cos(a) + (yy / max(h - 1, 1) - 0.5) * np.sin(a)
        d = (d - d.min()) / max(d.max() - d.min(), 1e-6)
        pos = np.linspace(0, 1, len(cols))
        out = np.stack([np.interp(d, pos, cols[:, k]) for k in range(3)], -1)
        return out.astype(np.uint8)
    if t == "image":
        im = cv2.imread(spec["path"])
        if im is None:
            raise ValueError("background image could not be read")
        ih, iw = im.shape[:2]
        k = max(w / iw, h / ih)  # cover
        im = cv2.resize(im, (max(w, int(iw * k + 0.5)), max(h, int(ih * k + 0.5))), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
        y0, x0 = (im.shape[0] - h) // 2, (im.shape[1] - w) // 2
        im = im[y0:y0 + h, x0:x0 + w]
        if spec.get("blur", 0) > 0:
            r = int(spec["blur"]) | 1
            im = cv2.GaussianBlur(im, (r, r), 0)
        return im
    raise ValueError(t)


class Segmenter:
    """MediaPipe selfie segmentation (model_selection=1 = landscape 256x144, best for webcam framing)."""

    def __init__(self, model_selection: int = 1):
        import mediapipe as mp

        self._s = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=model_selection)

    def __call__(self, bgr: np.ndarray) -> np.ndarray:
        """-> float32 person probability (h, w) in [0,1]."""
        return self._s.process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).segmentation_mask.astype(np.float32)


def segmenter() -> Segmenter:
    global _seg
    if _seg is None:
        _seg = Segmenter()
    return _seg


def refine(mask: np.ndarray, erode: int = 1, feather: float = 1.2) -> np.ndarray:
    """probability -> soft alpha: smoothstep around 0.5, fill holes, tiny erosion, feather."""
    m = np.clip((mask - 0.35) / 0.3, 0, 1)
    m = m * m * (3 - 2 * m)
    # largest connected component + holes filled (person is one blob; removes speckle in the background)
    b = (m > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(b, 8)
    if n > 2:
        keep = np.zeros_like(b)
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        keep[lab == big] = 1
        # keep other blobs that are reasonably large (hands, a second shoulder) but drop specks
        for i in range(1, n):
            if i != big and stats[i, cv2.CC_STAT_AREA] > 0.01 * b.size:
                keep[lab == i] = 1
        m = m * cv2.dilate(keep, np.ones((5, 5), np.uint8))
    inv = 1 - (m > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(inv, 4)
    for i in range(1, n):  # background-labelled holes not touching the border are filled
        x, y, w, h, a = stats[i]
        if x > 0 and y > 0 and x + w < m.shape[1] and y + h < m.shape[0] and a < 0.02 * m.size:
            m[lab == i] = 1.0
    if erode > 0:
        m = cv2.erode(m, np.ones((2 * erode + 1, 2 * erode + 1), np.uint8))
    if feather > 0:
        m = cv2.GaussianBlur(m, (0, 0), feather)
    return np.clip(m, 0, 1)


def person_masks(frames: list[np.ndarray], ema: float = 0.55, erode: int = 1, feather: float = 1.2) -> list[np.ndarray]:
    """Per-frame soft person masks at full frame resolution, temporally smoothed."""
    seg = segmenter()
    out, prev = [], None
    for f in frames:
        h, w = f.shape[:2]
        p = seg(f)
        if p.shape != (h, w):
            p = cv2.resize(p, (w, h), interpolation=cv2.INTER_LINEAR)
        prev = p if prev is None else ema * p + (1 - ema) * prev
        out.append(refine(prev, erode, feather))
    return out


def composite(frame: np.ndarray, mask: np.ndarray, plate: np.ndarray) -> np.ndarray:
    a = mask[..., None]
    return np.clip(frame.astype(np.float32) * a + plate.astype(np.float32) * (1 - a), 0, 255).astype(np.uint8)


def apply(frames: list[np.ndarray], spec: dict | None, masks: list[np.ndarray] | None = None) -> list[np.ndarray]:
    """Replace the background of every frame. Returns new frames (same size). No-op for spec None."""
    spec = normalize(spec)
    if not spec or not frames:
        return frames
    masks = masks or person_masks(frames)
    h, w = frames[0].shape[:2]
    out = []
    if spec["type"] == "blur":
        r = (spec["radius"] | 1)
        for f, m in zip(frames, masks):
            out.append(composite(f, m, cv2.GaussianBlur(f, (r, r), 0)))
        return out
    plate = make_plate(spec, w, h)
    return [composite(f, m, plate) for f, m in zip(frames, masks)]
