"""Live lip-sync service. Runs in workers/.venv (or workers/.venv-face with mediapipe), on Apple MPS, CUDA or CPU.

  uvicorn lipsync_server:app --port 8100     (from the workers/ directory)

POST /idle/{replica_id}            -> {"fps", "frames": [b64 jpeg...], "w", "h"}   idle loop (client ping-pongs it)
POST /render/{replica_id}          body = raw int16 mono 24 kHz PCM -> {"fps", "frames": [...], "ms", "start_phase", "end_phase"}
                                   query: ?phase=<n> start from this ping-pong position (continuity with the client's
                                          idle loop); ?fade_in=1 force a soft entry from the idle face.
POST /prepare/{replica_id}         warm base clip + model (cold ~5-10 s, then cached on disk)
POST /invalidate/{replica_id}      drop the cached base (called by the backend after a listening clip upload)
GET  /health                       device, engine, models, perf stats, per-replica base info

Env: MIRAGE_DATA, MIRAGE_LIPSYNC_DEVICE=auto|cuda|mps|cpu, MIRAGE_LIPSYNC_ENGINE=wav2lip|musetalk, MIRAGE_JPEG_Q,
     MIRAGE_IDLE_SECONDS, MIRAGE_FACE_TRACK=1|0 (mediapipe tracking), MIRAGE_LOG_LEVEL.

The base video is the replica's listening clip when present (else the calmest window of the source video, found with
face-landmark jaw-open scores). Only the lower face is replaced, so head motion and blinking come from real footage.
Frames are face-centred crops with overlays (name labels) removed; the model box follows the head via smoothed
landmarks. Wav2Lip works at 96x96; MuseTalk (256 px) is the optional quality engine."""
from __future__ import annotations

import base64
import collections
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from scipy.signal import resample_poly

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import background as bg  # noqa: E402
import face_render as fr  # noqa: E402
import facelib as fl  # noqa: E402

logging.basicConfig(level=os.environ.get("MIRAGE_LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("mirage.lipsync")

DATA = Path(os.environ.get("MIRAGE_DATA", HERE.parent / "data"))
DEVICE = fr.pick_device()
ENGINE_NAME = os.environ.get("MIRAGE_LIPSYNC_ENGINE", "wav2lip").lower()
FPS = fr.FPS
JPEG_Q = int(os.environ.get("MIRAGE_JPEG_Q", "80"))
FADE_FRAMES = 5          # soft mouth entry/exit so speaking segments do not pop against the idle loop
CONTINUOUS_S = 1.2       # a request starting within this long after the previous one ended counts as continuous speech
SHARPEN = float(os.environ.get("MIRAGE_SHARPEN", "0.6"))

app = FastAPI(title="Mirage lipsync")
_lock = threading.Lock()  # one GPU inference at a time
_engine = None
_tracker = None
_tracker_tried = False
_bases: dict = {}
_last_end: dict = {}  # rid -> monotonic time the last rendered audio would finish playing
_stats = collections.deque(maxlen=50)  # recent renders: dict(frames, infer_ms, comp_ms, total_ms)
_load_s = {}


def tracker():
    global _tracker, _tracker_tried
    if not _tracker_tried:
        _tracker_tried = True
        if os.environ.get("MIRAGE_FACE_TRACK", "1") != "0":
            try:
                t0 = time.time()
                _tracker = fl.FaceTracker()
                _load_s["tracker"] = round(time.time() - t0, 2)
            except Exception as e:  # mediapipe missing -> Haar fixed box
                log.warning("mediapipe tracker unavailable (%s: %s); falling back to a fixed Haar box", type(e).__name__, e)
    return _tracker


def engine():
    global _engine
    if _engine is None:
        t0 = time.time()
        if ENGINE_NAME == "musetalk":
            raise HTTPException(501, "musetalk engine is offline/GPU only; use workers/render_offline.py --engine musetalk")
        _engine = fr.Wav2LipEngine(DEVICE)
        _engine.warmup()
        _load_s["engine"] = round(time.time() - t0, 2)
        log.info("engine %s loaded on %s in %.1fs", ENGINE_NAME, DEVICE, _load_s["engine"])
    return _engine


def _clip_mtime(rid: str) -> float:
    d = DATA / "replicas" / rid
    return max((p.stat().st_mtime for p in (d / "source.mp4", d / "listening.mp4", d / "background.json") if p.exists()), default=0.0)


def background_for(rid: str) -> dict | None:
    """Per-replica background spec written by the backend (POST /v1/replicas/{id}/background). Applied once to the base clip."""
    p = DATA / "replicas" / rid / "background.json"
    try:
        return bg.normalize(json.loads(p.read_text())) if p.exists() else None
    except Exception as e:  # noqa: BLE001 - a broken file must not take the replica down
        log.warning("ignoring bad background.json for %s: %s", rid, e)
        return None


def base_for(rid: str) -> fr.Base:
    b = _bases.get(rid)
    mt = _clip_mtime(rid)
    if b is not None and b.info.get("_mtime") == mt:
        return b
    rdir = DATA / "replicas" / rid
    if not (rdir / "source.mp4").exists() and not (rdir / "listening.mp4").exists():
        raise HTTPException(404, f"no source video for replica {rid}")
    t0 = time.time()
    try:
        b = fr.prepare_base(rdir, tracker(), background=background_for(rid))
    except ValueError as e:
        raise HTTPException(422, str(e))
    b.info["_mtime"] = mt
    log.info("base %s ready in %.1fs (%d frames, crop %s, tracker=%s, listening=%s, cached=%s, overlays=%d)", rid,
             time.time() - t0, len(b.frames), b.info["crop"], b.info["tracker"], b.info["listening"], b.info["cached"],
             len(b.info["overlays"]))
    _bases[rid] = b
    return b


def jpeg(f) -> str:
    ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    return base64.b64encode(buf.tobytes()).decode()


@app.on_event("startup")
def _preload():
    """Load + warm the model in the background so the first conversation does not wait for it."""
    if os.environ.get("MIRAGE_LIPSYNC_PRELOAD", "1") == "0":
        return

    def go():
        try:
            with _lock:
                engine()
                tracker()
        except Exception as e:
            log.warning("preload failed: %s", e)

    threading.Thread(target=go, daemon=True).start()


@app.get("/health")
def health():
    import torch

    recent = list(_stats)
    agg = None
    if recent:
        fr_n = sum(s["frames"] for s in recent)
        agg = {"renders": len(recent), "frames": fr_n,
               "infer_fps": round(fr_n / max(1e-6, sum(s["infer_ms"] for s in recent) / 1000), 1),
               "end_to_end_fps": round(fr_n / max(1e-6, sum(s["total_ms"] for s in recent) / 1000), 1),
               "avg_comp_ms_per_frame": round(sum(s["comp_ms"] for s in recent) / fr_n, 2)}
    return {"ok": True, "device": DEVICE, "engine": ENGINE_NAME, "model": "wav2lip_gan (96px)", "loaded": _engine is not None,
            "torch": torch.__version__, "cuda": torch.cuda.is_available(), "mps": bool(torch.backends.mps.is_available()),
            "tracker": "mediapipe" if _tracker else ("haar" if _tracker_tried else "not loaded yet"), "load_s": _load_s,
            "replicas": {k: {"frames": len(v.frames), "crop": v.info.get("crop"), "listening": v.info.get("listening"),
                             "tracker": v.info.get("tracker"), "overlays": len(v.info.get("overlays", [])),
                             "background": (v.info.get("background") or {}).get("type")}
                         for k, v in _bases.items()},
            "perf": agg}


@app.post("/invalidate/{rid}")
def invalidate(rid: str):
    _bases.pop(rid, None)
    _last_end.pop(rid, None)
    return {"ok": True}


@app.post("/prepare/{rid}")
def prepare(rid: str):
    """Build (or load) the base clip now instead of on the first /idle; also loads the model."""
    t0 = time.time()
    b = base_for(rid)
    with _lock:
        engine()
    return {"ok": True, "frames": len(b.frames), "listening": b.info["listening"], "cached": b.info["cached"],
            "tracker": b.info["tracker"], "overlays": len(b.info["overlays"]), "ms": int((time.time() - t0) * 1000)}


@app.post("/idle/{rid}")
def idle(rid: str):
    b = base_for(rid)
    w, h = b.size
    return {"fps": FPS, "w": w, "h": h, "frames": [jpeg(f) for f in b.frames]}


@app.post("/render/{rid}")
async def render(rid: str, request: Request, phase: int | None = None, fade_in: int | None = None):
    pcm = await request.body()
    t_start = time.time()
    b = base_for(rid)
    a24 = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if len(a24) < 2400:
        return {"fps": FPS, "frames": [], "ms": 0}
    a16 = resample_poly(a24, 2, 3).astype(np.float32)
    n = int(len(a16) / 16000 * FPS)
    now = time.monotonic()
    continuous = (now - _last_end.get(rid, -1e9)) < CONTINUOUS_S and not fade_in
    _last_end[rid] = max(now, _last_end.get(rid, 0)) + n / FPS
    L = len(b.frames)
    start_phase = (b.cursor if phase is None else phase) % max(1, 2 * L - 2)
    seq = fr.pingpong_seq(b, n, advance=True, start=start_phase)
    t_inf = time.time()
    with _lock:
        eng = engine()
        chunks = eng.mel_chunks(a16)
        outs = eng.generate(b, seq, chunks)
        fr.sync(DEVICE)
    infer_ms = (time.time() - t_inf) * 1000
    t_c = time.time()
    frames = []
    sa = fr.speech_alpha(a16, n)
    for i in range(n):
        alpha = float(sa[i])
        if not continuous and i < FADE_FRAMES:
            alpha = min(alpha, (i + 1) / (FADE_FRAMES + 1))  # soft entry from idle face (first piece of a reply)
        frames.append(jpeg(fr.paste(b, seq[i], outs[i], sharpen=SHARPEN, alpha=alpha)))
    comp_ms = (time.time() - t_c) * 1000
    total = (time.time() - t_start) * 1000
    _stats.append({"frames": n, "infer_ms": infer_ms, "comp_ms": comp_ms, "total_ms": total})
    log.info("render %s: %d frames, infer %.0f ms (%.0f fps), composite+jpeg %.0f ms, total %.0f ms, continuous=%s",
             rid, n, infer_ms, n / max(infer_ms / 1000, 1e-6), comp_ms, total, continuous)
    return {"fps": FPS, "frames": frames, "ms": int(total), "start_phase": int(start_phase),
            "end_phase": int(b.cursor), "loop_len": L}
