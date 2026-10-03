"""MuseTalk v1.5 (256 px latent-space lip-sync) wrapper: 'quality' engine for offline video / near-real-time on a GPU.

Uses our mediapipe landmarks for the face crop instead of MuseTalk's mmpose/DWPose + face-parsing stack, which does not
install cleanly on Apple Silicon. Only the VAE, UNet and Whisper-tiny of the original repo are used.

Weights (download once, ~4.4 GB): workers/MuseTalk/models/{musetalkV15,sd-vae,whisper}  (see workers/README_face.md)."""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

HERE = Path(__file__).resolve().parent
MT = HERE / "MuseTalk"
sys.path.insert(0, str(MT))


def musetalk_available() -> bool:
    return (MT / "models" / "musetalkV15" / "unet.pth").exists() and (MT / "models" / "sd-vae" / "diffusion_pytorch_model.bin").exists() \
        and (MT / "models" / "whisper" / "pytorch_model.bin").exists()


class MuseTalkEngine:
    def __init__(self, device: str, half: bool | None = None):
        from diffusers import AutoencoderKL, UNet2DConditionModel
        from transformers import AutoFeatureExtractor, WhisperModel
        import json
        from musetalk.models.unet import PositionalEncoding

        self.device = torch.device(device)
        # fp16 on MPS is supported for these ops in recent torch; allow override
        self.dtype = torch.float16 if (half if half is not None else device in ("cuda", "mps")) else torch.float32
        m = MT / "models"
        self.vae = AutoencoderKL.from_pretrained(str(m / "sd-vae")).to(self.device, self.dtype).eval()
        self.scaling = self.vae.config.scaling_factor
        cfg = json.load(open(m / "musetalkV15" / "musetalk.json"))
        self.unet = UNet2DConditionModel(**cfg)
        self.unet.load_state_dict(torch.load(m / "musetalkV15" / "unet.pth", map_location="cpu"))
        self.unet = self.unet.to(self.device, self.dtype).eval()
        self.pe = PositionalEncoding(d_model=384).to(self.device, self.dtype)
        self.fe = AutoFeatureExtractor.from_pretrained(str(m / "whisper"))
        self.whisper = WhisperModel.from_pretrained(str(m / "whisper")).to(self.device, self.dtype).eval()
        self.t0 = torch.tensor([0], device=self.device)
        self._mask = torch.zeros(256, 256)
        self._mask[:128] = 1

    # ---------- audio ----------
    @torch.no_grad()
    def audio_chunks(self, a16: np.ndarray, fps: float = 25.0, pad_l: int = 2, pad_r: int = 2) -> torch.Tensor:
        """16 kHz float audio -> (T, 50, 384) whisper prompts per video frame."""
        seg = 30 * 16000
        feats = []
        for i in range(0, len(a16), seg):
            f = self.fe(a16[i:i + seg], return_tensors="pt", sampling_rate=16000).input_features.to(self.device, self.dtype)
            hs = self.whisper.encoder(f, output_hidden_states=True).hidden_states
            feats.append(torch.stack(hs, dim=2))
        wf = torch.cat(feats, 1)
        mult = 50 / int(fps)
        nf = math.floor(len(a16) / 16000 * int(fps))
        wf = wf[:, :math.floor(len(a16) / 16000 * 50)]
        pn = math.ceil(mult)
        wf = torch.cat([torch.zeros_like(wf[:, :pn * pad_l]), wf, torch.zeros_like(wf[:, :pn * 3 * pad_r])], 1)
        per = 2 * (pad_l + pad_r + 1)
        clips = [wf[:, math.floor(i * mult): math.floor(i * mult) + per] for i in range(nf)]
        x = torch.cat(clips, 0)  # T,10,5,384
        return x.reshape(x.shape[0], x.shape[1] * x.shape[2], x.shape[3])

    # ---------- vision ----------
    def _prep(self, bgr256: np.ndarray, masked: bool) -> torch.Tensor:
        x = torch.from_numpy(cv2.cvtColor(bgr256, cv2.COLOR_BGR2RGB)).float().permute(2, 0, 1) / 255.0
        if masked:
            x = x * self._mask
        return ((x - 0.5) / 0.5).unsqueeze(0)

    @torch.no_grad()
    def encode(self, crops256: list[np.ndarray]) -> list[torch.Tensor]:
        out = []
        for c in crops256:
            lat = []
            for masked in (True, False):
                x = self._prep(c, masked).to(self.device, self.dtype)
                lat.append(self.scaling * self.vae.encode(x).latent_dist.mode())  # mode(): deterministic, no jitter between frames
            out.append(torch.cat(lat, 1))
        return out

    @torch.no_grad()
    def generate(self, latents: list[torch.Tensor], prompts: torch.Tensor, seq: list[int], bs: int = 8) -> list[np.ndarray]:
        outs = []
        n = len(seq)
        for b0 in range(0, n, bs):
            idx = range(b0, min(b0 + bs, n))
            lat = torch.cat([latents[seq[i]] for i in idx], 0).to(self.device, self.dtype)
            aud = self.pe(prompts[b0:b0 + len(idx)])
            pred = self.unet(lat, self.t0, encoder_hidden_states=aud).sample
            img = self.vae.decode((1 / self.scaling) * pred).sample
            img = (img / 2 + 0.5).clamp(0, 1).float().permute(0, 2, 3, 1).cpu().numpy()
            outs.extend((img * 255).round().astype(np.uint8)[..., ::-1])
        return outs


def musetalk_box(pts: np.ndarray, H: int, W: int, extra: int = 10) -> tuple:
    """MuseTalk-style face crop from mediapipe landmarks: symmetric around the nose bridge vertically, cheek to cheek
    horizontally, +extra px below the chin (v1.5)."""
    ym = pts[195][1]  # ~ dlib-68 landmark 29 (lower nose bridge) that MuseTalk anchors on
    y2 = pts[152][1] + extra
    y1 = ym - (pts[152][1] - ym)
    x1, x2 = pts[234][0], pts[454][0]
    # make it square-ish around the horizontal centre (the model was trained on ~square crops)
    cx, side = (x1 + x2) / 2, max(x2 - x1, y2 - y1)
    x1, x2 = cx - side / 2, cx + side / 2
    return (int(max(0, x1)), int(max(0, y1)), int(min(W, x2)), int(min(H, y2)))
