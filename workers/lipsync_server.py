"""Live lip-sync service. Runs in workers/.venv (or workers/.venv-face with mediapipe), on Apple MPS, CUDA or CPU.

  uvicorn lipsync_server:app --port 8100     (from the workers/ directory)

POST /idle/{replica_id}            -> {"fps", "frames": [b64 jpeg...], "w", "h"}   idle loop (client ping-pongs it)
POST /render/{replica_id}          body = raw int16 mono 24 kHz PCM -> {"fps", "frames": [...], "ms", "start_phase", "end_phase"}
                                   query: ?phase=<n> start from this ping-pong position (continuity with the client's
                                          idle loop); ?fade_in=1 force a soft entry from the idle face.
POST /prepare/{replica_id}         warm base clip + model (cold ~5-10 s, then cached on disk)
POST /invalidate/{replica_id}      drop the cached base (called by the backend after a listening clip upload)
GET  /health                       device, engine, models, perf stats, per-replica base info

Env: VOCALFACE_DATA, VOCALFACE_LIPSYNC_DEVICE=auto|cuda|mps|cpu, VOCALFACE_LIPSYNC_ENGINE=auto|viseme|musetalk|wav2lip,
     VOCALFACE_COMMERCIAL_ONLY=1 (refuse research-licensed engines/weights, see engines/ and docs/LICENSES.md), VOCALFACE_JPEG_Q,
     VOCALFACE_IDLE_SECONDS, VOCALFACE_FACE_TRACK=1|0 (mediapipe tracking), VOCALFACE_LOG_LEVEL.

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
import lipsync_sched as sched  # noqa: E402
import engines as eng_registry  # noqa: E402
import face_render as fr  # noqa: E402
import facelib as fl  # noqa: E402

logging.basicConfig(level=os.environ.get("VOCALFACE_LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("vocalface.lipsync")

DATA = Path(os.environ.get("VOCALFACE_DATA", HERE.parent / "data"))
DEVICE = fr.pick_device()
COMMERCIAL_ONLY = eng_registry.commercial_only()
ENGINE_ERROR = None   # set when the requested engine is refused (commercial-only) or nothing usable exists
try:
    ENGINE_NAME = eng_registry.resolve()
except (eng_registry.CommercialOnlyError, eng_registry.EngineUnavailable, KeyError) as _e:
    ENGINE_NAME, ENGINE_ERROR = os.environ.get("VOCALFACE_LIPSYNC_ENGINE", "auto").lower(), str(_e)
    logging.getLogger("vocalface.lipsync").error("lip-sync engine refused: %s", _e)
FPS = fr.FPS
JPEG_Q = int(os.environ.get("VOCALFACE_JPEG_Q", "80"))
FADE_FRAMES = 5          # soft mouth entry/exit so speaking segments do not pop against the idle loop
CONTINUOUS_S = 1.2       # a request starting within this long after the previous one ended counts as continuous speech
SHARPEN = float(os.environ.get("VOCALFACE_SHARPEN", "0.6"))

app = FastAPI(title="VocalFace lipsync")
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
        if os.environ.get("VOCALFACE_FACE_TRACK", "1") != "0":
            try:
                t0 = time.time()
                _tracker = fl.FaceTracker()
                _load_s["tracker"] = round(time.time() - t0, 2)
            except Exception as e:  # mediapipe missing -> Haar fixed box
                log.warning("mediapipe tracker unavailable (%s: %s); falling back to a fixed Haar box", type(e).__name__, e)
    return _tracker


def engine():
    global _engine
    if ENGINE_ERROR:
        raise HTTPException(503, f"lip-sync engine unavailable: {ENGINE_ERROR}")
    if _engine is None:
        t0 = time.time()
        try:
            _engine = eng_registry.create(ENGINE_NAME, DEVICE)
        except eng_registry.CommercialOnlyError as e:
            raise HTTPException(503, str(e))
        except eng_registry.EngineUnavailable as e:
            raise HTTPException(501, f"engine {ENGINE_NAME} unavailable: {e}")
        _engine.warmup()
        _load_s["engine"] = round(time.time() - t0, 2)
        log.info("engine %s loaded on %s in %.1fs", ENGINE_NAME, DEVICE, _load_s["engine"])
    return _engine


_status_cache = {}


def engines_status():
    if "s" not in _status_cache:
        _status_cache["s"] = eng_registry.status(COMMERCIAL_ONLY)
    return _status_cache["s"]


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


_build_locks: dict = {}
_build_locks_guard = threading.Lock()


def base_for(rid: str) -> fr.Base:
    """Per-replica build lock: concurrent first requests (prepare + idle + renders of several sessions) build the base
    once instead of N times in parallel."""
    with _build_locks_guard:
        lk = _build_locks.setdefault(rid, threading.Lock())
    with lk:
        return _base_for(rid)


def _base_for(rid: str) -> fr.Base:
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
    if os.environ.get("VOCALFACE_LIPSYNC_PRELOAD", "1") == "0":
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
    return {"ok": True, "device": DEVICE, "engine": ENGINE_NAME, "engine_error": ENGINE_ERROR,
            "engine_licence": None if ENGINE_ERROR else eng_registry.engine_class(ENGINE_NAME).licence.to_dict(),
            "commercial_only": COMMERCIAL_ONLY, "disabled_engines": engines_status()["disabled"],
            "engines": {k: {"disabled_by_policy": v["disabled_by_policy"], "available": v["available"],
                            "commercial": v["licence"]["commercial"]} for k, v in engines_status()["engines"].items()},
            "model": None if ENGINE_ERROR else eng_registry.engine_class(ENGINE_NAME).description, "loaded": _engine is not None,
            "torch": torch.__version__, "cuda": torch.cuda.is_available(), "mps": bool(torch.backends.mps.is_available()),
            "tracker": "mediapipe" if _tracker else ("haar" if _tracker_tried else "not loaded yet"), "load_s": _load_s,
            "replicas": {k: {"frames": len(v.frames), "crop": v.info.get("crop"), "listening": v.info.get("listening"),
                             "tracker": v.info.get("tracker"), "overlays": len(v.info.get("overlays", [])),
                             "background": (v.info.get("background") or {}).get("type")}
                         for k, v in _bases.items()},
            "perf": agg, "queue": sched.SCHED.stats()}


@app.post("/invalidate/{rid}")
def invalidate(rid: str):
    _bases.pop(rid, None)
    _last_end.pop(rid, None)
    for k in [k for k in _cursors if k[0] == rid]:
        _cursors.pop(k, None)
        _last_end.pop(k, None)
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
async def render(rid: str, request: Request, phase: int | None = None, fade_in: int | None = None,
                 sid: str = "", piece: int | None = None, deadline_s: float = 0.0):
    """sid = conversation/session id (fair sharing + per-session ping-pong cursor), piece = index of this piece in the
    current reply (0 = the one that gates the first lip-synced frame: scheduled first), deadline_s = drop if queued longer.
    The work itself runs on the scheduler's lane threads (lipsync_sched.py), never on the event loop."""
    pcm = await request.body()
    if ENGINE_ERROR:
        raise HTTPException(503, f"lip-sync engine unavailable: {ENGINE_ERROR}")
    return await sched.SCHED.submit(request, sid or rid, piece, lambda: _render_sync(rid, pcm, phase, fade_in, sid or rid), deadline_s)


_state_lock = threading.Lock()
_cursors: dict = {}  # (rid, sid) -> (ping-pong cursor, last used): sessions of one replica must not share a cursor


def _render_sync(rid: str, pcm: bytes, phase: int | None, fade_in: int | None, sid: str) -> dict:
    t_start = time.time()
    b = base_for(rid)
    a24 = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if len(a24) < 2400:
        return {"fps": FPS, "frames": [], "ms": 0}
    a16 = resample_poly(a24, 2, 3).astype(np.float32)
    n = int(len(a16) / 16000 * FPS)
    now = time.monotonic()
    key = (rid, sid)
    L = len(b.frames)
    with _state_lock:
        continuous = (now - _last_end.get(key, -1e9)) < CONTINUOUS_S and not fade_in
        _last_end[key] = max(now, _last_end.get(key, 0)) + n / FPS
        cur = _cursors.get(key, (b.cursor, 0))[0]
        start_phase = (cur if phase is None else phase) % max(1, 2 * L - 2)
        seq = fr.pingpong_seq(b, n, advance=True, start=start_phase)
        end_phase = int(b.cursor)  # set by pingpong_seq just above (same lock)
        _cursors[key] = (end_phase, now)
        if len(_cursors) > 64:  # sessions that ended: forget them (no unbounded growth)
            for k in [k for k, (_, t) in _cursors.items() if now - t > 300]:
                _cursors.pop(k, None)
                _last_end.pop(k, None)
    t_inf = time.time()
    with _lock:
        eng = engine()
        chunks = eng.mel_chunks(a16)
        outs = eng.generate(b, seq, chunks)
        fr.sync(DEVICE)
    infer_ms = (time.time() - t_inf) * 1000
    t_c = time.time()
    frames = []
    sharp = SHARPEN if getattr(eng, "paste_sharpen", None) is None else eng.paste_sharpen
    sa = fr.speech_alpha(a16, n)
    for i in range(n):
        alpha = float(sa[i])
        if not continuous and i < FADE_FRAMES:
            alpha = min(alpha, (i + 1) / (FADE_FRAMES + 1))  # soft entry from idle face (first piece of a reply)
        frames.append(jpeg(fr.paste(b, seq[i], outs[i], sharpen=sharp, alpha=alpha)))
    comp_ms = (time.time() - t_c) * 1000
    total = (time.time() - t_start) * 1000
    _stats.append({"frames": n, "infer_ms": infer_ms, "comp_ms": comp_ms, "total_ms": total})
    log.info("render %s: %d frames, infer %.0f ms (%.0f fps), composite+jpeg %.0f ms, total %.0f ms, continuous=%s",
             rid, n, infer_ms, n / max(infer_ms / 1000, 1e-6), comp_ms, total, continuous)
    return {"fps": FPS, "frames": frames, "ms": int(total), "start_phase": int(start_phase),
            "end_phase": end_phase, "loop_len": L}
