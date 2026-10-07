"""Wav2Lip engine. RESEARCH / NON-COMMERCIAL ONLY.

Upstream README (workers/Wav2Lip/README.md): "As the models are trained on the LRS2 dataset, any form of commercial use is
strictly prohibited" and "This repository can only be used for personal/research/non-commercial purposes." The author's
commercial offer is the hosted Sync Labs API. Refused when VOCALFACE_COMMERCIAL_ONLY=1."""
from __future__ import annotations

from pathlib import Path

from .base import CommercialOnlyError, EngineUnavailable, LicenceInfo, LipsyncEngine, Weight

CKPT = Path(__file__).resolve().parent.parent / "Wav2Lip" / "checkpoints" / "wav2lip_gan.pth"


class Wav2LipLiveEngine(LipsyncEngine):
    name = "wav2lip"
    description = "Wav2Lip-GAN 96 px, 100+ fps on MPS; research licence (dev/demo only)"
    licence = LicenceInfo(
        "Non-commercial research (repo README)",
        weights=(Weight("wav2lip_gan.pth / wav2lip.pth", "research-only; trained on LRS2", False,
                        "README: any form of commercial use is strictly prohibited"),),
        research_only=True,
        note="Dev/demo default only. Never ship in a commercial build.")

    @classmethod
    def available(cls):
        return (True, "") if CKPT.exists() else (False, f"missing {CKPT}")

    def load(self, device: str):
        from . import check_allowed

        check_allowed("wav2lip")
        import face_render as fr

        if not CKPT.exists():
            raise EngineUnavailable(f"missing {CKPT}")
        self._e = fr.Wav2LipEngine(device)
        return self

    def warmup(self):
        self._e.warmup()

    def mel_chunks(self, a16, fps: float = 25.0):
        return self._e.mel_chunks(a16, fps)

    def generate(self, base, seq, cond):
        return self._e.generate(base, seq, cond)
