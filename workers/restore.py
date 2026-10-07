"""Optional face/mouth restoration applied to the lip-sync model output BEFORE compositing.

  sr       Real-ESRGAN general-x4v3 (SRVGGNetCompact, 4.9 MB, torch, runs on MPS/CUDA/CPU): 96 -> 384 px
  gfpgan   GFPGAN 1.4 (ONNX, 340 MB, onnxruntime CoreML/CUDA/CPU): face restoration at 512 px

Weights are downloaded on first use into $VOCALFACE_FACE_MODELS (default workers/models)."""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
MODELS = Path(os.environ.get("VOCALFACE_FACE_MODELS", HERE / "models"))
URLS = {
    "realesr-general-x4v3.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
    "gfpgan_1.4.onnx": "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/gfpgan_1.4.onnx",
}
_cache: dict = {}


def weight(name: str) -> Path:
    p = MODELS / name
    if not p.exists() or p.stat().st_size < 1_000_000:
        MODELS.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URLS[name], p)
    return p


def _srvgg(device):
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class SRVGGNetCompact(nn.Module):  # same layout as basicsr's (BSD-3), so the official weights load
        def __init__(self, nf=64, nc=32, up=4):
            super().__init__()
            self.up = up
            layers = [nn.Conv2d(3, nf, 3, 1, 1), nn.PReLU(nf)]
            for _ in range(nc):
                layers += [nn.Conv2d(nf, nf, 3, 1, 1), nn.PReLU(nf)]
            layers.append(nn.Conv2d(nf, 3 * up * up, 3, 1, 1))
            self.body = nn.ModuleList(layers)
            self.ps = nn.PixelShuffle(up)

        def forward(self, x):
            o = x
            for l in self.body:
                o = l(o)
            return self.ps(o) + F.interpolate(x, scale_factor=self.up, mode="nearest")

    m = SRVGGNetCompact()
    sd = torch.load(weight("realesr-general-x4v3.pth"), map_location="cpu")
    m.load_state_dict(sd["params"])
    return m.to(device).eval()


def restore_sr(imgs: list[np.ndarray], device: str, bs: int = 16) -> list[np.ndarray]:
    import torch

    key = ("sr", device)
    if key not in _cache:
        _cache[key] = _srvgg(device)
    m = _cache[key]
    out = []
    for i in range(0, len(imgs), bs):
        x = torch.from_numpy(np.stack(imgs[i:i + bs])[..., ::-1].copy()).permute(0, 3, 1, 2).float().div(255).to(device)
        with torch.no_grad():
            y = m(x).clamp(0, 1)
        out.extend((y.permute(0, 2, 3, 1).cpu().numpy()[..., ::-1] * 255).round().astype(np.uint8))
    return out


def restore_gfpgan(imgs: list[np.ndarray], device: str) -> list[np.ndarray]:
    import onnxruntime as ort

    if "gfpgan" not in _cache:
        prov = [p for p in ("CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
        if device == "cpu":
            prov = ["CPUExecutionProvider"]
        _cache["gfpgan"] = ort.InferenceSession(str(weight("gfpgan_1.4.onnx")), providers=prov)
    s = _cache["gfpgan"]
    inp = s.get_inputs()[0].name
    out = []
    for im in imgs:
        x = cv2.resize(im, (512, 512), interpolation=cv2.INTER_CUBIC)[..., ::-1].astype(np.float32) / 255.0
        x = ((x - 0.5) / 0.5).transpose(2, 0, 1)[None]
        y = s.run(None, {inp: x})[0][0]
        y = ((y.transpose(1, 2, 0) * 0.5 + 0.5).clip(0, 1) * 255).round().astype(np.uint8)[..., ::-1]
        out.append(np.ascontiguousarray(y))
    return out


def restore_faces(imgs, mode: str, device: str):
    if mode == "sr":
        return restore_sr(imgs, device)
    if mode == "gfpgan":
        return restore_gfpgan(imgs, device)
    raise ValueError(mode)
