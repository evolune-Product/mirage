"""LoRA fine-tune of SoulX-FlashHead Lite on the owner's own footage (own training loop; FlashHead ships inference only).
Objective: the released Lite model is a 4-step distilled flow model sampled at fixed (shifted) timesteps [1000,750,500,250].
We train exactly there: x_t=(1-t)x0+t*eps, prediction x0hat = x_t - t*v (as the sampler does), loss = MSE(x0hat, x0) on the non-clamped latent frames.
Conditioning matches the sampler: 2 clean motion latent frames clamped, reference-image latent as `y`, wav2vec window embeddings as `context`.
Run: workers/.venv-flash/bin/python lora_trial/train.py [--build-only] [--minutes 90]"""
import argparse, json, random, time, math
from common import *
import lora as L
from torch.utils.checkpoint import checkpoint
from flash_head.src.pipeline.flash_head_pipeline import timestep_transform

ap = argparse.ArgumentParser()
ap.add_argument("--rank", type=int, default=8); ap.add_argument("--alpha", type=float, default=8)
ap.add_argument("--lr", type=float, default=5e-5); ap.add_argument("--accum", type=int, default=4)
ap.add_argument("--updates", type=int, default=100); ap.add_argument("--eval_every", type=int, default=20)
ap.add_argument("--stride", type=int, default=8); ap.add_argument("--minutes", type=float, default=120)
ap.add_argument("--build-only", action="store_true"); ap.add_argument("--no-ckpt", action="store_true")
ap.add_argument("--tag", default="r8"); ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
dev = "mps"; dt = torch.bfloat16
LORA = FH / "lora"; CACHE = DATA / f"cache_s{a.stride}.pt"
pipe = load_pipeline(); model = pipe.model; vae = pipe.vae
FN, MOT = 33, 2

def to_vid(fr):  # (T,512,512,3) uint8 -> (1,3,T,512,512) in [-1,1]
    x = torch.from_numpy(fr).to(dev).permute(3, 0, 1, 2)[None].to(dt)
    return (x / 255 - 0.5) * 2

@torch.no_grad()
def win_audio(wav, s):  # replicate stream-mode window: last 8 s of audio ending at the window's last frame, zero front pad
    end = (s + FN) * 640; seg = wav[max(0, end - 128000):end]
    seg = np.concatenate([np.zeros(128000 - len(seg), np.float32), seg])
    return get_audio_embedding(pipe, seg, 200 - FN, 200).to(dt).cpu()  # (1,33,5,12,768)

@torch.no_grad()
def build():
    import librosa
    t0 = time.time(); C = dict(train=[], val=[], refs=[])
    for name in ("founder_train", "demo_train"):
        fr = np.load(DATA / f"{name}.npy"); wav, _ = librosa.load(str(DATA / f"{name}.wav"), sr=16000, mono=True)
        for s in range(0, len(fr) - FN + 1, a.stride):
            C["train"].append(dict(src=name, s=s, x0=vae.encode(to_vid(fr[s:s + FN])).cpu(), ctx=win_audio(wav, s)))
            if len(C["train"]) % 10 == 0: print("built", len(C["train"]), round(time.time() - t0), "s", flush=True)
        for s in range(15, len(fr) - 1, 60):   # reference pool: single frames spread across the clip, encoded as the pipeline does (repeated)
            C["refs"].append(dict(src=name, s=s, y=vae.encode(to_vid(np.repeat(fr[s:s + 1], FN, 0))).cpu()))
    fr = np.load(DATA / "founder_val.npy"); wav, _ = librosa.load(str(DATA / "heldout.wav"), sr=16000, mono=True)
    ref0 = vae.encode(to_vid(np.repeat(np.load(DATA / "founder_train.npy")[500:501], FN, 0))).cpu()
    for s in range(0, len(fr) - FN + 1, 24):
        C["val"].append(dict(s=s, x0=vae.encode(to_vid(fr[s:s + FN])).cpu(), ctx=win_audio(wav, s), y=ref0))
    torch.save(C, CACHE); print("cache built", {k: len(v) for k, v in C.items()}, round(time.time() - t0), "s"); return C

C = torch.load(CACHE) if CACHE.exists() else build()
print({k: len(v) for k, v in C.items()}, flush=True)
if a.build_only: sys.exit()

torch.manual_seed(a.seed); random.seed(a.seed)
names = L.inject(model, a.rank, a.alpha); P = L.params(model)
ntrain = sum(p.numel() for p in P); print(f"LoRA r={a.rank} on {len(names)} linears, trainable params {ntrain:,} / {sum(p.numel() for p in model.parameters()) - ntrain:,} frozen", flush=True)
if not a.no_ckpt:
    for b in model.blocks:
        f = b.forward; b.forward = (lambda *args, _f=f: checkpoint(_f, *args, use_reentrant=False)) if True else f
opt = torch.optim.AdamW(P, lr=a.lr, weight_decay=0.0, betas=(0.9, 0.99))
TS = [timestep_transform(torch.tensor([t], device=dev), shift=5.0, num_timesteps=1000) for t in (1000., 750., 500., 250.)]

def step_loss(x0, ctx, y, ti, gen=None):
    x0, y, ctx = x0.to(dev, dt), y.to(dev, dt), ctx.to(dev)
    t = TS[ti]; tau = (t / 1000).to(dt)
    eps = torch.randn(x0.shape, device=dev, dtype=dt, generator=gen)
    xt = (1 - tau) * x0 + tau * eps; xt[:, :MOT] = x0[:, :MOT]
    v = model(x=xt[None], timestep=t, context=ctx, y=y[None])[0]
    x0h = xt - v * tau
    return ((x0h[:, MOT:].float() - x0[:, MOT:].float()) ** 2).mean()

@torch.no_grad()
def val(mult):
    L.set_mult(model, mult); g = torch.Generator(device=dev).manual_seed(123); tot = [0] * 4
    for w in C["val"]:
        for ti in range(4): tot[ti] += step_loss(w["x0"], w["ctx"], w["y"], ti, g).item() / len(C["val"])
    L.set_mult(model, 1.0); return [round(x, 5) for x in tot]

log = open(LORA / f"train_{a.tag}.jsonl", "a"); peak = 0; t0 = time.time()
def emit(d): print(json.dumps(d), flush=True); log.write(json.dumps(d) + "\n"); log.flush()
base_v = val(0.0); emit(dict(ev="val_base", val=base_v, mean=round(float(np.mean(base_v)), 5)))
for u in range(1, a.updates + 1):
    # tiny cosine-free schedule: 5-update warmup then constant
    for g_ in opt.param_groups: g_["lr"] = a.lr * min(1, u / 5)
    tl = 0
    for _ in range(a.accum):
        w = random.choice(C["train"]); r = random.choice(C["refs"]); ti = random.randrange(4)
        loss = step_loss(w["x0"], w["ctx"], r["y"], ti) / a.accum; loss.backward(); tl += loss.item()
        peak = max(peak, torch.mps.driver_allocated_memory() / 2**30)
    gn = torch.nn.utils.clip_grad_norm_(P, 1.0).item(); opt.step(); opt.zero_grad(set_to_none=True)
    emit(dict(ev="train", u=u, loss=round(tl, 5), gnorm=round(gn, 3), peak_gb=round(peak, 2), min=round((time.time() - t0) / 60, 1)))
    if u % a.eval_every == 0 or u == a.updates or (time.time() - t0) / 60 > a.minutes:
        v = val(1.0); emit(dict(ev="val", u=u, val=v, mean=round(float(np.mean(v)), 5), base_mean=round(float(np.mean(base_v)), 5)))
        torch.save(dict(r=a.rank, alpha=a.alpha, state=L.state(model), updates=u, lr=a.lr), LORA / f"{a.tag}_u{u}.pt")
        if (time.time() - t0) / 60 > a.minutes: break
emit(dict(ev="done", minutes=round((time.time() - t0) / 60, 1), peak_gb=round(peak, 2), trainable=ntrain))
