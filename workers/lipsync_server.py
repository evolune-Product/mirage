"""Live lip-sync service (Wav2Lip on Apple MPS / CPU). Runs in workers/.venv.

  uvicorn lipsync_server:app --port 8100     (from the workers/ directory)

POST /idle/{replica_id}            -> {"fps", "frames": [b64 jpeg...]}  short idle loop (client ping-pongs it)
POST /render/{replica_id}          body = raw int16 mono 24 kHz PCM -> {"fps", "frames": [b64 jpeg...], "ms"}
GET  /health

The base video is the replica's source video. Lip-sync replaces the lower face only, so head motion and
blinking come from real footage. Quality note: Wav2Lip works at 96x96, so close-ups look soft."""
import base64
import os
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from fastapi import FastAPI, HTTPException, Request
from scipy.signal import resample_poly

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "Wav2Lip"))
import audio as w2l_audio  # noqa: E402
from models import Wav2Lip  # noqa: E402

DATA = Path(os.environ.get("MIRAGE_DATA", HERE.parent / "data"))
DEVICE = os.environ.get("MIRAGE_LIPSYNC_DEVICE") or ("mps" if torch.backends.mps.is_available() else "cpu")
FPS = 25.0
IDLE_SECONDS = 3.0
JPEG_Q = 72

app = FastAPI(title="Mirage lipsync")
_lock = threading.Lock()  # one GPU inference at a time
_model = None
_bases: dict = {}


def model():
    global _model
    if _model is None:
        m = Wav2Lip()
        sd = torch.load(HERE / "Wav2Lip" / "checkpoints" / "wav2lip_gan.pth", map_location="cpu", weights_only=False)["state_dict"]
        m.load_state_dict({k.replace("module.", ""): v for k, v in sd.items()})
        _model = m.to(DEVICE).eval()
    return _model


def _lowest_motion_window(frames, box, win):
    x, y, w, h = box
    diffs, prev = [0.0], None
    for f in frames:
        m = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)[y + int(h * .62):y + h, x + int(w * .2):x + int(w * .8)].astype(np.float32)
        if prev is not None:
            diffs.append(float(np.abs(m - prev).mean()))
        prev = m
    d = np.array(diffs)
    best = min(range(0, max(1, len(d) - win), 5), key=lambda s: d[s:s + win].mean())
    return best


def base_for(rid: str) -> dict:
    if rid in _bases:
        return _bases[rid]
    src = DATA / "replicas" / rid / "source.mp4"
    if not src.exists():
        raise HTTPException(404, f"no source video for replica {rid}")
    cap = cv2.VideoCapture(str(src))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, round(src_fps / FPS))
    frames, i = [], 0
    while len(frames) < int(25 * FPS):  # first 25 s is plenty to find a calm window
        ok, f = cap.read()
        if not ok:
            break
        if i % step == 0:
            if f.shape[1] > 720:
                f = cv2.resize(f, (720, int(f.shape[0] * 720 / f.shape[1])))
            frames.append(f)
        i += 1
    if len(frames) < 25:
        raise HTTPException(422, "source video too short")
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = det.detectMultiScale(cv2.cvtColor(frames[len(frames) // 2], cv2.COLOR_BGR2GRAY), 1.1, 6, minSize=(60, 60))
    if len(faces) == 0:
        raise HTTPException(422, "no face found in source video")
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    win = int(IDLE_SECONDS * FPS)
    s = _lowest_motion_window(frames, (x, y, w, h), win)
    clip = frames[s:s + win]
    pad = int(0.18 * h)
    H, W = clip[0].shape[:2]
    box = (max(0, y - pad // 4), min(H, y + h + pad), max(0, x - pad // 2), min(W, x + w + pad // 2))
    y1, y2, x1, x2 = box
    ch, cw = y2 - y1, x2 - x1
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    mask = np.clip(1.0 - (((xx - cw * .5) / (cw * .40)) ** 2 + ((yy - ch * .74) / (ch * .30)) ** 2), 0, 1)
    mask = (cv2.GaussianBlur(mask, (0, 0), sigmaX=cw * 0.03)[:, :, None]) ** 0.6
    _bases[rid] = {"clip": clip, "box": box, "mask": mask, "cursor": 0}
    return _bases[rid]


def jpeg(f) -> str:
    ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    return base64.b64encode(buf.tobytes()).decode()


def composite(base_frame, gen96, b):
    y1, y2, x1, x2 = b["box"]
    ch, cw = y2 - y1, x2 - x1
    gen = cv2.resize(gen96, (cw, ch), interpolation=cv2.INTER_LANCZOS4).astype(np.float32)
    fr = base_frame.copy()
    reg = fr[y1:y2, x1:x2].astype(np.float32)
    zy = slice(int(ch * 0.55), ch)
    gm, gs = gen[zy].mean((0, 1)), gen[zy].std((0, 1)) + 1e-3
    rm, rs = reg[zy].mean((0, 1)), reg[zy].std((0, 1)) + 1e-3
    gen = np.clip((gen - gm) / gs * rs + rm, 0, 255)
    gen = np.clip(gen + 0.5 * (gen - cv2.GaussianBlur(gen, (0, 0), 1.4)), 0, 255)  # gentle unsharp
    fr[y1:y2, x1:x2] = (gen * b["mask"] + reg * (1 - b["mask"])).astype(np.uint8)
    return fr


@app.get("/health")
def health():
    return {"ok": True, "device": DEVICE, "loaded": _model is not None, "replicas": list(_bases)}


@app.post("/idle/{rid}")
def idle(rid: str):
    b = base_for(rid)
    return {"fps": FPS, "frames": [jpeg(f) for f in b["clip"]]}


@app.post("/render/{rid}")
async def render(rid: str, request: Request):
    pcm = await request.body()
    t0 = time.time()
    b = base_for(rid)
    a24 = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if len(a24) < 2400:
        return {"fps": FPS, "frames": [], "ms": 0}
    a16 = resample_poly(a24, 2, 3).astype(np.float32)
    mel = w2l_audio.melspectrogram(a16)
    n = int(len(a16) / 16000 * FPS)
    mult = 80.0 / FPS
    chunks = []
    for i in range(n):
        s = int(i * mult)
        chunks.append(mel[:, -16:] if s + 16 > mel.shape[1] else mel[:, s:s + 16])
    clip, (y1, y2, x1, x2) = b["clip"], b["box"]
    L = len(clip)
    seq = []  # ping-pong cursor over the calm base clip, continuing between requests
    for _ in range(n):
        c = b["cursor"] % (2 * L - 2)
        seq.append(c if c < L else 2 * L - 2 - c)
        b["cursor"] += 1
    outs = []
    with _lock:
        m = model()
        for b0 in range(0, n, 32):
            idx = range(b0, min(b0 + 32, n))
            imgs = np.asarray([cv2.resize(clip[seq[i]][y1:y2, x1:x2], (96, 96)) for i in idx])
            masked = imgs.copy(); masked[:, 48:] = 0
            inp = np.concatenate((masked, imgs), axis=3) / 255.0
            mel_b = np.asarray([chunks[i] for i in idx]).reshape(len(imgs), 80, 16, 1)
            it = torch.FloatTensor(np.transpose(inp, (0, 3, 1, 2))).to(DEVICE)
            mt = torch.FloatTensor(np.transpose(mel_b, (0, 3, 1, 2))).to(DEVICE)
            with torch.no_grad():
                pred = m(mt, it)
            outs.extend((pred.cpu().numpy().transpose(0, 2, 3, 1) * 255.0).astype(np.uint8))
    frames = [jpeg(composite(clip[seq[i]], outs[i], b)) for i in range(n)]
    return {"fps": FPS, "frames": frames, "ms": int((time.time() - t0) * 1000)}
