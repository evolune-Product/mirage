"""Photo -> short idle clip (closed mouth, blinking, subtle head motion) with LivePortrait. Runs in workers/.venv.

  .venv/bin/python photo_idle.py --image photo.jpg --out idle.mp4 [--seconds 4] [--head 1.0] [--blink 1.0] [--keep-work DIR]

How the mouth stays closed: LivePortrait copies RELATIVE motion from a driving template onto the photo. We take a shipped calm
driving template (d0.pkl, real person: blinks + tiny head motion), freeze the 6 lip keypoints at their frame-0 value (so the
delta is exactly zero -> the photo's own mouth is kept) and scale the remaining motion. The result is returned as a ping-pong-able
clip: the live lip-sync service (Wav2Lip) loops it forwards/backwards, so the template does not need to loop by itself.

Cost: ~2 s/frame on an M1 Pro (MPS), so 4 s at 25 fps = 100 frames = a few minutes, ONE TIME per replica. We render at
12.5 fps equivalent by dropping to every 2nd template frame and then let ffmpeg interpolate to 25 fps (--fast, default).

Licence note (also in docs/overnight/creative.md): LivePortrait code is MIT, but its face detector uses InsightFace buffalo_l weights
(non-commercial research licence). Commercial use needs the detector swapped (e.g. a MediaPipe/own detector) or an InsightFace licence."""
from __future__ import annotations

import argparse
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
LP = HERE / "LivePortrait"
DRIVING = LP / "assets/examples/driving/d0.pkl"
MOUTH_IDX = [6, 12, 14, 17, 19, 20]  # lip keypoints (same as audio_driver.py)
LOOP_FPS = 25


def make_idle_template(out_pkl: str | Path, seconds: float = 4.0, head: float = 1.0, blink: float = 1.0,
                       base_pkl: str | Path = DRIVING, step: int = 1, blink_at: tuple[float, ...] = (1.3,)) -> dict:
    """Synthetic calm template: neutral face (d0 frame 58) + slow sinusoidal head sway + one real blink replayed from d0
    (frames 62-72) + lips frozen. `step`=2 renders every 2nd frame (half the LivePortrait cost; ffmpeg re-interpolates)."""
    from scipy.spatial.transform import Rotation as Rot

    base = pickle.load(open(base_pkl, "rb"))
    mot = base["motion"]
    ref = mot[58]
    eye_idx = [i for i in range(21) if i not in MOUTH_IDX]
    n = max(8, int(seconds * LOOP_FPS / step))
    dt = step / LOOP_FPS
    out = []
    for i in range(n):
        t = i * dt
        # gentle sway: three incommensurate sinusoids (deg), starts exactly at the neutral pose (frame 0 is the reference)
        yaw = head * 2.4 * np.sin(2 * np.pi * t / 5.3)
        pitch = head * 1.3 * np.sin(2 * np.pi * t / 3.7 + 0.6) - head * 1.3 * np.sin(0.6)
        roll = head * 0.9 * np.sin(2 * np.pi * t / 7.1)
        R = (Rot.from_euler("xyz", [pitch, yaw, roll], degrees=True).as_matrix() @ ref["R"][0])[None].astype(np.float32)
        shift = np.array([[0.004 * np.sin(2 * np.pi * t / 6.1), 0.003 * np.sin(2 * np.pi * t / 4.3), 0.0]], np.float32) * head
        e = ref["exp"].copy()
        for b0 in blink_at:  # d0 frame 62..72 is one natural blink (peak 66)
            k = int(round((t - b0) * LOOP_FPS)) + 62
            if 62 <= k <= 72:
                e[0, eye_idx] = ref["exp"][0, eye_idx] + blink * (mot[k]["exp"][0, eye_idx] - ref["exp"][0, eye_idx])
        e[0, MOUTH_IDX] = ref["exp"][0, MOUTH_IDX]  # lips frozen -> zero relative delta -> the photo's own closed mouth
        out.append({"scale": ref["scale"], "R": R, "exp": e.astype(np.float32), "t": (ref["t"] + shift).astype(np.float32)})
    tpl = {"n_frames": n, "output_fps": int(round(LOOP_FPS / step)), "motion": out,
           "c_eyes_lst": [np.zeros((1, 2), np.float32)] * n, "c_lip_lst": [np.zeros((1, 1), np.float32)] * n}
    pickle.dump(tpl, open(out_pkl, "wb"))
    return {"frames": n, "fps": LOOP_FPS / step}


def normalize_photo(src: Path, dst: Path, max_dim: int = 1024) -> tuple[int, int]:
    """Load any image, apply EXIF orientation, cap size, make dims even, save PNG."""
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    w, h = im.size
    k = min(1.0, max_dim / max(w, h))
    w, h = int(w * k) // 2 * 2, int(h * k) // 2 * 2
    im.resize((w, h), Image.LANCZOS).save(dst)
    return w, h


def read_and_repair(path: Path) -> tuple[list, int]:
    """Decode the LivePortrait output and replace glitched frames (fp16 on MPS occasionally yields one garbage frame) with the
    average of their neighbours. A frame is bad when it differs from BOTH neighbours by far more than the typical step."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    if len(frames) < 3:
        return frames, 0
    d = np.array([np.abs(frames[i].astype(np.int16) - frames[i + 1]).mean() for i in range(len(frames) - 1)])
    thr = max(6.0, 5 * float(np.median(d)))
    bad = 0
    for i in range(len(frames)):
        dp = d[i - 1] if i > 0 else None
        dn = d[i] if i < len(d) else None
        if all(x is None or x > thr for x in (dp, dn)):
            j0, j1 = max(0, i - 1), min(len(frames) - 1, i + 1)
            # neighbours may themselves be bad runs of 2: walk outwards to the nearest frames that are good
            while j0 > 0 and (d[j0 - 1] > thr):
                j0 -= 1
            while j1 < len(frames) - 1 and (d[j1] > thr):
                j1 += 1
            a, b = frames[j0], frames[j1]
            frames[i] = ((a.astype(np.uint16) + b) // 2).astype(np.uint8) if j0 != i and j1 != i else (a if j0 != i else b)
            bad += 1
    return frames, bad


def animate(image: Path, out_mp4: Path, seconds: float = 4.0, head: float = 1.0, blink: float = 1.0, fast: bool = True,
            keep_work: Path | None = None, python: str | None = None) -> dict:
    t0 = time.time()
    work = Path(keep_work) if keep_work else Path(tempfile.mkdtemp(prefix="idle_"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        photo = work / "photo.png"
        w, h = normalize_photo(image, photo)
        step = 2 if fast else 1
        info = make_idle_template(work / "idle.pkl", seconds, head, blink, step=step)
        env = dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK="1")
        r = subprocess.run([python or sys.executable, "inference.py", "-s", str(photo), "-d", str(work / "idle.pkl"), "-o", str(work),
                            "--no-flag-use-half-precision"],  # fp16 on MPS sometimes yields a garbage frame and is not faster
                           cwd=LP, env=env, capture_output=True, text=True)
        vids = [v for v in work.glob("*.mp4") if not v.name.endswith("_concat.mp4") and v.name != "idle_raw.mp4"]
        if r.returncode != 0 or not vids:
            return {"ok": False, "error": (r.stderr or r.stdout)[-800:]}
        t_lp = time.time() - t0
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        frames, nbad = read_and_repair(vids[0])
        if len(frames) < 8:
            return {"ok": False, "error": "LivePortrait produced too few frames"}
        if step > 1:  # re-time to 25 fps by blending neighbours (what ffmpeg minterpolate=blend would do)
            exp = []
            for i, f in enumerate(frames):
                exp.append(f)
                if i + 1 < len(frames):
                    exp.append(((f.astype(np.uint16) + frames[i + 1]) // 2).astype(np.uint8))
            frames = exp
        h, w = frames[0].shape[:2]
        enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                                "-r", str(LOOP_FPS), "-i", "-", "-an", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", str(out_mp4)],
                               stdin=subprocess.PIPE)
        for f in frames:
            enc.stdin.write(np.ascontiguousarray(f).tobytes())
        enc.stdin.close()
        if enc.wait() != 0:
            return {"ok": False, "error": "encode failed"}
        return {"ok": True, "seconds": round(time.time() - t0, 1), "liveportrait_s": round(t_lp, 1), "frames_rendered": info["frames"],
                "w": w, "h": h, "repaired_frames": nbad, "out_frames": len(frames), "s_per_frame": round(t_lp / info["frames"], 2), "fast": fast}
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)


def main():
    from engines import CommercialOnlyError, require_commercial_safe
    try:
        require_commercial_safe('liveportrait')
    except CommercialOnlyError as e:
        sys.exit(f'refused: {e}')
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--head", type=float, default=1.0)
    ap.add_argument("--blink", type=float, default=1.0)
    ap.add_argument("--full", action="store_true", help="render every template frame (2x slower, smoother)")
    ap.add_argument("--keep-work")
    a = ap.parse_args()
    res = animate(Path(a.image), Path(a.out), a.seconds, a.head, a.blink, fast=not a.full,
                  keep_work=Path(a.keep_work) if a.keep_work else None)
    print(json.dumps(res))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
