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
from .train import DATA, CK, low
from .data import LIP_NAMES
from .melfeat import logmel
from .audiofeat import read_wav16k
from .lip import Ridge


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
    dev = "cpu"  # 236 frames: CPU is fast enough and avoids MPS glitches while a training job shares the GPU
    ck = torch.load(a.ckpt, map_location="cpu")
    lipmode = ck.get('args', {}).get('cond') == 'lip'
    g = Generator(lip_dim=len(LIP_NAMES) if lipmode else 0).to(dev).eval()
    g.load_state_dict(ck["ema"])
    tr = Clips(DATA, dev)
    ds = Clips(DATA, dev, split=a.split, mean=ck["mean"], std=ck["std"], lip_stats=(ck.get("lip_mean", tr.lip_mean), ck.get("lip_std", tr.lip_std)))
    res = {}
    for ci, name in enumerate(ds.names):
        lo, hi = ds.ranges[ci]
        idx = torch.arange(lo, hi, device=dev)
        tg = ds.crops[ci][idx].float() / 255
        t_lo, t_hi = tr.ranges[ci]
        # reference: a train frame with a CLOSED/neutral mouth is what a user photo looks like; use the train-segment median-time frame
        ref = ds.crops[ci][torch.full_like(idx, (t_lo + t_hi) // 2)].float() / 255
        m = make_mask(len(idx), dev)
        run = lambda c: g(tg * (1 - m), ref, m, c)
        shift = lambda x: x[(idx - lo + 75) % (hi - lo)]  # same data, 3 s later: breaks the audio/lip <-> mouth pairing
        trmean = (tr.crops[ci][t_lo:t_hi].float() / 255).mean(0, keepdim=True).expand_as(tg)
        r = dict(frames=len(idx), copy_reference_frame=stats(ref, tg), mean_train_frame=stats(trmean, tg))
        if not lipmode:
            au = ds.window(ci, idx)
            r.update(mirage1_true_audio=stats(run(au), tg), mirage1_shuffled_audio=stats(run(shift(au)), tg),
                     mirage1_zero_audio=stats(run(torch.zeros_like(au)), tg))
        else:
            gt = ds.lips[ci][idx]
            rd = Ridge.load(CK / "lipridge.npz")
            import json as _j
            src = _j.loads((DATA / name / "meta.json").read_text())["source"]
            pr = torch.from_numpy(rd.predict_wav(read_wav16k(src)).astype(np.float32))[lo:hi]  # standardised, from AUDIO only
            r.update(oracle_gt_lip=stats(run(gt), tg), oracle_gt_lip_shuffled=stats(run(shift(gt)), tg),
                     audio_pred_lip_gain1=stats(run(pr), tg), audio_pred_lip_gain2_5=stats(run(pr * 2.5), tg),
                     audio_pred_lip_shuffled=stats(run(shift(pr * 2.5)), tg), zero_lip=stats(run(torch.zeros_like(gt)), tg))
        res[name] = r
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
