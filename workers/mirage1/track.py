"""Landmark tracking CLI (MediaPipe only, no torch): runs in workers/.venv-face because mediapipe 1.0 in .venv-flash crashes.
usage: python -m mirage1.track <video|image> <out.npy> [--fps 25]"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from facelib import FaceTracker, load_frames  # noqa: E402
from mirage1 import align  # noqa: E402

if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    if Path(src).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        frames = [cv2.imread(src)]
    else:
        frames = load_frames(src, 25.0)
    np.save(out, align.raw_points(frames, FaceTracker()))
