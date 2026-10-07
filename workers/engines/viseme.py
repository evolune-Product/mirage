"""Licence-clean live lip-sync: audio-driven viseme timeline + landmark warping of the base clip's own mouth.

No research-licensed weights, no neural network at all. Components and licences: MediaPipe FaceLandmarker (Apache-2.0,
already used for tracking), OpenCV (Apache-2.0), SciPy (BSD), NumPy (BSD). Runs on CPU in a few ms per frame.

How it works
  1. audio (16 kHz) -> per-video-frame viseme parameters {open, width, teeth, tongue} from energy and three spectral bands
     (rounded vowels are low-centroid, spread vowels/'ee' high-centroid, sibilants high-band dominated; plosive closures are
     the energy dips between syllables). An optional phoneme timeline (`visemes_from_phonemes`) can replace this when the
     TTS provides phonemes.
  2. the base frame's 478 FaceMesh landmarks give lips (outer/inner), corners and chin. Target positions are computed
     from the parameters (upper lip rises a little, lower lip + chin drop, corners widen/narrow) and a thin-plate-spline
     displacement field (scipy RBF on a coarse grid, upsampled) warps the real skin/lip texture with cv2.remap.
  3. the widened mouth opening is repainted procedurally: cavity colour sampled from the clip itself, an upper-teeth band
     (shaded toward the corners) and a tongue blob, all feathered, drawn only inside the warped inner-lip polygon.
The output is a box-sized crop of the base frame that face_render.paste blends with the usual soft mouth mask.

Honest limits: it is a 2D puppet. Visemes are an energy/spectral heuristic (no phoneme recognition), so it nails open/closed
timing and rough rounding but cannot distinguish e.g. 'f' vs 's' or show tongue-tip detail; large openings stretch the
real lip texture. Use MuseTalk where quality matters and the hardware allows."""
from __future__ import annotations

import numpy as np

from .base import LicenceInfo, LipsyncEngine, Weight

# FaceMesh indices (same set as facelib)
UPPER_OUT = [61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291]
LOWER_OUT = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291]
UPPER_IN = [78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308]
LOWER_IN = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308]
CHIN = [152, 148, 377, 176, 400, 175, 396, 171]
CORNERS = (61, 291)

FPS = 25.0

# ---------------------------------------------------------------- audio -> visemes
# phoneme (ARPAbet-ish / IPA-ish) -> (open, width, teeth). width 1 = neutral.
_PH = {
    "sil": (0.0, 1.0, 0.0),
    "p": (0.0, 0.95, 0.0), "b": (0.0, 0.95, 0.0), "m": (0.0, 0.95, 0.0),            # bilabial closure
    "f": (0.12, 1.0, 0.9), "v": (0.12, 1.0, 0.9),                                    # lip-teeth
    "th": (0.2, 1.0, 0.8), "dh": (0.2, 1.0, 0.8),
    "s": (0.15, 1.08, 0.9), "z": (0.15, 1.08, 0.9), "sh": (0.2, 0.85, 0.5), "zh": (0.2, 0.85, 0.5), "ch": (0.2, 0.85, 0.5), "jh": (0.2, 0.85, 0.5),
    "t": (0.2, 1.0, 0.6), "d": (0.2, 1.0, 0.6), "n": (0.2, 1.0, 0.5), "l": (0.3, 1.0, 0.4), "r": (0.25, 0.9, 0.3),
    "k": (0.3, 1.0, 0.2), "g": (0.3, 1.0, 0.2), "ng": (0.25, 1.0, 0.2), "h": (0.3, 1.0, 0.2), "y": (0.25, 1.1, 0.5), "w": (0.2, 0.7, 0.0),
    "aa": (1.0, 1.0, 0.3), "ae": (0.85, 1.1, 0.5), "ah": (0.8, 1.0, 0.3), "ao": (0.8, 0.8, 0.1), "aw": (0.8, 0.85, 0.2), "ay": (0.85, 1.05, 0.4),
    "eh": (0.6, 1.1, 0.5), "er": (0.45, 0.9, 0.2), "ey": (0.5, 1.12, 0.6), "ih": (0.4, 1.1, 0.5), "iy": (0.3, 1.18, 0.7),
    "ow": (0.55, 0.7, 0.0), "oy": (0.6, 0.75, 0.1), "uh": (0.4, 0.8, 0.0), "uw": (0.3, 0.65, 0.0),
}


def visemes_from_phonemes(timeline: list, n_frames: int, fps: float = FPS) -> dict:
    """timeline: [(phoneme, start_s, end_s)] (lower-case ARPAbet without stress, 'sil' for pauses) -> per-frame params,
    smoothed with the same attack/release as the audio path. Unknown phonemes map to a neutral half-open vowel."""
    o = np.zeros(n_frames); w = np.ones(n_frames); t = np.zeros(n_frames)
    for ph, s, e in timeline:
        a, wd, te = _PH.get(ph.lower().rstrip("012"), (0.4, 1.0, 0.3))
        i0, i1 = int(round(s * fps)), max(int(round(s * fps)) + 1, int(round(e * fps)))
        o[i0:i1], w[i0:i1], t[i0:i1] = a, wd, te
    return _finish(o, w, t)


def _smooth(x: np.ndarray, attack: float, release: float) -> np.ndarray:
    out = np.zeros_like(x, dtype=np.float64)
    prev = 0.0
    for i, v in enumerate(x):
        k = attack if v > prev else release
        prev = prev + (v - prev) * k
        out[i] = prev
    return out


def _finish(o, w, t):
    o = np.clip(_smooth(o, 0.85, 0.5), 0, 1)
    w = 1.0 + _smooth(w - 1.0, 0.6, 0.4) if False else 1.0 + (np.convolve(w - 1.0, [0.25, 0.5, 0.25], "same"))
    t = np.clip(np.convolve(t, [0.25, 0.5, 0.25], "same"), 0, 1)
    return {"open": o, "width": w, "teeth": t}


def audio_visemes(a16: np.ndarray, n: int, fps: float = FPS) -> dict:
    """16 kHz float audio -> per-frame viseme parameters (n frames)."""
    hop = int(16000 / fps)
    win = 1024
    o = np.zeros(n); w = np.ones(n); t = np.zeros(n)
    win_f = np.hanning(win)
    freqs = np.fft.rfftfreq(win, 1 / 16000)
    lo_m, mid_m, hi_m = freqs < 800, (freqs >= 800) & (freqs < 3500), freqs >= 3500
    for i in range(n):
        c = i * hop + hop // 2 + hop // 2  # one frame look-ahead: the mouth opens just before the sound is heard
        seg = a16[max(0, c - win // 2): c + win // 2]
        if len(seg) < win:
            seg = np.pad(seg, (0, win - len(seg)))
        rms = float(np.sqrt(np.mean(seg ** 2)))
        db = 20 * np.log10(rms + 1e-9)
        e = float(np.clip((db + 46.0) / 32.0, 0, 1))  # -46 dB floor .. -14 dB (TTS speech peaks sit around -15 dB)
        if e <= 0.02:
            continue
        sp = np.abs(np.fft.rfft(seg * win_f)) ** 2 + 1e-12
        lo, mid, hi = sp[lo_m].sum(), sp[mid_m].sum(), sp[hi_m].sum()
        tot = lo + mid + hi
        fric = hi / tot                                # sibilant / fricative share
        voiced = (lo + mid) / tot
        cent = float((freqs[mid_m | lo_m] * sp[mid_m | lo_m]).sum() / sp[mid_m | lo_m].sum())  # 300..2500 Hz
        spread = float(np.clip((cent - 900.0) / 900.0, -1, 1))   # + = 'ee'-like, - = 'oo'-like
        o[i] = e ** 1.3 * (1.0 - 0.7 * float(np.clip((fric - 0.35) / 0.4, 0, 1)))
        o[i] *= 1.0 - 0.35 * max(0.0, spread)           # spread vowels are less open than 'aa'
        w[i] = 1.0 + 0.14 * spread * voiced
        t[i] = float(np.clip((fric - 0.3) / 0.4, 0, 1)) * 0.9 + 0.25 * max(0.0, spread) * e
    return _finish(o, w, t)


# ---------------------------------------------------------------- warping
def _control_points(p: np.ndarray, open_: float, width: float, hmax: float) -> tuple[np.ndarray, np.ndarray]:
    """Source landmarks (n,2) and their target positions for one frame. p: (478,2) in crop coords."""
    cx, cy = p[[13, 14]].mean(0)
    wm = float(np.linalg.norm(p[291] - p[61])) + 1e-6
    dy = open_ * hmax * wm                       # lower-lip drop in px
    src, dst = [], []

    def add(idx, ddx, ddy):
        for i in idx:
            q = p[i]
            src.append(q)
            dst.append(q + np.array([ddx(q), ddy(q)]))

    def xscale(q):
        return (q[0] - cx) * (width - 1.0)

    def prof(q):  # 1 at the mouth centre, falls toward the corners
        return float(np.clip(1.0 - 0.75 * ((q[0] - cx) / (wm / 2)) ** 2, 0.15, 1.0))

    up_k = 0.18
    add(UPPER_OUT[1:-1], xscale, lambda q: -up_k * dy * prof(q))
    add(UPPER_IN[1:-1], xscale, lambda q: -up_k * dy * prof(q))
    add(LOWER_OUT[1:-1], xscale, lambda q: dy * prof(q))
    add(LOWER_IN[1:-1], xscale, lambda q: dy * prof(q) * 1.02)
    for i in (61, 78):
        add([i], xscale, lambda q: 0.12 * dy)
    for i in (291, 308):
        add([i], xscale, lambda q: 0.12 * dy)
    add(CHIN, lambda q: 0.0, lambda q: 0.7 * dy * float(np.clip(1.0 - abs(q[0] - cx) / (wm * 0.9), 0.3, 1)))
    # anchors: ring around the mouth that must not move (nose, cheeks, jaw line), lower half follows a little for continuity
    for ang in np.linspace(0, 2 * np.pi, 16, endpoint=False):
        r = 1.45 * wm
        q = np.array([cx + r * np.cos(ang) * 1.15, cy + r * np.sin(ang)])
        lower = np.sin(ang) > 0.3
        src.append(q)
        dst.append(q + np.array([0.0, 0.25 * dy if lower else 0.0]))
    return np.asarray(src, np.float64), np.asarray(dst, np.float64)


def warp_mouth(img: np.ndarray, p: np.ndarray, open_: float, width: float, hmax: float = 0.5):
    """Warp `img` (BGR crop) so the mouth takes the viseme shape. Returns (warped, dst inner-lip polygon (n,2) float)."""
    import cv2
    from scipy.interpolate import RBFInterpolator

    H, W = img.shape[:2]
    src, dst = _control_points(p, open_, width, hmax)
    wm = float(np.linalg.norm(p[291] - p[61]))
    cx, cy = p[[13, 14]].mean(0)
    x0, x1 = int(max(0, cx - 1.7 * wm)), int(min(W, cx + 1.7 * wm))
    y0, y1 = int(max(0, cy - 1.2 * wm)), int(min(H, cy + 2.0 * wm))
    step = 4
    gx = np.arange(x0, x1 + step, step); gy = np.arange(y0, y1 + step, step)
    GX, GY = np.meshgrid(gx, gy)
    # backward map: for each output pixel (dest position) where to sample in the source
    rbf = RBFInterpolator(dst, src - dst, kernel="thin_plate_spline", smoothing=1e-3)
    d = rbf(np.stack([GX.ravel(), GY.ravel()], 1)).reshape(GX.shape + (2,))
    # fade the field to zero toward the ROI border so the patch joins the untouched frame seamlessly
    fy = np.clip(np.minimum(GY - y0, y1 + step - GY) / (0.35 * wm), 0, 1)
    fx = np.clip(np.minimum(GX - x0, x1 + step - GX) / (0.35 * wm), 0, 1)
    d *= (fx * fy)[..., None]
    roi_w, roi_h = x1 - x0, y1 - y0
    flow = cv2.resize(d.astype(np.float32), (GX.shape[1] * step, GX.shape[0] * step), interpolation=cv2.INTER_LINEAR)[:roi_h, :roi_w]
    mx, my = np.meshgrid(np.arange(x0, x1, dtype=np.float32), np.arange(y0, y1, dtype=np.float32))
    mapx, mapy = mx + flow[..., 0], my + flow[..., 1]
    out = img.copy()
    out[y0:y1, x0:x1] = cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    # destination inner-lip polygon = warped inner landmarks (upper inner then lower inner reversed)
    s_in, d_in = {}, {}
    for s_, d_ in zip(src, dst):
        s_in[tuple(np.round(s_, 3))] = d_
    poly = []
    for i in UPPER_IN + LOWER_IN[::-1][1:-1]:
        q = p[i]
        k = tuple(np.round(q.astype(np.float64), 3))
        poly.append(s_in.get(k, q))
    return out, np.asarray(poly, np.float32)


def paint_interior(img: np.ndarray, poly: np.ndarray, base_img: np.ndarray, base_poly: np.ndarray, teeth: float, open_: float):
    """Repaint the opened mouth cavity inside `poly`: sampled dark cavity, upper-teeth band, tongue. In place."""
    import cv2

    H, W = img.shape[:2]
    m = np.zeros((H, W), np.uint8)
    cv2.fillPoly(m, [np.round(poly).astype(np.int32)], 255)
    area = float((m > 0).sum())
    if area < 6:
        return img
    # cavity colour from the clip's own mouth interior (darkened), robust median
    bm = np.zeros((H, W), np.uint8)
    cv2.fillPoly(bm, [np.round(base_poly).astype(np.int32)], 255)
    bm = cv2.erode(bm, np.ones((3, 3), np.uint8))
    cav = np.median(base_img[bm > 0], 0) if (bm > 0).sum() > 8 else np.array([40.0, 30.0, 50.0])
    cav = np.clip(cav * 0.6, np.array([42.0, 34.0, 52.0]), np.array([78.0, 66.0, 96.0]))
    ys, xs = np.nonzero(m)
    y_top, y_bot, x_l, x_r = ys.min(), ys.max(), xs.min(), xs.max()
    h = max(1, y_bot - y_top)
    layer = np.empty((H, W, 3), np.float32)
    layer[:] = cav
    # vertical shading: deeper/darker toward the throat
    yy = np.clip((np.arange(H) - y_top) / h, 0, 1)[:, None, None]
    layer *= (1.0 - 0.25 * yy)
    # tongue: pinkish blob in the lower half when open
    if open_ > 0.35 and h > 5:
        tm = np.zeros((H, W), np.float32)
        cv2.ellipse(tm, (int((x_l + x_r) / 2), int(y_bot - 0.18 * h)), (max(2, int((x_r - x_l) * 0.28)), max(1, int(h * 0.3))), 0, 0, 360, 1.0, -1)
        tm = cv2.GaussianBlur(tm, (0, 0), max(0.8, h * 0.08))[..., None]
        layer = layer * (1 - 0.55 * tm) + np.array([88.0, 82.0, 150.0], np.float32) * 0.55 * tm
    # upper teeth band
    if teeth > 0.05 and h > 3:
        th = np.zeros((H, W), np.float32)
        tb = int(y_top + max(1.5, teeth * 0.5 * h))
        th[y_top:tb + 1, x_l:x_r + 1] = 1.0
        th = cv2.GaussianBlur(th, (0, 0), 0.9)
        # darker toward the corners (buccal corridor)
        cxm = (x_l + x_r) / 2
        corridor = np.clip(1.0 - ((np.arange(W) - cxm) / max(1.0, (x_r - x_l) / 2)) ** 2 * 0.6, 0.35, 1.0)[None, :]
        teeth_col = np.array([188.0, 194.0, 202.0], np.float32)
        th = (th * corridor)[..., None]
        layer = layer * (1 - th) + teeth_col * th
    layer = cv2.GaussianBlur(layer, (0, 0), 1.1)  # match the softness of the footage
    a = cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), 1.0)[..., None]
    # keep the 1-px lip rim from the warped image (do not overpaint the lip edge)
    inner = cv2.erode(m, np.ones((3, 3), np.uint8)).astype(np.float32) / 255.0
    a = np.minimum(a, cv2.GaussianBlur(inner, (0, 0), 0.7)[..., None] * 1.3 + 0.0)
    a = np.clip(a, 0, 1)
    img[:] = np.clip(img.astype(np.float32) * (1 - a) + layer * a, 0, 255).astype(np.uint8)
    return img


class VisemeEngine(LipsyncEngine):
    name = "viseme"
    description = ("Licence-clean: energy/spectral viseme timeline + MediaPipe-landmark thin-plate warp of the clip's own "
                   "mouth, procedural teeth/cavity. CPU, 100+ fps; puppet-like")
    licence = LicenceInfo(
        "Own code (this repo) + MediaPipe Apache-2.0 + OpenCV Apache-2.0 + SciPy BSD",
        weights=(Weight("face_landmarker.task (MediaPipe)", "Apache-2.0", True, "already used for face tracking in every engine"),),
        note="No lip-sync model weights are loaded.")
    paste_sharpen = 0.0  # the crop is the real frame; sharpening would only amplify warp interpolation

    def __init__(self):
        self.hmax = 0.27
        self.last_params = None

    @classmethod
    def available(cls):
        try:
            import cv2, scipy  # noqa: F401, E401
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        return True, ""

    def load(self, device: str):
        from . import check_allowed

        check_allowed("viseme")
        import os

        self.hmax = float(os.environ.get("VOCALFACE_VISEME_GAIN", "0.27"))
        return self

    def warmup(self):
        pass

    def mel_chunks(self, a16, fps: float = FPS):
        n = int(len(a16) / 16000 * fps)
        return audio_visemes(a16, n, fps)

    def generate(self, base, seq, cond):
        outs = []
        for i, idx in enumerate(seq):
            x1, y1, x2, y2 = [int(v) for v in base.boxes[idx]]
            crop = base.frames[idx][y1:y2, x1:x2].copy()
            p = base.pts[idx]
            if p is None:  # Haar fallback has no landmarks: nothing to warp, pass the base through
                outs.append(crop)
                continue
            pl = p - np.array([x1, y1], np.float32)
            o, w, t = float(cond["open"][i]), float(cond["width"][i]), float(cond["teeth"][i])
            if o < 0.01 and abs(w - 1) < 0.01:
                outs.append(crop)
                continue
            warped, poly = warp_mouth(crop, pl, o, w, self.hmax)
            base_poly = pl[UPPER_IN + LOWER_IN[::-1][1:-1]]
            paint_interior(warped, poly, crop, base_poly, t, o)
            outs.append(warped)
        return outs
