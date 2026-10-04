"""Data prep: video(s) -> aligned 128px face crops + 25 fps wav2vec2 features + time-based train/val split.
usage: python -m mirage1.data_prep --out data/mirage1
Training data is ONLY the owner's own consented footage (see docs/MIRAGE1.md)."""
import argparse
import json
import sys
from pathlib import Path

import subprocess
import tempfile

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from facelib import load_frames  # noqa: E402
from mirage1 import align, audiofeat  # noqa: E402

HOME = Path.home()
CLIPS = {
    "founder": HOME / "Desktop/SpendVeto Video and PPT/SpendVeto_Founder_Video_1min.mp4",
    "demo": HOME / "Desktop/Mirage/demo_assets/demo_face_v2.mp4",
}
VAL_FRAC = {"founder": 0.12, "demo": 0.15}
GAP = 25  # frames dropped between train and val so neighbouring frames cannot leak


FACE_PY = Path(__file__).resolve().parents[1] / ".venv-face/bin/python"


def landmarks(path):
    with tempfile.TemporaryDirectory() as td:
        o = Path(td) / "p.npy"
        subprocess.run([str(FACE_PY), "-m", "mirage1.track", str(path), str(o)], cwd=Path(__file__).resolve().parents[1],
                       check=True, capture_output=True)
        return np.load(o)


def prep(name, path, out, device):
    frames = load_frames(path, 25.0)
    wav = audiofeat.read_wav16k(path)
    feats = audiofeat.features(wav, device=device)
    n = min(len(frames), len(feats))
    frames, feats = frames[:n], feats[:n]
    pts, ok = align.smooth_points(landmarks(path)[:n])
    if not (out / "template.npy").exists():  # template is built once, from the first (founder) clip
        tpl = align.build_template(pts[ok])
        np.save(out / "template.npy", tpl)
        align.TEMPLATE_PATH.write_text(json.dumps({"template": tpl.tolist(), "S": align.S, "stable": align.STABLE}))
    tpl = align.load_template()
    Ms = np.stack([align.fit(p, tpl) for p in pts]).astype(np.float32)
    crops = np.stack([align.warp_crop(f, M) for f, M in zip(frames, Ms)])
    nv = int(n * VAL_FRAC[name])
    split = {"train": [0, n - nv - GAP], "val": [n - nv, n]}
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "crops.npy", crops)  # (N,S,S,3) uint8 BGR
    np.save(d / "feats.npy", feats.astype(np.float16))
    np.save(d / "M.npy", Ms)
    meta = dict(source=str(path), frames=n, fps=25, seconds=round(n / 25, 2), found_rate=float(ok.mean()), split=split, frame_wh=list(frames[0].shape[1::-1]))
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    print(name, meta)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/mirage1")
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, p in CLIPS.items():
        prep(name, p, out, a.device)


if __name__ == "__main__":
    main()
