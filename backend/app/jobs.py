"""SQLite-backed job queue + processors for replica training and video generation.

Queue model: the source of truth is the existing rows (Replica.status=='training',
Video.status=='queued'). Workers claim a row by inserting a JobClaim (PK '<kind>:<id>');
the PK makes the claim atomic across processes. Stale claims (worker died) are re-taken
after STALE_SECONDS, up to MAX_ATTEMPTS.

All heavy steps are injectable (`Deps`) so tests use fakes. Defaults:
  * ffmpeg for audio/face extraction plumbing
  * workers/extract_face.py   (workers/.venv, OpenCV Haar scoring of sampled frames)
  * Kokoro TTS (kokoro-onnx, in the backend venv)
  * workers/render_liveportrait.py (workers/.venv, LivePortrait on MPS/CUDA)

HONESTY: LivePortrait is not audio-driven. workers/audio_driver.py adds a cheap amplitude-driven
mouth (open/close follows loudness). It is NOT phoneme-accurate lip-sync. Voice cloning is NOT
done; see VoiceProvider below.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from . import db
from .db import Replica, Video
from .models_extra import JobClaim, VideoMeta, ensure_tables

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("MIRAGE_DATA", ROOT / "data"))
WORKERS_DIR = ROOT / "workers"
WORKERS_PY = WORKERS_DIR / ".venv/bin/python"
MODELS = ROOT / "models"
STALE_SECONDS = 3600
MAX_ATTEMPTS = 2
FILE_URL_PREFIX = "/v1/files"  # served by routers/jobs_api.py


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _signed_hook_url(url):
    """Webhook receivers cannot send x-api-key: give them a 24 h signed link (safety-infra)."""
    if not url:
        return url
    from . import signing
    return signing.sign_path(url, ttl=86400)


def replica_dir(rid: str) -> Path:
    return DATA_DIR / "replicas" / rid


def video_path(vid: str) -> Path:
    return DATA_DIR / "videos" / f"{vid}.mp4"


# ---------------- voice provider interface ----------------


class VoiceProvider(Protocol):
    """Turns text into speech wav in a replica's voice.

    Default (KokoroPresetVoice) uses a preset Kokoro voice and ignores the reference clip, i.e.
    NO cloning. replicas/<id>/voice_ref.wav is stored so a cloning provider can be dropped in.
    Free models that could be plugged in (all need torch; best on GPU):
      * Chatterbox (Resemble AI, MIT) - zero-shot clone from ~10 s, good quality
      * OpenVoice v2 (MIT) - tone-colour conversion on top of any TTS incl. Kokoro output
      * F5-TTS (code MIT, weights CC-BY-NC) - zero-shot clone
      * XTTS-v2 (Coqui CPML, non-commercial) - 6 s reference
    Cheapest upgrade path here: keep Kokoro for speech, run OpenVoice v2 tone conversion with voice_ref.wav.
    """

    def synthesize(self, text: str, out_wav: Path, voice_ref: Optional[Path], voice: str) -> None: ...


class KokoroPresetVoice:
    def __init__(self):
        self._k = None

    def synthesize(self, text, out_wav, voice_ref, voice="default"):
        import numpy as np
        from kokoro_onnx import Kokoro

        if self._k is None:
            self._k = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
        from .languages import lang_for_voice

        v = "af_heart" if voice in ("default", "") else voice
        samples, sr = self._k.create(text, voice=v, speed=1.0, lang=lang_for_voice(v))  # es/fr/hi/... voices use their espeak lang
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        with wave.open(str(out_wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm)


# ---------------- default heavy-step implementations ----------------


def run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def fetch_video(url: str, dest: Path) -> None:
    """http(s) download, file:// URL, or local path copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith(("http://", "https://")):
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    else:
        src = Path(url[7:] if url.startswith("file://") else url).expanduser()
        if not src.is_file():
            raise FileNotFoundError(f"training video not found: {url}")
        shutil.copyfile(src, dest)


def ffmpeg_extract_audio(video: Path, out_wav: Path, max_seconds: int = 30) -> bool:
    r = run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "24000",
             "-t", str(max_seconds), str(out_wav)], timeout=300)
    return r.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 4096


def default_face_extractor(video: Path, out_png: Path) -> dict:
    r = run([str(WORKERS_PY), str(WORKERS_DIR / "extract_face.py"), str(video), str(out_png)], timeout=600)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": (r.stderr or r.stdout)[-400:] or "face extractor failed"}


def default_renderer(image: Path, wav: Path, out_mp4: Path, fps: float) -> dict:
    r = run([str(WORKERS_PY), str(WORKERS_DIR / "render_liveportrait.py"), "--image", str(image),
             "--audio", str(wav), "--out", str(out_mp4), "--fps", str(fps)], timeout=6 * 3600)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": (r.stderr or r.stdout)[-400:] or "renderer failed"}


def default_webhook(url: str, payload: dict) -> str:
    try:
        r = httpx.post(url, json=payload, timeout=15)
        return "delivered" if r.status_code < 300 else f"failed: HTTP {r.status_code}"
    except Exception as e:
        return f"failed: {e}"


@dataclass
class Deps:
    fetch: Callable[[str, Path], None] = fetch_video
    extract_audio: Callable[[Path, Path], bool] = ffmpeg_extract_audio
    extract_face: Callable[[Path, Path], dict] = default_face_extractor
    voice: VoiceProvider = field(default_factory=KokoroPresetVoice)
    render: Callable[[Path, Path, Path, float], dict] = default_renderer
    webhook: Callable[[str, dict], str] = default_webhook
    render_fps: float = float(os.environ.get("MIRAGE_RENDER_FPS", 12))


# ---------------- queue ----------------


def _claim(s: Session, kind: str, ref: str) -> Optional[JobClaim]:
    key = f"{kind}:{ref}"
    existing = s.get(JobClaim, key)
    if existing is None:
        c = JobClaim(key=key, kind=kind, ref_id=ref)
        s.add(c)
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            return None
        return c
    locked = existing.locked_at if existing.locked_at.tzinfo else existing.locked_at.replace(tzinfo=timezone.utc)
    if existing.finished_at is None and existing.attempts < MAX_ATTEMPTS and _now() - locked > timedelta(seconds=STALE_SECONDS):
        existing.attempts += 1
        existing.locked_at = _now()
        s.add(existing); s.commit()
        return existing
    return None


def claim_next(s: Session) -> Optional[tuple[str, str]]:
    """Return ('replica'|'video', id) for a claimed job, or None. Replicas first (videos depend on them)."""
    for r in s.exec(select(Replica).where(Replica.status.in_(("training", "awaiting_consent"))).order_by(Replica.created_at)).all():
        from .safety import has_consent

        if not has_consent(s, r.id):  # consent gate: never train a likeness without recorded consent
            if r.status != "awaiting_consent":
                r.status = "awaiting_consent"; s.add(r); s.commit()
            continue
        if _claim(s, "replica", r.id):
            return "replica", r.id
    # 'rendering' rows are included so a crashed worker's video gets retried once its claim goes stale.
    for v in s.exec(select(Video).where(Video.status.in_(["queued", "rendering"])).order_by(Video.created_at)).all():
        if _claim(s, "video", v.id):
            return "video", v.id
    return None


def _finish(s: Session, kind: str, ref: str, error: Optional[str], detail: dict) -> None:
    c = s.get(JobClaim, f"{kind}:{ref}")
    c.finished_at, c.error, c.detail = _now(), error, json.dumps(detail)
    s.add(c); s.commit()


# ---------------- processors ----------------


def _prewarm_face(rid: str) -> None:
    """Best-effort: ask the live lip-sync service to prepare this replica's base clip now (face tracking, overlay
    crop; ~8 s cold) so the first conversation does not pay for it. Never fails the replica job."""
    import urllib.request

    url = os.environ.get("MIRAGE_LIPSYNC_URL", "http://localhost:8100").rstrip("/")
    try:
        req = urllib.request.Request(f"{url}/prepare/{rid}", method="POST", data=b"")
        urllib.request.urlopen(req, timeout=120).read()
    except Exception:  # noqa: BLE001 - service may be down or not configured
        pass


def process_replica(rid: str, deps: Deps) -> bool:
    t0, timings, notes = time.time(), {}, []
    with Session(db.engine) as s:
        rep = s.get(Replica, rid)
        d = replica_dir(rid)
        err = None
        try:
            d.mkdir(parents=True, exist_ok=True)
            src = d / "source.mp4"
            t = time.time(); deps.fetch(rep.train_video_url, src); timings["fetch"] = round(time.time() - t, 2)
            t = time.time()
            has_voice = deps.extract_audio(src, d / "voice_ref.wav"); timings["audio"] = round(time.time() - t, 2)
            if not has_voice:
                notes.append("no usable audio track: replica has no voice reference (preset voice will be used)")
            t = time.time(); face = deps.extract_face(src, d / "face.png"); timings["face"] = round(time.time() - t, 2)
            if not face.get("ok"):
                raise RuntimeError(face.get("error", "face extraction failed"))
            (d / "meta.json").write_text(json.dumps({"face": face, "has_voice_ref": has_voice, "notes": notes}))
        except Exception as e:  # noqa: BLE001 - surface any failure on the row
            err = f"{type(e).__name__}: {e}"
        rep.status = "error" if err else "ready"
        s.add(rep); s.commit()
        timings["total"] = round(time.time() - t0, 2)
        _finish(s, "replica", rid, err, {"timings": timings, "notes": notes})
        from .events import on_replica_finished

        on_replica_finished(rid)
        if not err:
            _prewarm_face(rid)
        return err is None


def process_video(vid: str, deps: Deps) -> bool:
    t0, timings = time.time(), {}
    with Session(db.engine) as s:
        v = s.get(Video, vid)
        meta = s.get(VideoMeta, vid)
        v.status = "rendering"
        s.add(v); s.commit()
        err = None
        try:
            from .safety import moderate_or_raise

            moderate_or_raise(v.script)  # defense in depth: re-check even though the API checked at creation
            rep = s.get(Replica, v.replica_id)
            if rep is None or rep.status != "ready":
                raise RuntimeError("replica not ready")
            rd = replica_dir(rep.id)
            face = rd / "face.png"
            if not face.exists():
                raise RuntimeError("replica face asset missing")
            wav = DATA_DIR / "videos" / f"{vid}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            ref = rd / "voice_ref.wav"
            t = time.time()
            deps.voice.synthesize(v.script, wav, ref if ref.exists() else None, meta.voice if meta else "default")
            timings["tts"] = round(time.time() - t, 2)
            t = time.time()
            res = deps.render(face, wav, video_path(vid), deps.render_fps)
            timings["render"] = round(time.time() - t, 2)
            if not res.get("ok") or not video_path(vid).exists():
                raise RuntimeError(res.get("error", "render failed"))
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        (DATA_DIR / "videos" / f"{vid}.wav").unlink(missing_ok=True)
        v.status = "error" if err else "ready"
        v.output_url = None if err else f"{FILE_URL_PREFIX}/videos/{vid}.mp4"
        s.add(v); s.commit()
        timings["total"] = round(time.time() - t0, 2)
        _finish(s, "video", vid, err, {"timings": timings})
        from .events import on_video_finished

        on_video_finished(vid, err)
        if meta and meta.callback_url:
            meta.webhook_status = deps.webhook(meta.callback_url, {
                "event": "video.ready" if not err else "video.error", "video_id": vid,
                "status": v.status, "error": err,
                "output_url": _signed_hook_url(v.output_url)})
            s.add(meta); s.commit()
        return err is None


def run_once(deps: Optional[Deps] = None) -> Optional[tuple[str, str, bool]]:
    """Claim and process a single job. Returns (kind, id, ok) or None if the queue is empty."""
    ensure_tables(db.engine)
    deps = deps or Deps()
    with Session(db.engine) as s:
        job = claim_next(s)
    if job is None:
        return None
    kind, ref = job
    ok = process_replica(ref, deps) if kind == "replica" else process_video(ref, deps)
    return kind, ref, ok
