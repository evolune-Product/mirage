"""Face tooling shared by the lipsync service and offline scripts (runs in a venv with mediapipe: workers/.venv-face
or the GPU Docker image).

  * FaceTracker      - per-frame landmarks + blendshapes (mediapipe FaceLandmarker), robust fallback to Haar
  * smooth_track     - temporal smoothing of the face box centre (Gaussian, edge-padded) so the box follows head motion
  * detect_overlays  - find static text/label overlays (Zoom name tag, watermarks) from temporal statistics
  * prepare_base     - crop away overlays, pick a calm closed-mouth window, store processed base clip + track
  * composite_mouth  - Poisson-free soft blend (landmark-shaped mask, colour match, guarded sharpening)

Everything is plain numpy/opencv so it is unit-testable without a GPU."""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
MODEL_DIR = Path(os.environ.get("VOCALFACE_FACE_MODELS", HERE / "models"))
LANDMARKER_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"

# FaceMesh landmark indices
LIPS_OUTER = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
LIPS_INNER = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191]
JAW = [172, 136, 150, 149, 176, 148, 152, 377, 400, 378, 379, 365, 397, 288, 361, 323]
FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
             176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]


def landmarker_path() -> Path:
    p = MODEL_DIR / "face_landmarker.task"
    if not p.exists():
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(LANDMARKER_URL, p)
    return p


@dataclass
class FaceObs:
    ok: bool
    box: tuple  # x, y, w, h of the face (landmark extent), px
    mouth: tuple  # cx, cy, w, h of outer lips
    jaw_open: float
    blink: float
    pts: np.ndarray | None  # (478, 2) px


class FaceTracker:
    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision

        self._mp = mp
        opts = vision.FaceLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=str(landmarker_path()), delegate=mpp.BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.IMAGE, num_faces=1, output_face_blendshapes=True)
        self.lm = vision.FaceLandmarker.create_from_options(opts)

    def __call__(self, bgr: np.ndarray) -> FaceObs:
        h, w = bgr.shape[:2]
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        res = self.lm.detect(img)
        if not res.face_landmarks:
            return FaceObs(False, (0, 0, 0, 0), (0, 0, 0, 0), 0.0, 0.0, None)
        pts = np.array([[p.x * w, p.y * h] for p in res.face_landmarks[0]], dtype=np.float32)
        bs = {c.category_name: c.score for c in res.face_blendshapes[0]} if res.face_blendshapes else {}
        x0, y0 = pts[FACE_OVAL].min(0)
        x1, y1 = pts[FACE_OVAL].max(0)
        lp = pts[LIPS_OUTER]
        mx0, my0 = lp.min(0)
        mx1, my1 = lp.max(0)
        return FaceObs(True, (float(x0), float(y0), float(x1 - x0), float(y1 - y0)),
                       (float((mx0 + mx1) / 2), float((my0 + my1) / 2), float(mx1 - mx0), float(my1 - my0)),
                       float(bs.get("jawOpen", 0.0)),
                       float((bs.get("eyeBlinkLeft", 0.0) + bs.get("eyeBlinkRight", 0.0)) / 2), pts)


def haar_obs(bgr) -> FaceObs:
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    fs = det.detectMultiScale(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), 1.1, 6, minSize=(60, 60))
    if len(fs) == 0:
        return FaceObs(False, (0, 0, 0, 0), (0, 0, 0, 0), 0.0, 0.0, None)
    x, y, w, h = max(fs, key=lambda f: f[2] * f[3])
    return FaceObs(True, (float(x), float(y), float(w), float(h)), (x + w / 2, y + h * .75, w * .4, h * .15), 0.0, 0.0, None)


def smooth_series(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian smoothing along axis 0 with edge padding; NaN rows are interpolated first."""
    a = np.array(a, dtype=np.float64, copy=True)
    if a.ndim == 1:
        a = a[:, None]
        squeeze = True
    else:
        squeeze = False
    n = len(a)
    for k in range(a.shape[1]):
        bad = ~np.isfinite(a[:, k])
        if bad.all():
            a[:, k] = 0
        elif bad.any():
            a[bad, k] = np.interp(np.flatnonzero(bad), np.flatnonzero(~bad), a[~bad, k])
    if sigma > 0 and n > 1:
        r = max(1, int(3 * sigma))
        k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
        k /= k.sum()
        pad = np.pad(a, ((r, r), (0, 0)), mode="edge")
        a = np.stack([np.convolve(pad[:, j], k, mode="valid") for j in range(a.shape[1])], 1)
    return a[:, 0] if squeeze else a


def smooth_track(centres: np.ndarray, sigma: float = 2.5) -> np.ndarray:
    return smooth_series(centres, sigma)


def detect_overlays(frames: list[np.ndarray], border: float = 0.25) -> list[tuple]:
    """Static label/watermark overlays (Zoom/Meet name tag, logos, captions) -> [(x0, y0, x1, y1)].

    Two cues, both required to be temporally static (the real scene is not, or is not this flat):
      * label boxes: near-black or near-white solid rectangles touching the frame edge that contain contrasting text pixels
      * free text: tiny saturated-white/black glyph clusters that never change, closed into word blocks
    Only the outer `border` fraction of the frame is searched. Background photos with static texture do not trigger it
    because they lack solid-fill + glyph structure."""
    if len(frames) < 8:
        return []
    g = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames[:: max(1, len(frames) // 60)]]).astype(np.float32)
    H, W = g.shape[1:]
    tstd, med = g.std(0), g.mean(0)
    zone = np.zeros((H, W), np.uint8)
    bw, bh = int(W * border), int(H * border)
    zone[:, :bw] = 1; zone[:, W - bw:] = 1; zone[:bh] = 1; zone[H - bh:] = 1
    out = []
    for solid, text in (((med < 45) & (tstd < 4), med > 190), ((med > 215) & (tstd < 4), med < 80)):
        cand = (solid & (zone > 0)).astype(np.uint8)
        cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3)))
        n, _, st, _ = cv2.connectedComponentsWithStats(cand)
        for i in range(1, n):
            x, y, w, h = [int(v) for v in st[i, :4]]
            touches = x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2
            if not touches or not (18 <= w <= W * 0.5 and 6 <= h <= H * 0.12) or w / h < 1.5:
                continue
            roi_text = text[y:y + h, x:x + w]
            roi_tstd = tstd[y:y + h, x:x + w]
            glyph = float((roi_text & (roi_tstd < 6)).mean())
            fill = float(cand[y:y + h, x:x + w].mean())
            if glyph > 0.03 and fill > 0.4:
                out.append((max(0, x - 3), max(0, y - 3), min(W, x + w + 3), min(H, y + h + 3)))
    return merge_rects(out)


def merge_rects(rs: list[tuple]) -> list[tuple]:
    rs = [tuple(r) for r in rs]
    changed = True
    while changed:
        changed = False
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                if rect_overlap(rs[i], rs[j]):
                    a, b = rs[i], rs[j]
                    rs[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del rs[j]
                    changed = True
                    break
            if changed:
                break
    return rs


def rect_overlap(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def choose_crop(W: int, H: int, face_boxes: np.ndarray, overlays: list[tuple], aspect: float | None = None,
                margin: float = 4.2) -> tuple[int, int, int, int]:
    """Face-centred crop (x0,y0,x1,y1) of the source with `margin` x the face width around it, shifted/shrunk so that it
    contains the whole range of head motion and (when possible) avoids all detected overlays."""
    aspect = aspect or W / H
    xs0 = face_boxes[:, 0].min(); xs1 = (face_boxes[:, 0] + face_boxes[:, 2]).max()
    ys0 = face_boxes[:, 1].min(); ys1 = (face_boxes[:, 1] + face_boxes[:, 3]).max()
    fw = float(np.median(face_boxes[:, 2]))
    cw = min(W, max(margin * fw, (xs1 - xs0) * 1.3))
    ch = cw / aspect
    if ch > H:
        ch = H; cw = ch * aspect
    cx, cy = (xs0 + xs1) / 2, (ys0 + ys1) / 2 + 0.12 * ch  # a little extra room below the chin (shoulders)

    def box(cw_, ch_):
        x0 = min(max(0, cx - cw_ / 2), W - cw_)
        y0 = min(max(0, cy - ch_ / 2), H - ch_)
        return (int(x0) // 2 * 2, int(y0) // 2 * 2, int(x0 + cw_) // 2 * 2, int(y0 + ch_) // 2 * 2)

    b = box(cw, ch)
    for _ in range(14):
        if not any(rect_overlap(b, o) for o in overlays):
            break
        cw *= 0.94; ch = cw / aspect
        b = box(cw, ch)
    # never cut into the face
    return b


def mouth_openness_series(obs: list[FaceObs]) -> np.ndarray:
    return np.array([o.jaw_open if o.ok else np.nan for o in obs], dtype=np.float64)


def calm_window(jaw: np.ndarray, blink: np.ndarray, centres: np.ndarray, win: int, step: int = 3) -> int:
    """Start index of the window with a closed mouth (low jaw-open mean/peak), no blink, little motion."""
    n = len(jaw)
    if n <= win:
        return 0
    mot = np.r_[0, np.linalg.norm(np.diff(centres, axis=0), axis=1)]
    best, bs = 0, 1e9
    for s in range(0, n - win, step):
        j = jaw[s:s + win]
        score = j.mean() + 0.5 * j.max() + 0.02 * mot[s:s + win].mean() + 0.1 * blink[s:s + win].max()
        if score < bs:
            best, bs = s, score
    return best


def mouth_mask(shape, pts: np.ndarray | None, box_xywh=None, feather: float = 0.05) -> np.ndarray:
    """Soft mask (H,W) float in [0,1] around the lips + chin. With landmarks: convex hull of lips + lower jaw band, slightly
    dilated and feathered; without: ellipse on the lower face."""
    H, W = shape[:2]
    m = np.zeros((H, W), np.float32)
    if pts is not None:
        lips = pts[LIPS_OUTER]
        cx, cy = lips.mean(0)
        lw = lips[:, 0].max() - lips[:, 0].min()
        chin_y = pts[152][1]
        # region: from just under the nose to a bit above the chin tip so the Wav2Lip chin shadow never reaches the neck
        ell = (int(cx), int(cy + 0.1 * (chin_y - cy)))
        cv2.ellipse(m, ell, (int(lw * 0.78), int(max(lw * 0.55, (chin_y - cy) * 0.78))), 0, 0, 360, 1.0, -1)
        # keep it inside the face oval (the jaw line), so it can never paint background/neck
        oval = np.zeros_like(m)
        cv2.fillConvexPoly(oval, pts[FACE_OVAL].astype(np.int32), 1.0)
        oval = cv2.erode(oval, np.ones((5, 5), np.uint8), iterations=max(1, int(lw * 0.06)))
        m *= oval
        sig = max(1.5, lw * feather)
    else:
        x, y, w, h = box_xywh
        cv2.ellipse(m, (int(x + w * .5), int(y + h * .74)), (int(w * .38), int(h * .26)), 0, 0, 360, 1.0, -1)
        sig = max(1.5, w * feather)
    return np.clip(cv2.GaussianBlur(m, (0, 0), sig) * 1.15, 0, 1)


def color_match(gen: np.ndarray, ref: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Match mean/std per channel of `gen` to `ref` inside mask (LAB), with the std ratio clamped so a dark open mouth
    is not flattened."""
    g = cv2.cvtColor(gen, cv2.COLOR_BGR2LAB).astype(np.float32)
    r = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32)
    w = (mask > 0.3)
    if w.sum() < 50:
        return gen
    gm, gs = g[w].mean(0), g[w].std(0) + 1e-3
    rm, rs = r[w].mean(0), r[w].std(0) + 1e-3
    ratio = np.clip(rs / gs, 0.8, 1.25)
    out = (g - gm) * ratio + rm
    # only shift the mean for chroma (a, b): keeps lip colour natural; scale lightness contrast
    return cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def guarded_sharpen(img: np.ndarray, amount: float = 0.6, sigma: float = 1.2) -> np.ndarray:
    """Unsharp mask that skips near-flat/noisy pixels (halo + noise amplification guard) and clips overshoot."""
    f = img.astype(np.float32)
    blur = cv2.GaussianBlur(f, (0, 0), sigma)
    d = f - blur
    mag = np.abs(d).mean(2, keepdims=True)
    gate = np.clip((mag - 2.0) / 6.0, 0, 1) * np.clip(1.0 - (mag - 24.0) / 24.0, 0, 1)  # ignore flat and extreme edges
    return np.clip(f + amount * d * gate, 0, 255).astype(np.uint8)


def composite_mouth(frame: np.ndarray, gen_crop: np.ndarray, crop_xyxy: tuple, mask: np.ndarray, sharpen: float = 0.6,
                    seamless: bool = False) -> np.ndarray:
    """Blend generated face crop (BGR, already resized to the crop size) into `frame` inside crop_xyxy using `mask`
    (crop-sized). Colour matched; optional Poisson (cv2.seamlessClone) for the hardest lighting mismatches."""
    x1, y1, x2, y2 = crop_xyxy
    ref = frame[y1:y2, x1:x2]
    gen = color_match(gen_crop, ref, mask)
    if sharpen > 0:
        gen = guarded_sharpen(gen, sharpen)
    if seamless:
        m8 = (mask > 0.5).astype(np.uint8) * 255
        ys, xs = np.nonzero(m8)
        if len(xs) > 20:
            centre = (int((xs.min() + xs.max()) / 2) + x1, int((ys.min() + ys.max()) / 2) + y1)
            try:
                return cv2.seamlessClone(gen, frame, m8, centre, cv2.NORMAL_CLONE)
            except cv2.error:
                pass
    out = frame.copy()
    m3 = mask[:, :, None]
    out[y1:y2, x1:x2] = (gen.astype(np.float32) * m3 + ref.astype(np.float32) * (1 - m3)).astype(np.uint8)
    return out


def crossfade(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    return cv2.addWeighted(a, 1 - t, b, t, 0)


def content_rect(frames: list[np.ndarray], thresh: float = 14.0, max_frac: float = 0.35) -> tuple[int, int, int, int]:
    """Inner rectangle after trimming static near-black letterbox/pillarbox bars and capture-window edges (x0,y0,x1,y1)."""
    g = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames[:: max(1, len(frames) // 30)]]).astype(np.float32)
    H, W = g.shape[1:]
    m = g.mean(0)
    x0, y0, x1, y1 = 0, 0, W, H
    lim_y, lim_x = int(H * max_frac), int(W * max_frac)
    while y0 < lim_y and m[y0, x0:x1].mean() < thresh:
        y0 += 1
    while H - y1 < lim_y and m[y1 - 1, x0:x1].mean() < thresh:
        y1 -= 1
    while x0 < lim_x and m[y0:y1, x0].mean() < thresh:
        x0 += 1
    while W - x1 < lim_x and m[y0:y1, x1 - 1].mean() < thresh:
        x1 -= 1
    pad = 2 if (x0 or y0 or x1 < W or y1 < H) else 0  # a couple of px extra to drop anti-aliased edge pixels
    return (min(x0 + pad, W // 2) // 2 * 2, min(y0 + pad, H // 2) // 2 * 2, max(x1 - pad, W // 2 + 2) // 2 * 2, max(y1 - pad, H // 2 + 2) // 2 * 2)


def locate_face_region(path: str | Path, tracker, n_samples: int = 6, min_rel: float = 0.12):
    """For footage where the speaker is a small inset (screen recording + facecam, wide room shot): returns a native-resolution
    pre-crop (x0,y0,x1,y1) around the face, or None when the face is already big enough in the frame.
    The landmark detector misses faces below ~5% of the frame, so small faces are searched in overlapping tiles."""
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if n <= 0:
        return None
    hits, W, H = [], 0, 0
    for idx in np.linspace(0, max(0, n - 1), n_samples).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, f = cap.read()
        if not ok:
            continue
        H, W = f.shape[:2]
        o = tracker(f)
        if o.ok:
            hits.append((o.box, 0, 0))
            continue
        t = max(256, min(H, 384))
        for ty in range(0, max(1, H - t + 1), max(1, t // 2)):
            for tx in range(0, max(1, W - t + 1), max(1, t // 2)):
                o = tracker(np.ascontiguousarray(f[ty:ty + t, tx:tx + t]))
                if o.ok:
                    hits.append((o.box, tx, ty))
    if not hits or W == 0:
        return None
    boxes = np.array([[b[0] + tx, b[1] + ty, b[2], b[3]] for b, tx, ty in hits])
    med = np.median(boxes, 0)
    if med[2] >= min_rel * W:
        return None
    # keep the detections near the median (a facecam does not move; ignore stray hits on posters / other people)
    near = boxes[np.linalg.norm(boxes[:, :2] - med[:2], axis=1) < max(40, med[2])]
    med = np.median(near, 0) if len(near) else med
    fw = med[2]
    cw = max(4.0 * fw, 288.0)
    ch = cw * 9 / 16
    cx, cy = med[0] + fw / 2, med[1] + med[3] / 2 + 0.1 * ch
    x0 = int(min(max(0, cx - cw / 2), W - cw)) // 2 * 2
    y0 = int(min(max(0, cy - ch / 2), H - ch)) // 2 * 2
    return (x0, y0, int(x0 + cw) // 2 * 2, int(y0 + ch) // 2 * 2)


def load_frames(path: str | Path, fps: float = 25.0, max_w: int | None = None, max_s: float | None = None,
                pre_crop: tuple | None = None):
    cap = cv2.VideoCapture(str(path))
    src = cap.get(cv2.CAP_PROP_FPS) or fps
    frames, i, acc = [], 0, 0.0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        # resample to `fps` by accumulating time (handles 30 -> 25)
        acc += fps / src
        if acc >= 1.0:
            acc -= 1.0
            if pre_crop:
                f = f[pre_crop[1]:pre_crop[3], pre_crop[0]:pre_crop[2]]
            if max_w and f.shape[1] > max_w:
                f = cv2.resize(f, (max_w, int(f.shape[0] * max_w / f.shape[1])), interpolation=cv2.INTER_AREA)
            frames.append(f)
            if max_s and len(frames) >= max_s * fps:
                break
        i += 1
    return frames


def write_video(path: str | Path, frames: list[np.ndarray], fps: float = 25.0):
    h, w = frames[0].shape[:2]
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        vw.write(f)
    vw.release()
