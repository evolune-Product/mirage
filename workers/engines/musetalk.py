"""MuseTalk 1.5 engine (256 px latent-space lip-sync). Code MIT; vendor states weights are 'available for any purpose,
even commercially'. Only three weight sets are loaded here (the repo's DWPose / face-parse / S3FD stack is NOT used,
mediapipe landmarks replace it): MuseTalk unet, sd-vae-ft-mse (MIT), whisper-tiny (MIT / Apache-2.0).

The unet's training data (HDTF + an undisclosed private set) makes the weight licence 'vendor-stated yes, provenance
unclear', so MIRAGE_COMMERCIAL_ONLY=1 refuses it unless the operator sets MIRAGE_COMMERCIAL_ALLOW_UNCLEAR=1 after
legal review. Env: MIRAGE_MUSETALK_RES (256 default; 192 / 128 = faster, softer), MIRAGE_MUSETALK_BS."""
from __future__ import annotations

import os

import cv2
import numpy as np

from .base import EngineUnavailable, LicenceInfo, LipsyncEngine, Weight


class MuseTalkLiveEngine(LipsyncEngine):
    name = "musetalk"
    description = "MuseTalk 1.5 (VAE+UNet, 256 px): best quality, ~3-5 fps on M1 (offline), 30+ fps on a V100/RTX-class GPU"
    live_capable = False  # on Apple Silicon; True on CUDA (see docs/overnight/commercial-engine.md)
    licence = LicenceInfo(
        "MIT (workers/MuseTalk/LICENSE)",
        weights=(
            Weight("musetalkV15/unet.pth", "MIT (vendor: 'any purpose, even commercially')", None,
                   "trained on HDTF (CC BY 4.0 per its README, YouTube-sourced) + an undisclosed private dataset"),
            Weight("sd-vae-ft-mse", "MIT (HF model card)", True),
            Weight("whisper-tiny", "MIT (openai/whisper repo; HF card says Apache-2.0)", True),
        ),
        note="DWPose (Apache-2.0), face-parse-bisent (MIT code, CelebAMask-HQ non-commercial data) and S3FD are NOT loaded.")

    @classmethod
    def available(cls):
        from musetalk_engine import musetalk_available

        if not musetalk_available():
            return False, "weights missing under workers/MuseTalk/models/{musetalkV15,sd-vae,whisper}"
        try:
            import diffusers, transformers  # noqa: F401, E401
        except Exception as e:  # noqa: BLE001
            return False, f"python deps missing: {e}"
        return True, ""

    def load(self, device: str):
        from . import check_allowed

        check_allowed("musetalk")
        from musetalk_engine import MuseTalkEngine

        ok, why = self.available()
        if not ok:
            raise EngineUnavailable(why)
        self.device = device
        self.res = int(os.environ.get("MIRAGE_MUSETALK_RES", "256"))
        self._e = MuseTalkEngine(device, res=self.res)
        self.bs = int(os.environ.get("MIRAGE_MUSETALK_BS", "8"))
        self._lat = {}
        return self

    def warmup(self):
        import numpy as np

        z = np.zeros((self.res, self.res, 3), np.uint8)
        lat = self._e.encode([z])
        p = self._e.audio_chunks(np.zeros(16000, np.float32))
        self._e.generate(lat, p[:1], [0], bs=1)

    def mel_chunks(self, a16, fps: float = 25.0):
        return self._e.audio_chunks(a16, fps)

    def _latents(self, base):
        key = (id(base), self.res)
        if key not in self._lat:
            crops = []
            for i, f in enumerate(base.frames):
                x1, y1, x2, y2 = base.boxes[i]
                c = cv2.resize(f[y1:y2, x1:x2], (self.res, self.res), interpolation=cv2.INTER_AREA)
                crops.append(c)
            self._lat = {key: self._e.encode(crops)}
        return self._lat[key]

    def generate(self, base, seq, cond):
        return self._e.generate(self._latents(base), cond, list(seq), bs=self.bs)
