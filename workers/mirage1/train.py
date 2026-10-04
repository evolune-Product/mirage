"""Mirage-1 training: stage `sync` (contrastive mouth<->audio critic), stage `gen` (generator), with checkpoint/resume.
  python -m mirage1.train sync --steps 3000
  python -m mirage1.train gen  --steps 8000 [--resume] [--overfit]
Runs from workers/ with workers/.venv-flash (PyTorch MPS if available)."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from . import align
from .data import Clips
from .model import Generator, PatchDisc, SyncNet, make_mask, count, mouth_slice, WIN

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/mirage1"
CK = ROOT / "data/mirage1/ckpt"


def device():
    return "mps" if torch.backends.mps.is_available() else "cpu"


def lap_l1(a, b, levels=3):
    """L1 on a Laplacian pyramid (edges/texture), a perceptual-free sharpness loss."""
    tot = 0
    for _ in range(levels):
        a2, b2 = F.avg_pool2d(a, 2), F.avg_pool2d(b, 2)
        tot = tot + (( a - F.interpolate(a2, scale_factor=2)) - (b - F.interpolate(b2, scale_factor=2))).abs().mean()
        a, b = a2, b2
    return tot


def low(x):
    return x[:, :, int(align.MASK_ROW * align.S):]


@torch.no_grad()
def sync_eval(sn, clips, n_neg=15, max_n=2000):
    """Top-1 accuracy of picking the true audio window among 1 + n_neg windows taken >= 10 frames away; chance = 1/(n_neg+1)."""
    sn.eval()
    hit = tot = 0
    for ci, (lo, hi) in enumerate(clips.ranges):
        idx = torch.arange(lo, hi, device=clips.dev)
        v = sn.embed_v(clips.crops[ci][idx].float() / 255)
        for k in idx.tolist()[:max_n]:
            offs = np.random.choice([o for o in range(-60, 61) if abs(o) >= 10 and lo <= k + o < hi] or [10], n_neg)
            cand = torch.tensor([k] + [int(k + o) for o in offs], device=clips.dev).clamp(0, len(clips.feats[ci]) - 1)
            a = sn.embed_a(clips.window(ci, cand))
            hit += int((v[k - lo] @ a.t()).argmax() == 0)
            tot += 1
    sn.train()
    return hit / max(tot, 1)


def train_sync(a):
    dev = device()
    tr, va = Clips(DATA, dev), None
    va = Clips(DATA, dev, split="val", mean=tr.mean, std=tr.std)
    sn = SyncNet().to(dev)
    opt = torch.optim.AdamW(sn.parameters(), 1e-3, weight_decay=1e-2)
    log = []
    for step in range(1, a.steps + 1):
        tg, _, au, _ = tr.sample(64)
        v = sn.embed_v(tg)
        pa = sn.embed_a(au)
        # negatives = the other items in the batch (random frames of the same clips)
        lg = sn.logits(v, pa)
        tgt = torch.arange(len(v), device=dev)
        loss = F.cross_entropy(lg, tgt) + F.cross_entropy(lg.t(), tgt)
        opt.zero_grad(); loss.backward(); opt.step()
        if step % 250 == 0 or step == a.steps:
            acc = sync_eval(sn, va, max_n=300)
            print(f"sync step {step} loss {loss.item():.3f} val_top1(1v16) {acc:.3f}", flush=True)
            log.append((step, loss.item(), acc))
    CK.mkdir(parents=True, exist_ok=True)
    torch.save({"sync": sn.state_dict(), "log": log}, CK / "sync.pt")
    train_acc = sync_eval(sn, tr, max_n=300)
    print("final val top1", log[-1][2], "train top1", train_acc)
    json.dump({"val_top1": log[-1][2], "train_top1": train_acc, "chance": 1 / 16}, open(CK / "sync_metrics.json", "w"))


def to_img_grid(rows):
    return np.concatenate([np.concatenate([(x.permute(1, 2, 0).cpu().numpy()[..., ::-1] * 255).clip(0, 255).astype(np.uint8) for x in r], 1) for r in rows], 0)


@torch.no_grad()
def val_eval(g, va, n=96, cond='audio'):
    """Held-out reconstruction: PSNR/L1 on the hidden lower region for val frames, with a reference frame from the TRAIN part of the clip."""
    g.eval()
    ps, l1s = [], []
    grid = []
    for ci, (lo, hi) in enumerate(va.ranges):
        idx = torch.linspace(lo, hi - 1, min(n, hi - lo)).long().to(va.dev)
        ref_i = torch.full_like(idx, max(0, lo // 2))
        tg = va.crops[ci][idx].float() / 255
        rf = va.crops[ci][ref_i].float() / 255
        m = make_mask(len(idx), va.dev)
        out = g(tg * (1 - m), rf, m, va.lips[ci][idx] if cond == 'lip' else va.window(ci, idx))
        d = (low(out) - low(tg))
        l1s.append(d.abs().mean().item())
        ps.append((-10 * torch.log10((d ** 2).mean((1, 2, 3)) + 1e-8)).mean().item())
        if ci == 0:
            k = [0, len(idx) // 4, len(idx) // 2, 3 * len(idx) // 4]
            grid = [[tg[i] for i in k], [(tg * (1 - m))[i] for i in k], [out[i] for i in k]]
    g.train()
    return float(np.mean(l1s)), float(np.mean(ps)), grid


def train_gen(a):
    dev = device()
    tr = Clips(DATA, dev)
    va = Clips(DATA, dev, split="val", mean=tr.mean, std=tr.std, lip_stats=(tr.lip_mean, tr.lip_std))
    lipmode = a.cond == "lip"
    if a.overfit:  # sanity: a handful of train frames, no augmentation, must be memorised
        for i in range(len(tr.ranges)):
            tr.ranges[i] = (tr.ranges[i][0], tr.ranges[i][0] + 40)
    from .data import LIP_NAMES
    mk = lambda: Generator(lip_dim=len(LIP_NAMES) if lipmode else 0).to(dev)
    g, d = mk(), PatchDisc().to(dev)
    ema = mk()
    ema.load_state_dict(g.state_dict())
    sn = SyncNet().to(dev)
    use_sync = (CK / "sync.pt").exists() and a.w_sync > 0 and not lipmode
    if use_sync:
        sn.load_state_dict(torch.load(CK / "sync.pt")["sync"])
    sn.eval().requires_grad_(False)
    og = torch.optim.AdamW(g.parameters(), a.lr, betas=(0.5, 0.99), weight_decay=1e-4)
    od = torch.optim.AdamW(d.parameters(), a.lr, betas=(0.5, 0.99))
    step, tag = 0, a.tag
    path = CK / f"gen_{tag}.pt"
    t_prev = 0.0
    if a.resume and path.exists():
        ck = torch.load(path, map_location=dev)
        g.load_state_dict(ck["g"]); ema.load_state_dict(ck["ema"]); d.load_state_dict(ck["d"])
        og.load_state_dict(ck["og"]); od.load_state_dict(ck["od"]); step = ck["step"]; t_prev = ck.get("train_seconds", 0.0)
        print("resumed at", step)
    print(f"G params {count(g)/1e6:.2f}M, device {dev}, sync {use_sync}, train frames {[r[1]-r[0] for r in tr.ranges]}")
    t0 = time.time()
    hist = []
    mask = make_mask(a.bs, dev)
    while step < a.steps:
        step += 1
        tg, rf, au, lp = tr.sample(a.bs, min_gap=1 if a.overfit else 25, aug=not a.overfit)
        cond = lp + a.lip_noise * torch.randn_like(lp) if lipmode else au
        if a.overfit:
            tg = tg  # augmentation already mild; overfit test uses same pipeline
        inp = tg * (1 - mask)
        out = g(inp, rf, mask, cond)
        l_low = (low(out) - low(tg)).abs().mean()
        l_up = ((out - tg).abs() * (1 - mask)).mean()
        l_lap = lap_l1(out[:, :, -48:], tg[:, :, -48:])
        loss = 5 * l_low + l_up + a.w_lap * l_lap
        l_sync = torch.zeros((), device=dev)
        if use_sync and step > a.warm:
            v = sn.embed_v(out)
            lg = sn.logits(v, sn.embed_a(au))
            tgt = torch.arange(len(v), device=dev)
            l_sync = F.cross_entropy(lg, tgt) + F.cross_entropy(lg.t(), tgt)
            loss = loss + a.w_sync * l_sync
        l_adv = torch.zeros((), device=dev)
        if a.w_gan > 0 and step > a.warm:
            l_adv = -d(out).mean()
            loss = loss + a.w_gan * l_adv
        og.zero_grad(); loss.backward()
        torch.nn.utils.clip_grad_norm_(g.parameters(), 1.0)
        og.step()
        if a.w_gan > 0 and step > a.warm:  # D update AFTER the G step (in-place weight change would break G's backward graph)
            ld = F.relu(1 - d(tg)).mean() + F.relu(1 + d(out.detach())).mean()
            od.zero_grad(); ld.backward(); od.step()
        with torch.no_grad():
            dec = min(a.ema, (1 + step) / (10 + step))
            for pe, p in zip(ema.parameters(), g.parameters()):
                pe.lerp_(p, 1 - dec)
        if step % 50 == 0:
            print(f"step {step} L1low {l_low.item():.4f} lap {l_lap.item():.4f} sync {l_sync.item():.3f} adv {l_adv.item():.3f} "
                  f"{(time.time()-t0)/60:.1f}min", flush=True)
        if step % a.eval_every == 0 or step == a.steps:
            l1, ps, grid = val_eval(ema, va, cond=a.cond)
            tl1, tps, tgrid = val_eval(ema, tr, cond=a.cond) if step == a.steps or a.overfit else (None, None, None)  # tr.ranges = the 40-frame subset when --overfit
            hist.append(dict(step=step, val_l1_low=l1, val_psnr_low=ps, train_l1_low=tl1, train_psnr_low=tps, minutes=(time.time() - t0) / 60 + t_prev / 60))
            print("EVAL", hist[-1], flush=True)
            import cv2
            cv2.imwrite(str(CK / f"samples_{tag}.png"), to_img_grid(grid))
            CK.mkdir(parents=True, exist_ok=True)
            torch.save(dict(g=g.state_dict(), ema=ema.state_dict(), d=d.state_dict(), og=og.state_dict(), od=od.state_dict(), step=step,
                            mean=tr.mean, std=tr.std, lip_mean=tr.lip_mean, lip_std=tr.lip_std, hist=hist, train_seconds=time.time() - t0 + t_prev, args=vars(a)), path)
    (CK / f"gen_{tag}_hist.json").write_text(json.dumps(hist, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["sync", "gen"])
    ap.add_argument("--steps", type=int, default=6000)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--ema", type=float, default=0.995)
    ap.add_argument("--w-lap", type=float, default=0.5)
    ap.add_argument("--w-sync", type=float, default=0.1)
    ap.add_argument("--w-gan", type=float, default=0.02)
    ap.add_argument("--warm", type=int, default=500)
    ap.add_argument("--eval-every", type=int, default=500)
    ap.add_argument("--tag", default="main")
    ap.add_argument("--cond", choices=["audio", "lip"], default="audio")
    ap.add_argument("--lip-noise", type=float, default=0.15)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--overfit", action="store_true")
    a = ap.parse_args()
    torch.manual_seed(0); np.random.seed(0)
    CK.mkdir(parents=True, exist_ok=True)
    (train_sync if a.stage == "sync" else train_gen)(a)


if __name__ == "__main__":
    main()
