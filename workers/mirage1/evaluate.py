"""Video-level evaluation (runs in workers/.venv-face: mediapipe + opencv + numpy only).
Metrics per video: lip-audio correlation (jaw opening vs audio energy, best lag +-6 frames), jaw range, head jitter, face sharpness,
identity cosine vs a reference photo (SFace), face-found rate; optional jaw correlation against a reference (real) video.
usage: python -m mirage1.evaluate --ref-img face.png [--ref-video real.mp4] name=video.mp4 ... > metrics.json"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from facelib import FaceTracker  # noqa: E402

MODELS = Path(__file__).resolve().parents[2] / "models"


def frames_of(path):
    cap = cv2.VideoCapture(path)
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f)
    return out


def audio_env(path, n, fps=25.0):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.float32)
    hop = int(16000 / fps)
    return np.array([np.sqrt((a[i * hop:(i + 1) * hop] ** 2).mean() + 1e-12) for i in range(n)])


def zs(x):
    x = x - np.nanmean(x)
    return x / (np.nanstd(x) + 1e-9)


def best_lag_corr(x, y, lags=range(-6, 7)):
    best, bl = -1, 0
    n = len(x)
    for lag in lags:
        a, b = zs(x[max(0, lag):n + min(0, lag)]), zs(y[max(0, -lag):n - max(0, lag)])
        c = float(np.mean(a * b))
        if c > best:
            best, bl = c, lag
    return best, bl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-img", required=True)
    ap.add_argument("--ref-video")
    ap.add_argument("items", nargs="+")
    a = ap.parse_args()
    tr = FaceTracker()
    det = cv2.FaceDetectorYN.create(str(MODELS / "yunet.onnx"), "", (320, 320))
    rec = cv2.FaceRecognizerSF.create(str(MODELS / "sface.onnx"), "")

    def emb(img):
        h, w = img.shape[:2]
        det.setInputSize((w, h))
        _, f = det.detect(img)
        return None if f is None else rec.feature(rec.alignCrop(img, f[0]))

    ref = emb(cv2.imread(a.ref_img))
    ref_jaw = None
    if a.ref_video:
        ref_jaw = np.array([o.jaw_open if o.ok else np.nan for o in map(tr, frames_of(a.ref_video))])
    res = {}
    for it in a.items:
        name, vid = it.split("=", 1)
        fr = frames_of(vid)
        n = len(fr)
        obs = [tr(f) for f in fr]
        jaw = np.array([o.jaw_open if o.ok else np.nan for o in obs])
        jj = np.nan_to_num(jaw, nan=np.nanmean(jaw))
        env = audio_env(vid, n)
        best, bl = best_lag_corr(jj, env)
        cen = np.array([[o.box[0] + o.box[2] / 2, o.box[1] + o.box[3] / 2] for o in obs if o.ok])
        wd = np.mean([o.box[2] for o in obs if o.ok])
        jit = float(np.mean(np.linalg.norm(np.diff(cen, axis=0), axis=1)) / wd) if len(cen) > 2 else float("nan")
        sharp, sims = [], []
        for f, o in zip(fr[::5], obs[::5]):
            if not o.ok:
                continue
            x, y, w, h = [int(v) for v in o.box]
            c = f[max(0, y):y + h, max(0, x):x + w]
            if c.size == 0:
                continue
            c = cv2.resize(c, (256, 256))
            sharp.append(cv2.Laplacian(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
            e = emb(f)
            if e is not None and ref is not None:
                sims.append(float(rec.match(ref, e, cv2.FaceRecognizerSF_FR_COSINE)))
        r = dict(frames=n, size=f"{fr[0].shape[1]}x{fr[0].shape[0]}", face_found=round(float(np.mean([o.ok for o in obs])), 3),
                 lip_audio_corr=round(best, 3), lag_frames=bl,
                 jaw_range=round(float(np.nanpercentile(jaw, 95) - np.nanpercentile(jaw, 5)), 3), head_jitter=round(jit, 4),
                 sharpness=round(float(np.mean(sharp)), 1) if sharp else None,
                 identity_cos=round(float(np.mean(sims)), 3) if sims else None)
        if ref_jaw is not None:
            m = min(len(ref_jaw), n)
            ok = ~np.isnan(ref_jaw[:m]) & ~np.isnan(jaw[:m])
            r["jaw_corr_vs_real"] = round(float(np.corrcoef(ref_jaw[:m][ok], jaw[:m][ok])[0, 1]), 3)
        res[name] = r
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
