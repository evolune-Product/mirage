"""Validate a portrait photo for photo-avatar creation and write a normalised copy + face.png. Runs in workers/.venv-face.

  .venv-face/bin/python photo_check.py --image in.jpg --out-dir DIR

Writes DIR/photo.png (EXIF-rotated, <=1024 px, even dims) and DIR/face.png (square face crop, like extract_face.py does for video).
Last stdout line is JSON: {"ok": bool, "error"?: str, "warnings": [...], "face_px", "jaw_open", "blink", "yaw"...}.

Hard errors (the avatar would look wrong): no face, more than one face, face narrower than 90 px, mouth clearly open (the
animation freezes the lips, so an open mouth in the photo stays open and Wav2Lip then draws on top of it), eyes closed.
Warnings: head turned, face small, soft/blurry, dark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MIN_FACE_PX = 90
JAW_ERR, JAW_WARN = 0.15, 0.06
BLINK_ERR = 0.45


def check(image: Path, out_dir: Path) -> dict:
    from PIL import Image, ImageOps

    import facelib as fl

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        im = ImageOps.exif_transpose(Image.open(image)).convert("RGB")
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "could not read the photo (use JPEG or PNG)"}
    w, h = im.size
    if min(w, h) < 256:
        return {"ok": False, "error": f"photo is too small ({w}x{h}); use at least 512 px on the short side"}
    k = min(1.0, 1024 / max(w, h))
    w, h = int(w * k) // 2 * 2, int(h * k) // 2 * 2
    im = im.resize((w, h), Image.LANCZOS)
    photo = out_dir / "photo.png"
    im.save(photo)
    bgr = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)

    warnings: list[str] = []
    import mediapipe as mp

    det = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
    res = det.process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    n = len(res.detections or [])
    if n == 0:
        return {"ok": False, "error": "no face found in the photo (use a clear, front-facing portrait)"}
    if n > 1:
        return {"ok": False, "error": f"{n} faces found; use a photo with exactly one person"}
    obs = fl.FaceTracker()(bgr)
    if not obs.ok:
        return {"ok": False, "error": "face landmarks could not be found (face too small, occluded or turned away)"}
    x, y, bw, bh = [float(v) for v in obs.box]
    info = {"face_px": int(bw), "jaw_open": round(float(obs.jaw_open), 3), "blink": round(float(obs.blink), 3), "size": [w, h]}
    if bw < MIN_FACE_PX:
        return {"ok": False, "error": f"face is only {int(bw)} px wide; use a closer photo (at least {MIN_FACE_PX} px)", **info}
    if obs.jaw_open > JAW_ERR:
        return {"ok": False, "error": "mouth is open in the photo; use a photo with a closed, relaxed mouth (the idle animation keeps the mouth as in the photo)", **info}
    if obs.blink > BLINK_ERR:
        return {"ok": False, "error": "eyes look closed in the photo; use a photo with open eyes", **info}
    if obs.jaw_open > JAW_WARN:
        warnings.append("mouth is slightly open; a fully closed mouth gives a cleaner idle loop")
    if bw < 140:
        warnings.append("face is small in the frame; the avatar will look soft (a closer portrait is better)")
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    face_gray = gray[max(0, int(y)):int(y + bh), max(0, int(x)):int(x + bw)]
    if face_gray.size and cv2.Laplacian(face_gray, cv2.CV_64F).var() < 25:
        warnings.append("photo looks blurry")
    if face_gray.size and face_gray.mean() < 55:
        warnings.append("photo is dark")
    # square face crop with margin, like extract_face.py
    cx, cy, side = x + bw / 2, y + bh / 2, max(bw, bh) * 1.9
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    x1, y1 = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
    cv2.imwrite(str(out_dir / "face.png"), bgr[y0:y1, x0:x1])
    return {"ok": True, "warnings": warnings, **info}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    res = check(Path(a.image), Path(a.out_dir))
    print(json.dumps(res))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
