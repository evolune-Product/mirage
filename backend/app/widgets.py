"""Embed widget helpers: allowed-domain matching for guest share tokens and the widget snippet."""
from __future__ import annotations

import html
import json
import re
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlmodel import Session as DB

from .models_templates import WidgetConfig

_PATTERN = re.compile(r"^(https?://)?(\*\.)?([a-z0-9]([a-z0-9-]*[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:(\d{1,5}|\*))?$")
POSITIONS = ("bottom-right", "bottom-left")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_domains(items: list[str]) -> list[str]:
    """Validate/normalise allowed embedding sites: "example.com", "*.example.com", "localhost:8000", "https://shop.example.com"."""
    if len(items) > 50:
        raise ValueError("at most 50 allowed domains")
    out: list[str] = []
    for raw in items:
        v = str(raw).strip().lower().rstrip("/")
        if not v or v == "*" or "/" in v.replace("://", "") or not _PATTERN.match(v):
            raise ValueError(f"invalid domain '{raw}': use example.com, *.example.com, localhost:8000 or https://example.com "
                             "(an empty list allows any site)")
        if v not in out:
            out.append(v)
    return out


def _parse_pattern(p: str):
    scheme = None
    if "://" in p:
        scheme, p = p.split("://", 1)
    port = None
    if ":" in p:
        p, port = p.rsplit(":", 1)
    wild = p.startswith("*.")
    return scheme, wild, p[2:] if wild else p, port


def origin_matches(patterns: list[str], origin: str) -> bool:
    try:
        u = urlparse(origin)
        host, scheme = (u.hostname or "").lower(), (u.scheme or "").lower()
        port = str(u.port or (443 if scheme == "https" else 80))
    except ValueError:
        return False
    if not host or scheme not in ("http", "https"):
        return False
    for p in patterns:
        ps, wild, ph, pp = _parse_pattern(p)
        if ps and ps != scheme:
            continue
        if pp and pp != "*" and pp != port:
            continue
        if (host.endswith("." + ph)) if wild else (host == ph):
            return True
    return False


def domains_of(w: Optional[WidgetConfig]) -> list[str]:
    try:
        return list(json.loads(w.allowed_domains)) if w else []
    except ValueError:
        return []


def frame_ancestors(patterns: list[str]) -> str:
    if not patterns:
        return "frame-ancestors *"
    src = []
    for p in patterns:
        scheme, wild, host, port = _parse_pattern(p)
        base = ("*." if wild else "") + host + (":" + port if port else ":*")
        src += [f"{scheme}://{base}"] if scheme else [f"https://{base}", f"http://{base}"]
    return "frame-ancestors 'self' " + " ".join(src)


def enforce(s: DB, token: str, headers, own_host: str = "") -> None:
    """Guest API gate: when the token has allowed domains, a browser request must come from one of them (or from the
    API's own origin, i.e. the share-link page / the widget iframe, whose embedding is limited by frame-ancestors).
    Raises HTTPException(403). Origin checks stop other websites; they do not stop scripts that forge the header,
    which is why guest links also carry cost caps and per-IP rate limits."""
    w = s.get(WidgetConfig, token)
    patterns = domains_of(w)
    if not patterns:
        return
    origin = (headers.get("origin") or "").strip()
    host = own_host or headers.get("host", "")
    if origin:
        try:
            if urlparse(origin).netloc.lower() == host.lower():
                return
        except ValueError:
            pass
        if origin_matches(patterns, origin):
            return
    raise HTTPException(403, "this agent may not be used from this website")


def validate_style(label: str, color: str, position: str, greeting: str, language: str) -> None:
    if not 1 <= len(label) <= 40:
        raise ValueError("label must be 1-40 characters")
    if not _COLOR.match(color):
        raise ValueError("color must be a #rrggbb hex value")
    if position not in POSITIONS:
        raise ValueError(f"position must be one of {POSITIONS}")
    if len(greeting) > 140:
        raise ValueError("greeting max 140 characters")
    if language:
        from . import languages

        languages.normalize_language(language)


def snippet(base: str, w: WidgetConfig) -> str:
    a = lambda k, v: f' data-{k}="{html.escape(str(v), quote=True)}"' if v else ""  # noqa: E731
    return (f'<script src="{base}/widget.js" data-token="{w.token}"' + a("label", w.label) + a("color", w.color)
            + a("position", w.position) + a("greeting", w.greeting) + a("language", w.language) + " async></script>")
