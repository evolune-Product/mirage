"""In-memory dataset for Mirage-1 training (everything is ~1 minute of video, so it all lives on the device)."""
import json
from pathlib import Path

import numpy as np
import torch

from .model import WIN


class Clips:
    def __init__(self, root, device, names=("founder", "demo"), split="train", mean=None, std=None):
        self.dev = device
        self.crops, self.feats, self.ranges, self.names = [], [], [], []
        train_rng = []
        for n in names:
            d = Path(root) / n
            meta = json.loads((d / "meta.json").read_text())
            c = torch.from_numpy(np.load(d / "crops.npy")[..., ::-1].copy()).permute(0, 3, 1, 2).contiguous()  # BGR->RGB uint8
            self.crops.append(c.to(device))
            self.feats.append(torch.from_numpy(np.load(d / "feats.npy").astype(np.float32)))
            self.ranges.append(tuple(meta["split"][split]))
            train_rng.append(tuple(meta["split"]["train"]))
            self.names.append(n)
        if mean is None:  # normalisation statistics from TRAIN frames only
            allf = torch.cat([f[a:b] for f, (a, b) in zip(self.feats, train_rng)])
            mean, std = allf.mean(0), allf.std(0) + 1e-5
        self.mean, self.std = mean.cpu(), std.cpu()
        self.feats = [((f - self.mean) / self.std).to(device) for f in self.feats]
        self.pad = WIN // 2

    def window(self, ci, idx):  # idx: LongTensor (B,) -> (B,WIN,768); edge frames are replicated
        f = self.feats[ci]
        o = torch.arange(-self.pad, self.pad + 1, device=idx.device)
        j = (idx[:, None] + o[None]).clamp(0, len(f) - 1)
        return f[j]

    def sample(self, B, min_gap=25, aug=True):
        """Random training batch: target, reference (same clip, >= min_gap frames away), audio window. Colour/shift augment."""
        lens = np.array([r[1] - r[0] for r in self.ranges], float)
        cis = np.random.choice(len(lens), B, p=lens / lens.sum())
        tg, rf, au = [], [], []
        for ci in np.unique(cis):
            n = int((cis == ci).sum())
            lo, hi = self.ranges[ci]
            t = torch.from_numpy(np.random.randint(lo, hi, n)).to(self.dev)
            r = torch.from_numpy(np.random.randint(lo, hi, n)).to(self.dev)
            bad = (t - r).abs() < min_gap
            far = torch.where(t - lo > hi - t, torch.full_like(t, lo), torch.full_like(t, hi - 1))
            r = torch.where(bad, far, r)
            tg.append(self.crops[ci][t]); rf.append(self.crops[ci][r]); au.append(self.window(ci, t))
        tg, rf, au = [torch.cat(x) for x in (tg, rf, au)]
        tg, rf = tg.float() / 255, rf.float() / 255
        if not aug:
            return tg, rf, au
        B = tg.shape[0]  # same colour jitter + translation for target and reference (same camera session)
        g = 1 + 0.15 * (torch.rand(B, 1, 1, 1, device=self.dev) - 0.5) * 2
        b = 0.06 * (torch.rand(B, 1, 1, 1, device=self.dev) - 0.5) * 2
        tg, rf = (tg * g + b).clamp(0, 1), (rf * g + b).clamp(0, 1)
        dx, dy = np.random.randint(-3, 4, 2)
        tg = torch.roll(tg, (int(dy), int(dx)), (2, 3))
        rf = torch.roll(rf, (int(dy), int(dx)), (2, 3))
        return tg, rf, au
