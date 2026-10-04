"""Landmark + blendshape tracking CLI (MediaPipe only, no torch): runs in workers/.venv-face because mediapipe 1.0 in .venv-flash crashes.
usage: python -m mirage1.track <video|image> <out.npy> [--blend out_blend.npy]
Blendshapes (52 scores per frame) are used ONLY as lip-state labels for the audio->lip model (MediaPipe, Apache-2.0)."""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from facelib import FaceTracker, load_frames  # noqa: E402
from mirage1 import align  # noqa: E402


def blendshapes(tracker, frames):
    out, names = [], None
    for f in frames:
        img = tracker._mp.Image(image_format=tracker._mp.ImageFormat.SRGB, data=cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        r = tracker.lm.detect(img)
        if r.face_blendshapes:
            names = [c.category_name for c in r.face_blendshapes[0]]
            out.append([c.score for c in r.face_blendshapes[0]])
        else:
            out.append([np.nan] * 52)
    return np.array(out, np.float32), names


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    if Path(src).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        frames = [cv2.imread(src)]
    else:
        frames = load_frames(src, 25.0)
    tr = FaceTracker()
    np.save(out, align.raw_points(frames, tr))
    if "--blend" in sys.argv:
        b, names = blendshapes(tr, frames)
        np.save(sys.argv[sys.argv.index("--blend") + 1], b)
        (Path(sys.argv[sys.argv.index("--blend") + 1]).with_suffix(".names.txt")).write_text("\n".join(names or []))
