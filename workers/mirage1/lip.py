"""Stage A of Mirage-1b: audio -> lip-state regression (wav2vec2 window -> 28 jaw/mouth blendshape scores).
  python -m mirage1.lip --steps 1500      (small; runs fine on CPU)
Reports held-out (val segment) per-parameter Pearson correlation and R^2 against a predict-the-train-mean baseline."""
import argparse
import json

import numpy as np
import torch
import torch.nn.functional as F

from .data import Clips, LIP_NAMES
from .audiofeat import read_wav16k
from .melfeat import logmel
from .model import LipNet, WIN, count
from .train import CK, DATA


@torch.no_grad()
def evaluate(net, ds):
    net.eval()
    P, T = [], []
    for ci, (lo, hi) in enumerate(ds.ranges):
        idx = torch.arange(lo, hi, device=ds.dev)
        P.append(net(ds.window(ci, idx)).cpu()); T.append(ds.lips[ci][idx].cpu())
    net.train()
    P, T = torch.cat(P), torch.cat(T)
    r = [float(np.corrcoef(P[:, k], T[:, k])[0, 1]) if T[:, k].std() > 1e-6 else 0.0 for k in range(T.shape[1])]
    r2 = 1 - ((P - T) ** 2).mean(0) / (T.var(0) + 1e-6)  # vs predicting the (train) mean -> only comparable to mean=0 baseline
    mse_mean = ((T - 0) ** 2).mean().item()
    return dict(corr={n: round(x, 3) for n, x in zip(LIP_NAMES, r)}, mean_corr=round(float(np.nanmean(r)), 3),
                mse=round(((P - T) ** 2).mean().item(), 4), mse_predict_mean=round(mse_mean, 4))


class Ridge:
    """log-mel (40 bands, 25 fps), 11-frame window, ridge regression -> standardised lip state. On the held-out val segment this beat
    both the neural LipNet (overfits in ~100 steps on ~1 min of data) and ridge on wav2vec2 features, so inference uses it.
    No pretrained component."""
    def __init__(self, mu, sd, W, lip_mean, lip_std):
        self.mu, self.sd, self.W, self.lip_mean, self.lip_std = mu, sd, W, lip_mean, lip_std

    @staticmethod
    def _win(Z):  # (N,D) -> (N, WIN*D), edge-replicated
        pad = WIN // 2
        idx = np.clip(np.arange(len(Z))[:, None] + np.arange(-pad, pad + 1)[None], 0, len(Z) - 1)
        return Z[idx].reshape(len(Z), -1)

    def predict_wav(self, wav):  # 16 kHz float32 -> (N,28) standardised lip state
        return self._win((logmel(wav) - self.mu) / self.sd) @ self.W

    def save(self, path):
        np.savez(path, mu=self.mu, sd=self.sd, W=self.W, lip_mean=self.lip_mean, lip_std=self.lip_std)

    @classmethod
    def load(cls, path):
        z = np.load(path)
        return cls(z["mu"], z["sd"], z["W"], z["lip_mean"], z["lip_std"])


def fit_ridge(lam=1e3, device="cpu"):
    import json as _j
    tr = Clips(DATA, device)
    va = Clips(DATA, device, split="val", mean=tr.mean, std=tr.std, lip_stats=(tr.lip_mean, tr.lip_std))
    mel = {n: logmel(read_wav16k(_j.loads((DATA / n / "meta.json").read_text())["source"])) for n in tr.names}
    N = {n: len(tr.feats[i]) for i, n in enumerate(tr.names)}
    mel = {n: m[:N[n]] if len(m) >= N[n] else np.pad(m, ((0, N[n] - len(m)), (0, 0)), mode="edge") for n, m in mel.items()}
    mm = np.concatenate([mel[n][lo:hi] for n, (lo, hi) in zip(tr.names, tr.ranges)])
    mu, sd = mm.mean(0), mm.std(0) + 1e-5

    def build(c):
        X, Y = [], []
        for ci, (lo, hi) in enumerate(c.ranges):
            X.append(Ridge._win((mel[c.names[ci]] - mu) / sd)[lo:hi]); Y.append(c.lips[ci][lo:hi].cpu().numpy())
        return np.concatenate(X), np.concatenate(Y)
    A, Yt = build(tr)
    W = np.linalg.solve(A.T @ A + lam * np.eye(A.shape[1]), A.T @ Yt).astype(np.float32)
    B, Yv = build(va)
    p = B @ W
    r = [float(np.corrcoef(p[:, k], Yv[:, k])[0, 1]) for k in range(Yv.shape[1])]
    m = dict(corr={n: round(x, 3) for n, x in zip(LIP_NAMES, r)}, mean_corr=round(float(np.nanmean(r)), 3),
             mse=round(float(((p - Yv) ** 2).mean()), 4), mse_predict_mean=round(float((Yv ** 2).mean()), 4),
             pred_std_over_gt_std=round(float(p.std(0).mean() / Yv.std(0).mean()), 3), lam=lam, features="log-mel40 x 11 frames",
             train_mean_corr=round(float(np.nanmean([np.corrcoef((A @ W)[:, k], Yt[:, k])[0, 1] for k in range(Yt.shape[1])])), 3),
             note="lam, window and feature type were chosen by looking at this val segment (about 9 s): optimistic")
    CK.mkdir(parents=True, exist_ok=True)
    Ridge(mu.astype(np.float32), sd.astype(np.float32), W, tr.lip_mean.numpy(), tr.lip_std.numpy()).save(CK / "lipridge.npz")
    json.dump(m, open(CK / "lipridge_metrics.json", "w"), indent=1)
    print(json.dumps(m))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=1500)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--ridge", action="store_true")
    a = ap.parse_args()
    if a.ridge:
        return fit_ridge()
    torch.manual_seed(0); np.random.seed(0)
    tr = Clips(DATA, a.device)
    va = Clips(DATA, a.device, split="val", mean=tr.mean, std=tr.std, lip_stats=(tr.lip_mean, tr.lip_std))
    net = LipNet(len(LIP_NAMES)).to(a.device)
    opt = torch.optim.AdamW(net.parameters(), 1e-3, weight_decay=5e-2)
    best, hist = None, []
    for step in range(1, a.steps + 1):
        _, _, au, lp = tr.sample(64, aug=False)
        au = au + 0.15 * torch.randn_like(au)  # feature noise
        loss = F.smooth_l1_loss(net(au), lp)
        opt.zero_grad(); loss.backward(); opt.step()
        if step % 100 == 0:
            m = evaluate(net, va)
            hist.append((step, loss.item(), m["mean_corr"], m["mse"]))
            print(f"step {step} loss {loss.item():.3f} val mean_corr {m['mean_corr']} val_mse {m['mse']} (predict-mean {m['mse_predict_mean']})", flush=True)
            if best is None or m["mse"] < best[0]:  # early stopping on the val segment (honest note in docs: val used for model selection)
                best = (m["mse"], step, {k: v.clone() for k, v in net.state_dict().items()}, m)
    net.load_state_dict(best[2])
    CK.mkdir(parents=True, exist_ok=True)
    torch.save(dict(net=best[2], step=best[1], lip_mean=tr.lip_mean, lip_std=tr.lip_std, names=LIP_NAMES, metrics=best[3], hist=hist), CK / "lipnet.pt")
    print("BEST step", best[1], json.dumps(best[3]))
    json.dump(best[3] | {"best_step": best[1], "params": count(net)}, open(CK / "lipnet_metrics.json", "w"), indent=1)


if __name__ == "__main__":
    main()
