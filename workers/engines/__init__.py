"""Pluggable lip-sync engines + the MIRAGE_COMMERCIAL_ONLY licence gate.

Select with MIRAGE_LIPSYNC_ENGINE=viseme|musetalk|wav2lip|auto (default auto). `auto` picks the best engine that is
allowed in the current mode and loadable: commercial-only -> musetalk if weights+GPU are present, else viseme;
otherwise wav2lip (research-licensed, dev default) falling back to viseme.

MIRAGE_COMMERCIAL_ONLY=1 refuses to load any engine whose licence is not commercial-safe (Wav2Lip), and the server's
/health lists what is disabled and why."""
from __future__ import annotations

import importlib
import os

from .base import CommercialOnlyError, EngineUnavailable, LicenceInfo, LipsyncEngine, Weight  # noqa: F401

# name -> (module, class). Imported lazily so listing engines never imports torch.
_SPECS = {
    "viseme": ("engines.viseme", "VisemeEngine"),
    "musetalk": ("engines.musetalk", "MuseTalkLiveEngine"),
    "wav2lip": ("engines.wav2lip", "Wav2LipLiveEngine"),
}
ORDER_COMMERCIAL = ("musetalk", "viseme")
ORDER_DEV = ("wav2lip", "viseme")


def commercial_only() -> bool:
    return os.environ.get("MIRAGE_COMMERCIAL_ONLY", "0").strip().lower() in ("1", "true", "yes", "on")


def engine_class(name: str):
    if name not in _SPECS:
        raise KeyError(f"unknown lip-sync engine {name!r}; choose from {sorted(_SPECS)} or auto")
    mod, cls = _SPECS[name]
    return getattr(importlib.import_module(mod), cls)


def check_allowed(name: str, strict: bool | None = None) -> None:
    """Raise CommercialOnlyError if MIRAGE_COMMERCIAL_ONLY is on and `name` is not commercial-safe.
    'Unclear' counts as allowed only when MIRAGE_COMMERCIAL_ALLOW_UNCLEAR=1 (default: refused)."""
    if not (commercial_only() if strict is None else strict):
        return
    lic = engine_class(name).licence
    ok = lic.commercial
    if ok is None and os.environ.get("MIRAGE_COMMERCIAL_ALLOW_UNCLEAR", "0") == "1":
        ok = True  # explicit operator opt-in after legal review (docs/LICENSES.md)
    if not ok:
        if lic.commercial is None:
            bad = [f"{w.name} ({w.licence}; {w.note})" for w in lic.weights if w.commercial is None]
            raise CommercialOnlyError(f"engine '{name}' is disabled by MIRAGE_COMMERCIAL_ONLY=1: unclear weight licence: "
                                      f"{', '.join(bad)}. Set MIRAGE_COMMERCIAL_ALLOW_UNCLEAR=1 only after legal review")
        bad = [f"{w.name} ({w.licence})" for w in lic.weights if w.commercial is False] or ["research-only licence"]
        raise CommercialOnlyError(f"engine '{name}' is disabled by MIRAGE_COMMERCIAL_ONLY=1: {', '.join(bad)}")


def status(strict: bool | None = None) -> dict:
    """Everything /health reports: per-engine licence, availability, and whether commercial-only mode disables it."""
    strict = commercial_only() if strict is None else strict
    out = {}
    for name in _SPECS:
        cls = engine_class(name)
        try:
            check_allowed(name, strict)
            disabled = None
        except CommercialOnlyError as e:
            disabled = str(e)
        try:
            ok, why = cls.available()
        except Exception as e:  # noqa: BLE001 - missing optional deps must not break /health
            ok, why = False, f"{type(e).__name__}: {e}"
        out[name] = {"licence": cls.licence.to_dict(), "disabled_by_policy": disabled, "available": ok,
                     "unavailable_reason": why or None, "live_capable": cls.live_capable, "description": cls.description}
    return {"commercial_only": strict, "engines": out,
            "disabled": sorted(n for n, v in out.items() if v["disabled_by_policy"])}


def resolve(name: str | None = None, strict: bool | None = None) -> str:
    """Engine name to use. Explicit name that violates commercial-only raises; 'auto' picks the best allowed+available."""
    strict = commercial_only() if strict is None else strict
    name = (name or os.environ.get("MIRAGE_LIPSYNC_ENGINE") or "auto").lower()
    if name != "auto":
        check_allowed(name, strict)
        return name
    for cand in (ORDER_COMMERCIAL if strict else ORDER_DEV):
        try:
            check_allowed(cand, strict)
        except CommercialOnlyError:
            continue
        try:
            ok, _ = engine_class(cand).available()
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            return cand
    raise EngineUnavailable("no lip-sync engine is both allowed and available")


def create(name: str, device: str) -> LipsyncEngine:
    check_allowed(name)  # defence in depth: load path re-checks
    return engine_class(name)().load(device)


# Non-engine components that cannot be used in a commercial build. Scripts call require_commercial_safe() first.
COMPONENTS_BLOCKED = {
    "liveportrait": "LivePortrait loads InsightFace buffalo_l (det_10g.onnx, 2d106det.onnx): models are non-commercial "
                    "research only (LivePortrait/LICENSE + insightface README). Replace the cropper with MediaPipe or buy an "
                    "InsightFace licence.",
}


def require_commercial_safe(component: str) -> None:
    """Exit-by-exception guard for worker scripts (photo_idle.py, render_liveportrait.py)."""
    if commercial_only() and component in COMPONENTS_BLOCKED:
        raise CommercialOnlyError(f"'{component}' is disabled by MIRAGE_COMMERCIAL_ONLY=1: {COMPONENTS_BLOCKED[component]}")
