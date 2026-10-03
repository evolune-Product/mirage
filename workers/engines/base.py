"""Lip-sync engine interface + licence metadata.

An engine turns 16 kHz mono audio into per-video-frame mouth/face crops for a prepared base clip (face_render.Base):

    eng.load(device)                      heavy init (weights); raises EngineUnavailable if files/libs are missing
    eng.warmup()
    cond = eng.mel_chunks(a16)            per-frame audio conditioning (opaque to the server)
    outs = eng.generate(base, seq, cond)  list of BGR uint8 crops, one per entry of seq; each is resized to
                                          base.boxes[seq[i]] and blended with base.masks[seq[i]] by face_render.paste

Licence metadata is plain data that is readable WITHOUT importing torch or loading weights, so MIRAGE_COMMERCIAL_ONLY
can refuse an engine before anything is downloaded or read from disk. See docs/LICENSES.md for the evidence behind each entry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


class EngineUnavailable(RuntimeError):
    """Engine cannot run here (weights/libs missing)."""


class CommercialOnlyError(RuntimeError):
    """MIRAGE_COMMERCIAL_ONLY=1 and the requested engine/weight is not licensed for commercial use."""


@dataclass(frozen=True)
class Weight:
    name: str
    licence: str
    commercial: Optional[bool]  # True = commercial use allowed, False = not allowed, None = unclear (needs a lawyer)
    note: str = ""


@dataclass(frozen=True)
class LicenceInfo:
    code_licence: str
    weights: tuple = ()
    research_only: bool = False
    note: str = ""

    @property
    def commercial(self) -> Optional[bool]:
        """False if any weight is non-commercial, None if any is unclear, else True."""
        vals = [w.commercial for w in self.weights]
        if self.research_only or any(v is False for v in vals):
            return False
        if any(v is None for v in vals):
            return None
        return True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["commercial"] = self.commercial
        return d


class LipsyncEngine:
    name = "base"
    description = ""
    licence = LicenceInfo("n/a")
    live_capable = True          # fast enough for the live path on at least some hardware
    paste_sharpen: Optional[float] = None  # None -> server default

    def load(self, device: str) -> "LipsyncEngine":
        raise NotImplementedError

    def warmup(self) -> None:
        pass

    def mel_chunks(self, a16, fps: float = 25.0):
        raise NotImplementedError

    def generate(self, base, seq, cond):
        raise NotImplementedError

    @classmethod
    def available(cls) -> tuple[bool, str]:
        """(can_load, reason). Cheap: only checks files/imports, never loads weights."""
        return True, ""
