"""Tight square face crop for avatar models. Runs in workers/.venv-face (MediaPipe on CPU via facelib).
usage: face_crop.py IN_IMAGE OUT_PNG [size]  -> prints JSON. Side = 2.3x face width, centre 0.12 face heights below the face centre,
so the face fills about half the frame (FlashHead barely moves the mouth when the face is small in the frame)."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import facelib as fl  # noqa: E402


def main(src: str, out: str, size: int = 768) -> dict:
    im = cv2.imread(src)
    if im is None:
        return {"cropped": False, "why": "unreadable image"}
    H, W = im.shape[:2]
    tr = fl.FaceTracker()
    o = None
    # small faces (full-body shots) are missed at a reduced size: retry at full size on the upper part of the image
    for k, (ry0, ry1) in [(min(1.0, 1600 / max(H, W)), (0, 1.0)), (1.0, (0, 0.6)), (1.0, (0, 1.0))]:
        y_a, y_b = int(H * ry0), int(H * ry1)
        reg = im[y_a:y_b]
        small = cv2.resize(reg, None, fx=k, fy=k) if k < 1 else reg
        o = tr(small)
        if o.ok:
            x, y, w, h = [v / k for v in o.box]
            y += y_a
            break
    if o is None or not o.ok:
        return {"cropped": False, "why": "no face found"}
    cx, cy = x + w / 2, y + h / 2 + 0.12 * h
    side = int(min(2.3 * max(w, h), W, H))
    ox = int(np.clip(cx - side / 2, 0, W - side)); oy = int(np.clip(cy - side / 2, 0, H - side))
    crop = cv2.resize(im[oy:oy + side, ox:ox + side], (size, size), interpolation=cv2.INTER_LANCZOS4)
    cv2.imwrite(out, crop)
    return {"cropped": True, "face_px": int(w), "crop_px": side}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 768)))
