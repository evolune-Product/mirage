"""Pick the best face frame from a video. Runs in workers/.venv (needs opencv).
usage: extract_face.py VIDEO OUT_PNG  -> prints JSON {ok, frame_idx, score, faces_found, ...}"""
import json
import sys

import cv2
import numpy as np


def main(video: str, out: str, max_samples: int = 60) -> dict:
    cap = cv2.VideoCapture(video)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    if n <= 0:
        return {"ok": False, "error": "cannot read video frames"}
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    eye = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
    best, found = None, 0
    for idx in np.linspace(0, n - 1, min(max_samples, n)).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, fr = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        faces = det.detectMultiScale(g, 1.1, 6, minSize=(max(g.shape) // 12,) * 2)
        if len(faces) == 0:
            continue
        found += 1
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        roi = g[y:y + h, x:x + w]
        sharp = cv2.Laplacian(roi, cv2.CV_64F).var()
        eyes = len(eye.detectMultiScale(roi[: h // 2], 1.1, 5))  # >=2 eyes visible ~ frontal, eyes open
        cx, cy = (x + w / 2) / g.shape[1], (y + h / 2) / g.shape[0]
        center = 1 - min(abs(cx - 0.5) + abs(cy - 0.5), 1)
        rivals = sum(1 for f in faces if f[2] * f[3] > 0.4 * w * h) - 1  # other comparably-sized faces => crowd
        score = (0.25 ** rivals) * (w * h / (g.shape[0] * g.shape[1])) ** 0.5 * np.log1p(sharp) * (1 + (eyes >= 2)) * (0.5 + center)
        if best is None or score > best[0]:
            best = (score, int(idx), fr, (int(x), int(y), int(w), int(h)))
    if best is None:
        return {"ok": False, "error": "no face detected in video", "frames_checked": int(min(max_samples, n))}
    cv2.imwrite(out, best[2])
    return {"ok": True, "frame_idx": best[1], "time_s": best[1] / fps, "score": float(best[0]),
            "bbox": best[3], "faces_found": found, "size": [best[2].shape[1], best[2].shape[0]]}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2])))
