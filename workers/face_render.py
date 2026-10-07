"""Engine-agnostic face render core used by lipsync_server.py and render_offline.py.

  prepare_base(replica_dir)   source video (+ optional listening clip) -> tracked, overlay-free, face-centred base clip
  Wav2LipEngine               96 px, real-time on MPS/CUDA (default)
  render(base, pcm16k, engine) -> list of BGR frames (same size as base.frames)

Works with or without mediapipe: without it the Haar detector gives one fixed box (old behaviour) but all compositing
improvements still apply."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

import facelib as fl

HERE = Path(__file__).resolve().parent
log = logging.getLogger("vocalface.face")
FPS = 25.0
BASE_VERSION = 3  # bump when prepare_base output format/logic changes (invalidates caches)
IDLE_SECONDS = float(os.environ.get("VOCALFACE_IDLE_SECONDS", "3.0"))


def pick_device() -> str:
    import torch

    want = (os.environ.get("VOCALFACE_LIPSYNC_DEVICE") or "auto").lower()
    if want != "auto":
        return want
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def sync(device: str):
    import torch

    if device == "cuda":
        torch.cuda.synchronize()
    elif device == "mps":
        torch.mps.synchronize()


@dataclass
class Base:
    rid: str
    frames: list                 # BGR crops (overlay free), len n
    pts: list                    # per-frame (478,2) landmarks in crop coords, or None
    boxes: np.ndarray            # (n,4) int x1,y1,x2,y2 square-ish model crop per frame (crop coords)
    masks: list                  # per-frame float32 (h,w) soft blend mask in crop coords
    info: dict = field(default_factory=dict)
    cursor: int = 0              # ping-pong position, continues between requests

    @property
    def size(self):
        return self.frames[0].shape[1], self.frames[0].shape[0]


def _trim(frames, rect):
    return frames if not rect else [f[rect[1]:rect[3], rect[0]:rect[2]] for f in frames]


def _track_all(frames, tracker):
    obs = []
    for f in frames:
        o = tracker(f) if tracker else fl.haar_obs(f)
        obs.append(o)
    return obs


def _fill_pts(obs):
    """Interpolate missing landmark frames from neighbours (hold)."""
    pts = [o.pts for o in obs]
    last = next((p for p in pts if p is not None), None)
    for i, p in enumerate(pts):
        if p is None:
            pts[i] = last
        else:
            last = p
    return pts


def _smooth_pts(pts, sigma=1.5):
    arr = np.stack(pts).astype(np.float64)  # n,478,2
    n = len(arr)
    flat = arr.reshape(n, -1)
    return fl.smooth_series(flat, sigma).reshape(arr.shape).astype(np.float32)


def model_boxes(pts_arr: np.ndarray, H: int, W: int, mode: str = "lm", sigma: float = 3.0) -> np.ndarray:
    """Per-frame square crop for the 96/256 px model, centred on the (smoothed) face, FIXED size (no scale jitter).
    mode 'lm' = nose-bottom anchored so the model's lower-half mask starts under the nose."""
    n = len(pts_arr)
    cheek_w = pts_arr[:, 454, 0] - pts_arr[:, 234, 0]
    nose_chin = pts_arr[:, 152, 1] - pts_arr[:, 2, 1]
    side = float(np.median(np.maximum(cheek_w * 1.02, nose_chin * 2.0 * 1.12)))
    cx = fl.smooth_series((pts_arr[:, 234, 0] + pts_arr[:, 454, 0]) / 2, sigma)
    ay = fl.smooth_series(pts_arr[:, 2, 1], sigma)  # nose bottom
    boxes = np.zeros((n, 4), np.int32)
    for i in range(n):
        x1 = int(round(cx[i] - side / 2)); y1 = int(round(ay[i] - side / 2 + 0.02 * side))
        x1 = min(max(0, x1), max(0, W - int(side))); y1 = min(max(0, y1), max(0, H - int(side)))
        boxes[i] = (x1, y1, x1 + int(side), y1 + int(side))
    return boxes


def variant_key(aspect: float | None, background: dict | None) -> str:
    """'' for the default base; otherwise a short key of (crop aspect, background spec) -> separate cache dir."""
    if aspect is None and not background:
        return ""
    import background as bgm

    return hashlib.sha1(f"{aspect and round(aspect, 4)}|{bgm.spec_key(background)}".encode()).hexdigest()[:8]


def _signature(src: Path, extra: Path | None, variant: str = "") -> str:
    h = hashlib.sha1(f"v{BASE_VERSION}|{IDLE_SECONDS}|{variant}".encode())
    for p in (src, extra):
        if p and p.exists():
            st = p.stat()
            h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def prepare_base(rdir: Path, tracker=None, force: bool = False, win_s: float | None = None,
                 aspect: float | None = None, background: dict | None = None) -> Base:
    """Build (or load from cache) the processed base clip for a replica directory containing source.mp4 and optionally
    listening.mp4. Cached under <rdir>/base_v3/ (frames/*.png, base.mp4, meta.json).
    aspect: target crop aspect (w/h) for vertical/square outputs (default: source aspect);
    background: background spec (see background.py), segmentation + replacement happen ONCE here and are cached, so live
    frames and offline renders pay nothing per frame. Variants are cached in base_v3_<key>/."""
    rid = rdir.name
    src, listen = rdir / "source.mp4", rdir / "listening.mp4"
    if not src.exists() and not listen.exists():
        raise FileNotFoundError(f"no source video for replica {rid}")
    variant = variant_key(aspect, background)
    sig = _signature(src, listen, variant)
    cache = rdir / ("base_v3" + (f"_{variant}" if variant else ""))
    meta_p = cache / "meta.json"
    win = int((win_s or IDLE_SECONDS) * FPS)
    t0 = time.time()
    if not force and meta_p.exists():
        try:
            meta = json.loads(meta_p.read_text())
            if meta.get("sig") == sig:
                frames = [cv2.imread(str(cache / "frames" / f"{i:04d}.png")) for i in range(meta["n"])]
                if all(f is not None for f in frames):
                    return _finish_base(rid, frames, meta["crop"], meta, tracker, cached=True)
        except Exception as e:  # corrupt cache -> rebuild
            log.warning("base cache for %s unusable (%s); rebuilding", rid, e)

    use_listen = listen.exists()
    clip_src = listen if use_listen else src
    max_s = 60 if use_listen else float(os.environ.get("VOCALFACE_PREP_SECONDS", "40"))
    pre_crop = fl.locate_face_region(clip_src, tracker) if tracker else None  # small facecam inset -> native-res pre-crop
    frames = fl.load_frames(clip_src, FPS, max_w=720, max_s=max_s, pre_crop=pre_crop)
    if len(frames) < 25:
        raise ValueError("source video too short")
    bx0, by0, bx1, by1 = fl.content_rect(frames)  # drop black bars / window edges
    if (bx1 - bx0, by1 - by0) != (frames[0].shape[1], frames[0].shape[0]):
        frames = [f[by0:by1, bx0:bx1] for f in frames]
        trimmed = [bx0, by0, bx1, by1]
    else:
        trimmed = None
    # overlay detection always on the main source (labels live there); listening clip is usually the same capture
    ov_frames = frames if not use_listen else (_trim(fl.load_frames(src, FPS, max_w=720, max_s=20, pre_crop=pre_crop), trimmed) if src.exists() else frames)
    overlays = fl.detect_overlays(ov_frames)
    # candidate search tracks every 2nd frame (halves the cold-start cost) and interpolates in between
    stride = 1 if use_listen else 2
    idx = np.arange(0, len(frames), stride)
    sub = _track_all([frames[i] for i in idx], tracker)
    if not any(o.ok for o in sub):
        raise ValueError("no face found in source video")

    def series(vals):
        v = np.array(vals, dtype=np.float64)
        good = np.isfinite(v).all(axis=-1) if v.ndim > 1 else np.isfinite(v)
        if v.ndim == 1:
            return np.interp(np.arange(len(frames)), idx[good], v[good])
        return np.stack([np.interp(np.arange(len(frames)), idx[good], v[good, k]) for k in range(v.shape[1])], 1)

    jaw = series([o.jaw_open if o.ok else np.nan for o in sub])
    blink = series([o.blink if o.ok else np.nan for o in sub])
    centres = series([[o.box[0] + o.box[2] / 2, o.box[1] + o.box[3] / 2] if o.ok else [np.nan, np.nan] for o in sub])
    centres = fl.smooth_series(centres, 2.0)
    jaw = fl.smooth_series(jaw, 1.0)
    full_boxes = series([list(o.box) if o.ok else [np.nan] * 4 for o in sub])
    if use_listen:
        s = 0
        e = min(len(frames), int(10 * FPS))  # keep <=10 s of listening clip
    else:
        s = fl.calm_window(jaw, blink, centres, win)
        e = min(len(frames), s + win)
    clip = frames[s:e]
    fb = full_boxes[s:e]
    H, W = clip[0].shape[:2]
    # black bars / window edges that exist in the chosen window (layouts can change over a screen recording)
    cx0, cy0, cx1, cy1 = fl.content_rect(clip)
    bars = [r for r in ((0, 0, W, cy0), (0, cy1, W, H), (0, 0, cx0, H), (cx1, 0, W, H)) if r[2] > r[0] and r[3] > r[1]]
    crop = fl.choose_crop(W, H, fb, list(overlays) + bars, aspect=aspect)
    meta = {"sig": sig, "n": len(clip), "crop": list(crop), "overlays": overlays, "listening": use_listen, "window_start": s,
            "jaw_mean": float(np.nanmean(jaw[s:e])), "jaw_global_mean": float(np.nanmean(jaw)),
            "tracker": "mediapipe" if tracker else "haar", "pre_crop": list(pre_crop) if pre_crop else None, "trimmed": trimmed, "src_wh": [W, H], "prep_s": round(time.time() - t0, 2)}
    cx0, cy0, cx1, cy1 = crop
    cropped = [f[cy0:cy1, cx0:cx1].copy() for f in clip]
    if background:
        import background as bgm

        t_bg = time.time()
        cropped = bgm.apply(cropped, background)
        meta["background"] = bgm.normalize(background)
        meta["bg_s"] = round(time.time() - t_bg, 2)
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "frames").mkdir(exist_ok=True)
    for i, f in enumerate(cropped):
        cv2.imwrite(str(cache / "frames" / f"{i:04d}.png"), f)
    meta_p.write_text(json.dumps(meta))
    try:  # processed base clip for inspection / other tools
        import subprocess

        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", str(cache / "frames" / "%04d.png"),
                        "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(cache / "base.mp4")], timeout=120)
    except Exception:
        pass
    return _finish_base(rid, cropped, list(crop), meta, tracker, cached=False)


def _finish_base(rid, frames, crop, meta, tracker, cached):
    """Landmarks + boxes + masks on the final cropped frames (cheap: n ~ 75 frames)."""
    H, W = frames[0].shape[:2]
    obs = _track_all(frames, tracker)
    if any(o.pts is not None for o in obs):
        pts = _smooth_pts(_fill_pts(obs))
        boxes = model_boxes(pts, H, W)
        masks = [fl.mouth_mask((H, W), p) for p in pts]
        pts_list = list(pts)
    else:  # Haar only: one fixed box (old behaviour)
        o = next((o for o in obs if o.ok), None)
        x, y, w, h = [int(v) for v in (o.box if o else (0, 0, W, H))]
        pad = int(0.18 * h)
        b = (max(0, x - pad // 2), max(0, y - pad // 4), min(W, x + w + pad // 2), min(H, y + h + pad))
        boxes = np.tile(np.array(b, np.int32), (len(frames), 1))
        m = fl.mouth_mask((H, W), None, (b[0], b[1], b[2] - b[0], b[3] - b[1]))
        masks = [m] * len(frames)
        pts_list = [None] * len(frames)
    meta = dict(meta, cached=cached)
    return Base(rid, frames, pts_list, boxes, masks, meta)


# ---------------------------------------------------------------- engines
class Wav2LipEngine:
    name = "wav2lip"

    def __init__(self, device: str, ckpt: str = "wav2lip_gan.pth"):
        import torch

        sys.path.insert(0, str(HERE / "Wav2Lip"))
        import audio as w2l_audio
        from models import Wav2Lip

        self.torch, self.audio, self.device = torch, w2l_audio, device
        m = Wav2Lip()
        sd = torch.load(HERE / "Wav2Lip" / "checkpoints" / ckpt, map_location="cpu", weights_only=False)["state_dict"]
        m.load_state_dict({k.replace("module.", ""): v for k, v in sd.items()})
        self.m = m.to(device).eval()
        self.size = 96

    def warmup(self):
        """Compile/allocate kernels for the usual batch sizes so the first live reply does not pay ~1.5 s."""
        torch = self.torch
        with torch.no_grad():
            for bs in (32, 25, 5):
                self.m(torch.zeros(bs, 1, 80, 16, device=self.device), torch.zeros(bs, 6, 96, 96, device=self.device))
        sync(self.device)

    def mel_chunks(self, a16: np.ndarray, fps: float = FPS):
        mel = self.audio.melspectrogram(a16)
        n = int(len(a16) / 16000 * fps)
        mult = 80.0 / fps
        out = []
        for i in range(n):
            s = int(i * mult)
            out.append(mel[:, -16:] if s + 16 > mel.shape[1] else mel[:, s:s + 16])
        return out

    def generate(self, base: Base, seq: list[int], chunks) -> list[np.ndarray]:
        torch, dev, S = self.torch, self.device, self.size
        outs = []
        for b0 in range(0, len(seq), 32):
            idx = range(b0, min(b0 + 32, len(seq)))
            imgs = []
            for i in idx:
                x1, y1, x2, y2 = base.boxes[seq[i]]
                imgs.append(cv2.resize(base.frames[seq[i]][y1:y2, x1:x2], (S, S), interpolation=cv2.INTER_AREA))
            imgs = np.asarray(imgs)
            masked = imgs.copy(); masked[:, S // 2:] = 0
            inp = np.concatenate((masked, imgs), axis=3) / 255.0
            mel_b = np.asarray([chunks[i] for i in idx]).reshape(len(imgs), 80, 16, 1)
            it = torch.FloatTensor(np.transpose(inp, (0, 3, 1, 2))).to(dev)
            mt = torch.FloatTensor(np.transpose(mel_b, (0, 3, 1, 2))).to(dev)
            with torch.no_grad():
                pred = self.m(mt, it)
            outs.extend((pred.cpu().numpy().transpose(0, 2, 3, 1) * 255.0).astype(np.uint8))
        return outs


def pingpong_seq(base: Base, n: int, advance: bool = True, start: int | None = None) -> list[int]:
    L = len(base.frames)
    period = max(1, 2 * L - 2)
    cur = base.cursor if start is None else start
    seq = []
    for k in range(n):
        c = (cur + k) % period
        seq.append(c if c < L else period - c)
    if advance:
        base.cursor = (cur + n) % period
    return seq


def paste(base: Base, idx: int, gen: np.ndarray, sharpen: float = 0.6, alpha: float = 1.0, seamless: bool = False):
    """Resize generated model-crop to the box, composite into base frame idx."""
    x1, y1, x2, y2 = [int(v) for v in base.boxes[idx]]
    w, h = x2 - x1, y2 - y1
    g = cv2.resize(gen, (w, h), interpolation=cv2.INTER_AREA if gen.shape[1] > w else cv2.INTER_CUBIC)
    m = base.masks[idx][y1:y2, x1:x2]
    if alpha < 1.0:
        m = m * alpha
    return fl.composite_mouth(base.frames[idx], g, (x1, y1, x2, y2), m, sharpen=sharpen, seamless=seamless)


def speech_alpha(a16: np.ndarray, n: int, fps: float = FPS, lo_db: float = -52.0, hi_db: float = -40.0) -> np.ndarray:
    """Per-video-frame weight of the generated mouth in [0,1]: ~0 in silence (the real closed-mouth base shows, instead of
    the generator's 'resting but slightly parted' mouth), 1 in speech. Fast attack, slower release so word gaps do not flicker."""
    hop = 16000 / fps
    win = int(0.04 * 16000)
    a = np.zeros(n)
    for i in range(n):
        c = int(i * hop + hop / 2)
        seg = a16[max(0, c - win // 2): c + win // 2]
        rms = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
        db = 20 * np.log10(rms + 1e-9)
        a[i] = np.clip((db - lo_db) / (hi_db - lo_db), 0, 1)
    out = np.zeros(n)
    prev = 0.0
    for i in range(n):  # attack 1 frame, release ~4 frames
        prev = a[i] if a[i] > prev else prev + (a[i] - prev) * 0.35
        out[i] = prev
    # look ahead one frame so the mouth starts opening just before the first sound (audio is played on the same clock)
    return np.maximum(out, np.r_[out[1:], out[-1:]] * 0.6)
