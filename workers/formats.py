"""Output formats: aspect ratios with face-aware reframing, logo watermark, scene transitions, thumbnail picking. Pure numpy/cv2.

Aspects: "16:9" (1280x720), "9:16" (720x1280, vertical shorts), "1:1" (720x720); `resolution` = short side in px (480/720/1080).
The base clip is already prepared with the target crop aspect (face_render.prepare_base(aspect=...)), so the reframer only
makes the small residual crop that keeps the (smoothed) face centred, then resizes. Upscaling beyond the source's real
pixels is unavoidable for 9:16 from 16:9 footage: a 720p webcam source gives ~230 px of width, so vertical output is soft;
a portrait photo/clip does not have this problem."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

ASPECTS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}
RESOLUTIONS = (480, 720, 1080)
TRANSITIONS = ("cut", "fade", "dip", "slide")
POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")


def out_size(fmt: str, resolution: int = 720) -> tuple[int, int]:
    if fmt not in ASPECTS:
        raise ValueError(f"unknown format {fmt!r} (one of {', '.join(ASPECTS)})")
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}")
    a = ASPECTS[fmt]
    if a >= 1:
        h = resolution
        w = int(round(h * a / 2)) * 2
    else:
        w = resolution
        h = int(round(w / a / 2)) * 2
    return w, h


class Reframer:
    """Crop each base frame to the target aspect keeping the face centred, then resize to the output size."""

    def __init__(self, frames_hw: tuple[int, int], pts: list, out: tuple[int, int], sigma: float = 4.0):
        H, W = frames_hw
        self.out = out
        aspect = out[0] / out[1]
        if W / H >= aspect:
            ch, cw = H, int(round(H * aspect))
        else:
            cw, ch = W, int(round(W / aspect))
        self.cw, self.ch, self.W, self.H = cw, ch, W, H
        n = len(pts)
        cx = np.full(n, W / 2.0)
        cy = np.full(n, H / 2.0)
        for i, p in enumerate(pts):
            if p is not None:
                cx[i], cy[i] = float(p[[234, 454], 0].mean()), float(p[[10, 152], 1].mean())
        from scipy.ndimage import gaussian_filter1d

        if n > 1 and sigma > 0:
            cx, cy = gaussian_filter1d(cx, sigma, mode="nearest"), gaussian_filter1d(cy, sigma, mode="nearest")
        self.x0 = np.clip(np.round(cx - cw / 2), 0, W - cw).astype(int)
        # vertical: keep the face a bit above centre (headroom) when there is vertical slack
        self.y0 = np.clip(np.round(cy - ch * 0.45), 0, H - ch).astype(int)
        self.noop = (cw, ch) == (W, H) and (W, H) == out

    def __call__(self, frame: np.ndarray, idx: int) -> np.ndarray:
        if self.noop:
            return frame
        x0, y0 = self.x0[idx], self.y0[idx]
        c = frame[y0:y0 + self.ch, x0:x0 + self.cw]
        if (c.shape[1], c.shape[0]) == self.out:
            return c
        up = self.out[0] > c.shape[1]
        return cv2.resize(c, self.out, interpolation=cv2.INTER_CUBIC if up else cv2.INTER_AREA)


class Logo:
    """Watermark: RGBA (or RGB) image placed in a corner, `scale` = logo width as a fraction of the frame width."""

    def __init__(self, path: str, size: tuple[int, int], position: str = "top-right", scale: float = 0.14, opacity: float = 0.9,
                 margin: float = 0.03, **_ignored):  # extra keys (asset_id) come from the API spec
        if position not in POSITIONS:
            raise ValueError(f"logo position must be one of {POSITIONS}")
        im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if im is None:
            raise ValueError("logo image could not be read")
        if im.ndim == 2:
            im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
        a = im[..., 3:4].astype(np.float32) / 255.0 if im.shape[2] == 4 else np.ones((*im.shape[:2], 1), np.float32)
        bgr = im[..., :3]
        W, H = size
        lw = max(8, int(W * scale))
        lh = max(8, int(lw * bgr.shape[0] / bgr.shape[1]))
        self.bgr = cv2.resize(bgr, (lw, lh), interpolation=cv2.INTER_AREA).astype(np.float32)
        self.a = (cv2.resize(a, (lw, lh), interpolation=cv2.INTER_AREA).reshape(lh, lw, 1) * float(opacity)).astype(np.float32)
        m = int(min(W, H) * margin)
        self.x = m if position.endswith("left") else W - lw - m
        self.y = m if position.startswith("top") else H - lh - m

    def draw(self, frame: np.ndarray) -> np.ndarray:
        h, w = self.bgr.shape[:2]
        roi = frame[self.y:self.y + h, self.x:self.x + w].astype(np.float32)
        frame[self.y:self.y + h, self.x:self.x + w] = (roi * (1 - self.a) + self.bgr * self.a).astype(np.uint8)
        return frame


def blend_transition(kind: str, a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """Frame at progress t in (0,1) from outgoing frame a to incoming frame b."""
    if kind == "fade":
        return cv2.addWeighted(a, 1 - t, b, t, 0)
    if kind == "dip":  # through black: first half fades a out, second half fades b in
        return (a * (1 - 2 * t)).astype(np.uint8) if t < 0.5 else (b * (2 * t - 1)).astype(np.uint8)
    if kind == "slide":  # b pushes a out to the left, eased
        w = a.shape[1]
        e = t * t * (3 - 2 * t)
        off = int(round(e * w))
        out = np.empty_like(a)
        out[:, : w - off] = a[:, off:]
        out[:, w - off:] = b[:, :off]
        return out
    return b


def sharpness(f: np.ndarray) -> float:
    return float(cv2.Laplacian(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def pad_audio_to_frames(x: np.ndarray, sr: int, fps: float = 25.0, lead: float = 0.0, tail: float = 0.0) -> np.ndarray:
    """Add silence at both ends and pad so the length is a whole number of video frames (exact A/V sync across scenes)."""
    spf = int(round(sr / fps))
    x = np.concatenate([np.zeros(int(lead * sr), x.dtype), x, np.zeros(int(tail * sr), x.dtype)])
    rem = (-len(x)) % spf
    return np.concatenate([x, np.zeros(rem, x.dtype)]) if rem else x


def overlap_add(parts: list[np.ndarray], overlap: int) -> np.ndarray:
    """Concatenate audio parts with a linear crossfade of `overlap` samples (0 = plain concat)."""
    if not parts:
        return np.zeros(0, np.float32)
    out = parts[0].astype(np.float32)
    for p in parts[1:]:
        p = p.astype(np.float32)
        if overlap <= 0:
            out = np.concatenate([out, p])
            continue
        k = min(overlap, len(out), len(p))
        r = np.linspace(0, 1, k, dtype=np.float32)
        mix = out[-k:] * (1 - r) + p[:k] * r
        out = np.concatenate([out[:-k], mix, p[k:]])
    return out


from scenes import split_scenes  # noqa: E402,F401  (pure-python module so the backend can import it without numpy/cv2)
