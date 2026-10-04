"""Landmark-based face alignment (similarity transform to a canonical template), crop warping, paste-back masks.
Written from scratch for Mirage-1. Uses only MediaPipe FaceLandmarker (Apache-2.0) via workers/facelib.py for landmarks."""
import json
from pathlib import Path

import cv2
import numpy as np

S = 128  # crop size
STABLE = [33, 263, 133, 362, 168, 1]  # outer/inner eye corners, nose bridge, nose tip: move little when speaking
MASK_ROW = 0.58  # generator input hides rows >= MASK_ROW * S (below the nose)
TEMPLATE_PATH = Path(__file__).with_name("template.json")
EYE_L, EYE_R, EYE_Y = 0.31, 0.69, 0.34  # where the outer eye corners land in the crop (fraction of S)


def eye_similarity(p: np.ndarray, size: int = S) -> np.ndarray:
    """2x3 similarity that sends outer eye corners (landmarks 33, 263) to the fixed canonical positions."""
    src = np.array([p[0], p[1]], np.float32)
    dst = np.array([[EYE_L * size, EYE_Y * size], [EYE_R * size, EYE_Y * size]], np.float32)
    return cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)[0]


def build_template(pts_seq: np.ndarray) -> np.ndarray:
    """pts_seq (N, len(STABLE), 2) -> mean canonical positions of the stable points in crop pixels (size S)."""
    acc = []
    for p in pts_seq:
        M = eye_similarity(p)
        acc.append(p @ M[:, :2].T + M[:, 2])
    return np.mean(acc, 0)


def load_template() -> np.ndarray:
    return np.array(json.loads(TEMPLATE_PATH.read_text())["template"], np.float32)


def fit(p: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Similarity transform (2x3) mapping frame coords -> crop coords from stable landmarks p (K,2)."""
    return cv2.estimateAffinePartial2D(p.astype(np.float32), template.astype(np.float32), method=cv2.LMEDS)[0]


def warp_crop(frame: np.ndarray, M: np.ndarray, size: int = S) -> np.ndarray:
    s = float(np.sqrt(abs(np.linalg.det(M[:, :2]))))
    if s < 0.75:  # downscaling: pre-shrink with area filter to avoid aliasing
        frame = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        M = np.concatenate([M[:, :2] / s, M[:, 2:]], 1)
    return cv2.warpAffine(frame, M, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def paste_mask(size: int = S, feather: float = 0.07) -> np.ndarray:
    """Soft (size,size) float mask of the region we paste back: an ellipse around mouth+chin (below MASK_ROW)."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) / size
    d = np.sqrt(((xx - 0.5) / 0.25) ** 2 + ((yy - 0.77) / 0.21) ** 2)
    m = np.clip((1.0 - d) / (feather * 4), 0, 1)
    m *= np.clip((yy - (MASK_ROW - 0.01)) / 0.04, 0, 1)
    return cv2.GaussianBlur(m, (0, 0), size * 0.015)


def paste_back(frame: np.ndarray, crop: np.ndarray, M: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Warp the generated crop (+mask) back into the frame with the inverse transform and alpha-blend."""
    h, w = frame.shape[:2]
    Mi = cv2.invertAffineTransform(M)
    big = cv2.warpAffine(crop, Mi, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    a = cv2.warpAffine(mask, Mi, (w, h), flags=cv2.INTER_LINEAR)[..., None]
    return (frame.astype(np.float32) * (1 - a) + big.astype(np.float32) * a).clip(0, 255).astype(np.uint8)


def raw_points(frames, tracker) -> np.ndarray:
    """Tracker on every frame -> (N,K,2) stable landmarks, NaN where no face was found."""
    pts = np.full((len(frames), len(STABLE), 2), np.nan, np.float32)
    for i, f in enumerate(frames):
        o = tracker(f)
        if o.ok:
            pts[i] = o.pts[STABLE]
    return pts


def smooth_points(pts: np.ndarray, sigma: float = 1.5):
    """Interpolate gaps + Gaussian smooth over time. Returns (pts, found_mask)."""
    from facelib import smooth_series
    pts = pts.copy()
    N = len(pts)
    ok = ~np.isnan(pts[:, 0, 0])
    if ok.sum() < 1:
        return pts, ok
    idx = np.arange(N)
    for k in range(len(STABLE)):
        for c in range(2):
            pts[:, k, c] = np.interp(idx, idx[ok], pts[ok, k, c])
            if sigma > 0 and N > 1:
                pts[:, k, c] = smooth_series(pts[:, k, c], sigma)
    return pts, ok
