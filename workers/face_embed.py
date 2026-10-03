"""Face embeddings + passive liveness heuristics for consent binding. Runs in workers/.venv-face (OpenCV 4.11).

  .venv-face/bin/python face_embed.py --media FILE [--fps 5] [--max-frames 40] [--start 0] [--duration 60]

Detector: YuNet (models/yunet.onnx, MIT, OpenCV Zoo). Recogniser: SFace (models/sface.onnx, Apache-2.0, OpenCV Zoo,
cv2.FaceRecognizerSF, 128-d, cosine). Both permit commercial use (the InsightFace buffalo weights do not, so they are NOT used).
Images and videos are both accepted (ffmpeg samples frames). Nothing is written to disk besides a temp dir that is removed.

Last stdout line: JSON {"ok", "error"?, "n_decoded", "n_face_frames", "multi_face_frames", "frames": [{"t","emb","box","score"}],
"liveness": {...}}. Liveness is a PASSIVE HEURISTIC (non-rigid landmark motion, mouth movement, texture change), not certified.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

MODELS = Path(__file__).resolve().parents[1] / "models"
MIN_FACE_PX = 60


def models() -> tuple[Path, Path]:
    y, s = MODELS / "yunet.onnx", MODELS / "sface.onnx"
    if not y.exists() or not s.exists():
        raise FileNotFoundError(f"face models missing in {MODELS} (yunet.onnx, sface.onnx)")
    return y, s


def decode_frames(media: Path, tmp: Path, fps: float, max_frames: int, start: float, duration: float) -> list[Path]:
    """ffmpeg -> PNG frames (works for webm/mp4/mov and for still images)."""
    cmd = ["ffmpeg", "-loglevel", "error", "-y"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(media)]
    if duration:
        cmd += ["-t", str(duration)]
    is_img = media.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp")
    vf = "scale='min(960,iw)':-2" if is_img else f"fps={fps},scale='min(960,iw)':-2"
    cmd += ["-an", "-vf", vf, "-frames:v", str(max_frames), str(tmp / "f_%04d.png")]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        raise ValueError("could not decode media: " + r.stderr.decode(errors="ignore")[-200:])
    return sorted(tmp.glob("f_*.png"))


def analyse(paths: list[Path], fps: float) -> dict:
    ypath, spath = models()
    rec = cv2.FaceRecognizerSF.create(str(spath), "")
    frames, lms, crops, patches, multi = [], [], [], [], 0
    det, dsize = None, None
    for i, p in enumerate(paths):
        img = cv2.imread(str(p))
        if img is None:
            continue
        h, w = img.shape[:2]
        if det is None:
            det = cv2.FaceDetectorYN.create(str(ypath), "", (w, h), 0.8, 0.3, 5000)
        if (w, h) != dsize:
            det.setInputSize((w, h)); dsize = (w, h)
        _, faces = det.detect(img)
        if faces is None or len(faces) == 0:
            continue
        faces = [f for f in faces if min(f[2], f[3]) >= MIN_FACE_PX]
        if not faces:
            continue
        if len(faces) > 1:
            multi += 1
            # only a clearly dominant face is accepted (bystanders in frame are flagged separately)
            faces = sorted(faces, key=lambda f: -f[2] * f[3])
            if faces[1][2] * faces[1][3] > 0.25 * faces[0][2] * faces[0][3]:
                continue
        f = faces[0]
        al = rec.alignCrop(img, f)
        emb = rec.feature(al)[0]
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        frames.append({"t": round(i / fps, 3), "emb": [round(float(x), 5) for x in emb],
                       "box": [int(f[0]), int(f[1]), int(f[2]), int(f[3])], "score": round(float(f[14]), 3)})
        lms.append(np.array(f[4:14], dtype=np.float32).reshape(5, 2))
        crops.append(cv2.cvtColor(al, cv2.COLOR_BGR2GRAY).astype(np.float32))
        patches.append(face_patch(img, f))
    return {"frames": frames, "lms": lms, "crops": crops, "patches": patches, "multi": multi}


def face_patch(img, f, size: int = 96) -> np.ndarray:
    """Square grayscale patch around the detected face (box x1.5), resized, lightly blurred, zero-mean/unit-std."""
    h, w = img.shape[:2]
    cx, cy, s = f[0] + f[2] / 2, f[1] + f[3] / 2, max(f[2], f[3]) * 1.5 / 2
    x0, y0, x1, y1 = int(max(0, cx - s)), int(max(0, cy - s)), int(min(w, cx + s)), int(min(h, cy + s))
    g = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(cv2.resize(g, (size, size), interpolation=cv2.INTER_AREA), (3, 3), 0).astype(np.float32)
    return (g - g.mean()) / (g.std() + 1e-6)


def registered_residual(patches) -> float:
    """Median over consecutive frame pairs of the mouth/chin appearance change AFTER registering the two face patches
    with a homography (ECC). A photo or screen moved by hand/tilted is explained by the homography (residual ~ noise);
    a talking face changes locally (lips, teeth, jaw) and is not. Unit: mean |diff| in std units of the patch."""
    out = []
    n = patches[0].shape[0]
    r0, r1, c0, c1 = int(n * 0.58), int(n * 0.95), int(n * 0.22), int(n * 0.78)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5)
    for a, b in zip(patches[:-1], patches[1:]):
        W = np.eye(3, dtype=np.float32)
        try:
            _, W = cv2.findTransformECC(a, b, W, cv2.MOTION_HOMOGRAPHY, crit, None, 5)
            bw = cv2.warpPerspective(b, W, (n, n), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
        except cv2.error:
            bw = b
        out.append(float(np.abs(a[r0:r1, c0:c1] - bw[r0:r1, c0:c1]).mean()))
    return float(np.median(out)) if out else 0.0


def liveness(frames, lms, crops, n_decoded: int, patches=None) -> dict:
    """Passive heuristics on a selfie clip. All numbers are unitless ratios, thresholds live in the backend."""
    out = {"face_ratio": round(len(frames) / max(1, n_decoded), 3), "nonrigid": 0.0, "mouth_motion": 0.0,
           "texture_motion": 0.0, "head_motion": 0.0, "mouth_residual": 0.0, "frames_used": len(frames)}
    if len(frames) < 4:
        return out
    L = np.stack(lms)  # N x 5 x 2: re, le, nose, mouth-right, mouth-left
    iod = np.linalg.norm(L[:, 0] - L[:, 1], axis=1)
    iod = np.maximum(iod, 1.0)
    # similarity-normalise each frame onto the mean shape (removes translation, scale, in-plane rotation)
    ref = L[0] - L[0].mean(0)
    ref = ref / np.linalg.norm(ref)
    al = []
    for k in range(len(L)):
        P = L[k] - L[k].mean(0)
        P = P / (np.linalg.norm(P) + 1e-9)
        U, _, Vt = np.linalg.svd(P.T @ ref)
        al.append(P @ (U @ Vt))
    al = np.stack(al)
    mean_shape = al.mean(0)
    resid = np.linalg.norm(al - mean_shape, axis=2).mean(1)  # per frame, normalised shape units
    out["nonrigid"] = round(float(resid.mean() * 100), 3)
    mw = np.linalg.norm(L[:, 3] - L[:, 4], axis=1) / iod  # mouth width / inter-ocular
    mh = np.abs(L[:, 2, 1] - (L[:, 3, 1] + L[:, 4, 1]) / 2) / iod  # nose -> mouth vertical (jaw drop proxy)
    out["mouth_motion"] = round(float((mw.std() + mh.std()) * 100), 3)
    centers = np.array([[f["box"][0] + f["box"][2] / 2, f["box"][1] + f["box"][3] / 2] for f in frames])
    wid = np.array([f["box"][2] for f in frames], dtype=np.float32)
    out["head_motion"] = round(float(centers.std(0).mean() / wid.mean() * 100), 3)
    # texture change in the lower half of the aligned face (speech changes mouth pixels, a held photo does not),
    # measured after a median blur so sensor noise does not count
    lows = [cv2.GaussianBlur(c[70:, :], (5, 5), 0) for c in crops]
    d = [float(np.abs(lows[i + 1] - lows[i]).mean()) for i in range(len(lows) - 1)]
    out["texture_motion"] = round(float(np.mean(d)), 3)
    if patches:
        out["mouth_residual"] = round(registered_residual(patches), 3)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--media", required=True)
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--max-frames", type=int, default=40)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=0.0)
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="faceembed_"))
    try:
        paths = decode_frames(Path(a.media), tmp, a.fps, a.max_frames, a.start, a.duration)
        if not paths:
            print(json.dumps({"ok": False, "error": "no frames could be read"}))
            return 0
        r = analyse(paths, a.fps)
        res = {"ok": True, "n_decoded": len(paths), "n_face_frames": len(r["frames"]), "multi_face_frames": r["multi"],
               "frames": r["frames"], "liveness": liveness(r["frames"], r["lms"], r["crops"], len(paths), r["patches"])}
        print(json.dumps(res))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(e)[:300]}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
