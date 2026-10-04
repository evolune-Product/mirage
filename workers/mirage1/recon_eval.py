"""Held-out-frame reconstruction metrics (runs in workers/.venv-flash). For each clip's VAL segment (never trained on, separated from
train by a 25-frame gap) the lower face is hidden and regenerated from the audio window + a reference frame from the train part.
Compared on the hidden region: Mirage-1 with the true audio, with shuffled audio (shifted >= 3 s), with zeroed audio, and two
non-learned baselines (copy the reference frame; mean train frame). If true-audio is not clearly better than shuffled-audio, the
model is not using audio.   usage: python -m mirage1.recon_eval [--ckpt ...] > recon.json"""
import argparse
import json

import numpy as np
import torch
import torch.nn.functional as F

from . import align
from .data import Clips
from .model import Generator, make_mask
from .train import DATA, CK, device, low


def ssim(a, b, win=7):
    """Mean SSIM (gaussian window) between (B,3,H,W) images in [0,1]."""
    x = torch.arange(win, device=a.device).float() - win // 2
    k = torch.exp(-x ** 2 / (2 * 1.5 ** 2)); k = (k / k.sum())
    k2 = (k[:, None] * k[None]).expand(3, 1, win, win).contiguous()
    f = lambda t: F.conv2d(t, k2, groups=3)
    ma, mb = f(a), f(b)
    va, vb, cab = f(a * a) - ma ** 2, f(b * b) - mb ** 2, f(a * b) - ma * mb
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    return (((2 * ma * mb + c1) * (2 * cab + c2)) / ((ma ** 2 + mb ** 2 + c1) * (va + vb + c2))).mean().item()


def stats(out, tg):
    d = low(out) - low(tg)
    return dict(l1=round(d.abs().mean().item(), 4), psnr=round((-10 * torch.log10((d ** 2).mean((1, 2, 3)) + 1e-8)).mean().item(), 2),
                ssim=round(ssim(low(out), low(tg)), 4))


@torch.no_grad()
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=str(CK / "gen_main.pt"))
    ap.add_argument("--split", default="val")
    a = ap.parse_args()
    dev = device()
    ck = torch.load(a.ckpt, map_location="cpu")
    g = Generator().to(dev).eval()
    g.load_state_dict(ck["ema"])
    tr = Clips(DATA, dev)
    ds = Clips(DATA, dev, split=a.split, mean=ck["mean"], std=ck["std"])
    res = {}
    for ci, name in enumerate(ds.names):
        lo, hi = ds.ranges[ci]
        idx = torch.arange(lo, hi, device=dev)
        tg = ds.crops[ci][idx].float() / 255
        t_lo, t_hi = tr.ranges[ci]
        # reference: a train frame with a CLOSED/neutral mouth is what a user photo looks like; use the train-segment median-time frame
        ref = ds.crops[ci][torch.full_like(idx, (t_lo + t_hi) // 2)].float() / 255
        m = make_mask(len(idx), dev)
        run = lambda au: g(tg * (1 - m), ref, m, au)
        au = ds.window(ci, idx)
        sh = ds.window(ci, (idx - lo + 75) % (hi - lo) + lo)
        trmean = (tr.crops[ci][t_lo:t_hi].float() / 255).mean(0, keepdim=True).expand_as(tg)
        res[name] = dict(frames=len(idx), mirage1_true_audio=stats(run(au), tg), mirage1_shuffled_audio=stats(run(sh), tg),
                         mirage1_zero_audio=stats(run(torch.zeros_like(au)), tg), copy_reference_frame=stats(ref, tg),
                         mean_train_frame=stats(trmean, tg))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
