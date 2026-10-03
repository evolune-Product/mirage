"""Validation of creative render options (pure python: the backend venv has no numpy/cv2).

Mirrors workers/background.py + workers/formats.py + workers/captions.py constants; tests/test_creative_options.py checks the
lists stay in sync when the workers modules are importable."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

ASPECTS = ("16:9", "9:16", "1:1")
RESOLUTIONS = (480, 720, 1080)
TRANSITIONS = ("cut", "fade", "dip", "slide")
CAPTION_STYLES = ("classic", "bold", "minimal", "karaoke")
LOGO_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")
BG_TYPES = ("none", "color", "gradient", "image", "blur")
_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _hex(c, what: str) -> str:
    if not isinstance(c, str) or not _HEX.match(c.strip()):
        raise ValueError(f"{what}: use a #rrggbb colour")
    return "#" + c.strip().lstrip("#").lower()


Resolver = Callable[[str, str], Path]  # (asset_id, kind) -> file path; raises ValueError


def validate_background(spec: Optional[dict], resolve: Resolver) -> Optional[dict]:
    if not spec or spec.get("type") in (None, "none"):
        return None
    t = spec["type"]
    if t not in BG_TYPES:
        raise ValueError(f"background.type must be one of {', '.join(BG_TYPES)}")
    if t == "color":
        return {"type": "color", "color": _hex(spec.get("color"), "background.color")}
    if t == "gradient":
        cols = spec.get("colors")
        if not isinstance(cols, list) or not 2 <= len(cols) <= 4:
            raise ValueError("background.colors: give 2-4 colours")
        angle = float(spec.get("angle", 90))
        return {"type": "gradient", "colors": [_hex(c, "background.colors") for c in cols], "angle": angle}
    if t == "blur":
        r = int(spec.get("radius", 25))
        if not 3 <= r <= 99:
            raise ValueError("background.radius must be 3-99")
        return {"type": "blur", "radius": r}
    # image
    aid = spec.get("asset_id")
    if not aid:
        raise ValueError("background image needs asset_id (upload with POST /v1/creative/assets)")
    blur = int(spec.get("blur", 0))
    if not 0 <= blur <= 99:
        raise ValueError("background.blur must be 0-99")
    return {"type": "image", "asset_id": aid, "path": str(resolve(aid, "background")), "blur": blur}


def validate_options(raw: Optional[dict], resolve: Resolver) -> dict:
    """-> normalized options dict (stored in VideoOptions.options, passed to the worker). Raises ValueError (-> HTTP 422)."""
    raw = dict(raw or {})
    known = {"format", "resolution", "background", "captions", "logo", "transition", "transition_s", "scenes", "thumbnail", "restore"}
    extra = sorted(set(raw) - known)
    if extra:
        raise ValueError(f"unknown option(s): {', '.join(extra)}")
    out: dict = {}
    fmt = raw.get("format", "16:9")
    if fmt not in ASPECTS:
        raise ValueError(f"format must be one of {', '.join(ASPECTS)}")
    out["format"] = fmt
    res = int(raw.get("resolution", 720))
    if res not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}")
    out["resolution"] = res
    out["background"] = validate_background(raw.get("background"), resolve)
    cap = raw.get("captions")
    if cap:
        if cap is True:
            cap = {}
        style = cap.get("style", "classic")
        if style not in CAPTION_STYLES:
            raise ValueError(f"captions.style must be one of {', '.join(CAPTION_STYLES)}")
        out["captions"] = {"style": style, "accent": _hex(cap.get("accent", "#ffd23f"), "captions.accent")}
    logo = raw.get("logo")
    if logo:
        aid = logo.get("asset_id")
        if not aid:
            raise ValueError("logo needs asset_id (upload with POST /v1/creative/assets kind=logo)")
        pos = logo.get("position", "top-right")
        if pos not in LOGO_POSITIONS:
            raise ValueError(f"logo.position must be one of {', '.join(LOGO_POSITIONS)}")
        scale, opacity = float(logo.get("scale", 0.14)), float(logo.get("opacity", 0.9))
        if not 0.04 <= scale <= 0.5 or not 0.1 <= opacity <= 1.0:
            raise ValueError("logo.scale must be 0.04-0.5 and logo.opacity 0.1-1.0")
        out["logo"] = {"asset_id": aid, "path": str(resolve(aid, "logo")), "position": pos, "scale": scale, "opacity": opacity}
    tr = raw.get("transition", "fade")
    if tr not in TRANSITIONS:
        raise ValueError(f"transition must be one of {', '.join(TRANSITIONS)}")
    out["transition"] = tr
    ts = float(raw.get("transition_s", 0.4))
    if not 0.1 <= ts <= 1.5:
        raise ValueError("transition_s must be 0.1-1.5")
    out["transition_s"] = ts
    sc = raw.get("scenes", "paragraphs")
    if sc not in ("paragraphs", "single"):
        raise ValueError("scenes must be 'paragraphs' (blank-line separated paragraphs become scenes) or 'single'")
    out["scenes"] = sc
    out["thumbnail"] = bool(raw.get("thumbnail", True))
    rs = raw.get("restore", "none")
    if rs not in ("none", "sr"):
        raise ValueError("restore must be 'none' or 'sr'")
    out["restore"] = rs
    return out
