"""Render a talking clip with Mirage-1 (our own audio-driven lower-face generator): face image (or base clip) + speech wav -> mp4.

Same CLI and JSON result as render_flashhead.py.
usage: render_mirage1.py --image face.png|base.mp4 --audio speech.wav --out out.mp4 [--ckpt data/mirage1/ckpt/gen_main.pt]
Runs with workers/.venv-flash (torch). Landmarks come from workers/.venv-face (mediapipe). Head pose is NOT generated: the
input pose is kept (an image gives a static head, a base clip loops). Only the region below the nose is synthesised."""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from mirage1 import align, audiofeat  # noqa: E402
from mirage1.model import WIN, Generator, make_mask  # noqa: E402
from mirage1.data import LIP_NAMES, LIP_IDX  # noqa: E402

FACE_PY = HERE / ".venv-face/bin/python"
DEFAULT_CKPT = HERE.parent / "data/mirage1/ckpt/gen_main.pt"


def load_gen(ckpt, dev):
    ck = torch.load(ckpt, map_location="cpu")
    lipmode = ck.get("args", {}).get("cond") == "lip"
    g = Generator(lip_dim=len(LIP_NAMES) if lipmode else 0)
    g.load_state_dict(ck["ema"])
    return g.to(dev).eval(), ck["mean"], ck["std"], lipmode, ck


def landmarks(path):
    with tempfile.TemporaryDirectory() as td:
        o = Path(td) / "p.npy"
        subprocess.run([str(FACE_PY), "-m", "mirage1.track", str(path), str(o)], cwd=HERE, check=True, capture_output=True)
        return np.load(o)


@torch.no_grad()
def generate_crops(g, crops, ref_crop, cond, dev, bs=32):
    """crops: (N,S,S,3) BGR uint8 aligned; ref_crop (S,S,3); cond: either (N,768) normalised audio feats (audio generator, windowed here)
    or (N,28) standardised lip state (lip generator). Returns generated crops (N,S,S,3) uint8 BGR."""
    N = len(crops)
    pad = WIN // 2
    fp = torch.from_numpy(cond).float().to(dev)
    o = torch.arange(-pad, pad + 1, device=dev)
    lipmode = cond.shape[1] != 768
    rgb = lambda a: torch.from_numpy(a[..., ::-1].copy()).permute(0, 3, 1, 2).float().to(dev) / 255
    ref = rgb(ref_crop[None])
    out = []
    for s in range(0, N, bs):
        idx = torch.arange(s, min(N, s + bs), device=dev)
        win = fp[idx] if lipmode else fp[(idx[:, None] + o[None]).clamp(0, len(fp) - 1)]
        tg = rgb(crops[s:s + len(idx)])
        m = make_mask(len(idx), dev)
        y = g(tg * (1 - m), ref.expand(len(idx), -1, -1, -1), m, win)
        out.append((y.permute(0, 2, 3, 1).cpu().numpy()[..., ::-1] * 255).clip(0, 255).astype(np.uint8))
    return np.concatenate(out)


def smooth_time(x, k=(0.2, 0.6, 0.2)):
    y = x.astype(np.float32)
    p = np.concatenate([y[:1], y, y[-1:]])
    return (k[0] * p[:-2] + k[1] * p[1:-1] + k[2] * p[2:]).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=25.0)  # fixed 25 fps; accepted for CLI compatibility
    ap.add_argument("--ckpt", default=str(DEFAULT_CKPT))
    ap.add_argument("--no-smooth", action="store_true")
    ap.add_argument("--lip-gain", type=float, default=2.5, help="lip generator: scale of predicted lip state (ridge output has ~0.36x the std of real lip state)")
    ap.add_argument("--lip-npy", help="evaluation only: raw (N,52) blendshape file used instead of the audio->lip prediction (oracle)")
    ap.add_argument("--max-side", type=int, default=720)
    a = ap.parse_args()
    t0 = time.time()
    if not Path(a.ckpt).exists():
        print(json.dumps({"ok": False, "error": f"mirage1 checkpoint missing: {a.ckpt}"}))
        return 1
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    g, mean, std, lipmode, ck = load_gen(a.ckpt, dev)
    wav = audiofeat.read_wav16k(a.audio)
    feats = (audiofeat.features(wav, device=dev) - mean.numpy()) / std.numpy()
    N = len(feats)
    cond = feats
    if lipmode:
        from mirage1.lip import Ridge
        from facelib import smooth_series
        if a.lip_npy:
            raw = np.nan_to_num(np.load(a.lip_npy)[:, LIP_IDX])[:N]
            cond = (raw - ck["lip_mean"].numpy()) / ck["lip_std"].numpy()
            N = len(cond)
        else:
            cond = Ridge.load(Path(a.ckpt).with_name("lipridge.npz")).predict_wav(wav)[:N] * a.lip_gain
            cond = smooth_series(cond, 1.0).astype(np.float32)
    is_img = Path(a.image).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    if is_img:
        base = [cv2.imread(a.image)]
    else:
        sys.path.insert(0, str(HERE))
        from facelib import load_frames
        base = load_frames(a.image, 25.0)
    sc = min(1.0, a.max_side / max(base[0].shape[:2]))
    raw = landmarks(a.image)
    if np.isnan(raw[:, 0, 0]).all():
        print(json.dumps({"ok": False, "error": "no face found in input"}))
        return 1
    pts, _ = align.smooth_points(raw)
    if sc < 1:
        base = [cv2.resize(f, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA) for f in base]
        pts = pts * sc
    tpl = align.load_template()
    Ms = np.stack([align.fit(p, tpl) for p in pts])
    # frame i of the output uses base frame pingpong(i)
    nb = len(base)
    order = np.clip((nb - 1) - np.abs((np.arange(N) % (2 * (nb - 1))) - (nb - 1)), 0, nb - 1) if nb > 1 else np.zeros(N, int)
    crops_b = np.stack([align.warp_crop(f, M) for f, M in zip(base, Ms)])
    crops = crops_b[order]
    gen = generate_crops(g, crops, crops_b[0], cond, dev)
    if not a.no_smooth:
        gen = smooth_time(gen)
    pm = align.paste_mask()
    h, w = base[0].shape[:2]
    h2, w2 = h - h % 2, w - w % 2
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "v.mp4"
        vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (w2, h2))
        for i in range(N):
            fr = align.paste_back(base[order[i]], gen[i], Ms[order[i]], pm)
            vw.write(fr[:h2, :w2])
        vw.release()
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-i", a.audio, "-map", "0:v", "-map", "1:a",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "aac", "-shortest", a.out],
                           capture_output=True, text=True)
    if r.returncode or not Path(a.out).exists():
        print(json.dumps({"ok": False, "error": r.stderr[-500:]}))
        return 1
    print(json.dumps({"ok": True, "seconds": round(time.time() - t0, 1), "engine": "mirage1", "frames": N}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
