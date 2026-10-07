"""Worker-side logic for the creative features (called from jobs.py hooks; heavy steps are subprocesses in workers/.venv*).

* photo replicas: photo -> validated -> LivePortrait idle clip (workers/photo_idle.py, workers/.venv)
* creative videos: scenes -> TTS per scene -> (Whisper word timing) -> workers/creative_render.py (workers/.venv-face):
  Wav2Lip lip-sync + background + captions + format + logo + transitions + thumbnail.

Every heavy step is a module-level function (`run_photo_check`, `run_photo_idle`, `transcribe_words`, `run_renderer`) so tests
monkeypatch them; nothing here needs a GPU model at import time."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session

from . import db
from .db import Replica, Video
from .models_creative import PhotoReplica, ReplicaBackground, VideoOptions
from .models_extra import JobClaim, VideoMeta, ensure_tables

ROOT = Path(__file__).resolve().parents[2]
WORKERS = ROOT / "workers"
FACE_PY = WORKERS / ".venv-face/bin/python"   # mediapipe + torch (lip-sync, segmentation, captions)
LP_PY = WORKERS / ".venv/bin/python"          # LivePortrait
MAX_PHOTO_BYTES = 25 * 1024 * 1024


def _jobs():
    from . import jobs  # late import: jobs imports this module

    return jobs


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def is_photo_replica(rid: str) -> bool:
    ensure_tables(db.engine)
    with Session(db.engine) as s:
        return s.get(PhotoReplica, rid) is not None


def replica_background(s: Session, rid: str) -> Optional[dict]:
    row = s.get(ReplicaBackground, rid)
    try:
        spec = json.loads(row.spec) if row else None
    except ValueError:
        spec = None
    return spec or None


def use_creative(s: Session, vid: str, rep: Replica) -> bool:
    """Route a video through the Wav2Lip creative renderer?  Yes when it has render options, the replica is a photo replica,
    the replica has a default background, or VOCALFACE_VIDEO_ENGINE=wav2lip. Plain videos keep the legacy path."""
    return (s.get(VideoOptions, vid) is not None or s.get(PhotoReplica, rep.id) is not None
            or replica_background(s, rep.id) is not None or os.environ.get("VOCALFACE_VIDEO_ENGINE", "").lower() == "wav2lip")


# ---------------------------------------------------------------- heavy steps (monkeypatched in tests)
def _last_json(out: str) -> dict:
    for line in reversed((out or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("no json in output")


def run_photo_check(image: Path, out_dir: Path) -> dict:
    r = subprocess.run([str(FACE_PY), str(WORKERS / "photo_check.py"), "--image", str(image), "--out-dir", str(out_dir)],
                       capture_output=True, text=True, timeout=300)
    try:
        return _last_json(r.stdout)
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": (r.stderr or r.stdout)[-300:] or "photo check failed"}


def run_photo_idle(photo: Path, out_mp4: Path, seconds: float, head: float) -> dict:
    from .jobs import flashhead_ready  # SoulX-FlashHead first (licence-clean); LivePortrait only as the research-licensed fallback

    if flashhead_ready():
        r = subprocess.run([str(WORKERS / ".venv-flash/bin/python"), str(WORKERS / "photo_idle_flashhead.py"), "--image", str(photo),
                            "--out", str(out_mp4), "--seconds", str(seconds)], capture_output=True, text=True, timeout=3 * 3600)
        try:
            res = _last_json(r.stdout)
        except Exception:  # noqa: BLE001
            res = {"ok": False, "error": (r.stderr or r.stdout)[-400:] or "flashhead idle failed"}
        if res.get("ok") or os.environ.get("VOCALFACE_VIDEO_RENDERER", "auto").lower() == "flashhead":
            return res
    r = subprocess.run([str(LP_PY), str(WORKERS / "photo_idle.py"), "--image", str(photo), "--out", str(out_mp4),
                        "--seconds", str(seconds), "--head", str(head)], capture_output=True, text=True, timeout=3 * 3600,
                       env=dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK="1"))
    try:
        return _last_json(r.stdout)
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": (r.stderr or r.stdout)[-400:] or "photo animation failed"}


_whisper = None


def transcribe_words(wav: Path, language: Optional[str] = None) -> Optional[list[dict]]:
    """Whisper word timestamps for caption timing: [{"w","s","e"}] or None when Whisper is unavailable (captions then fall back
    to energy-gated proportional timing)."""
    global _whisper
    try:
        import numpy as np

        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
        audio = np.frombuffer(raw, np.float32).copy()
        if _whisper is None:
            from faster_whisper import WhisperModel

            _whisper = WhisperModel(os.environ.get("VOCALFACE_CAPTION_WHISPER", "base"), device="cpu", compute_type="int8")
        segs, _ = _whisper.transcribe(audio, language=language, word_timestamps=True)
        return [{"w": w.word.strip(), "s": float(w.start), "e": float(w.end)} for sg in segs for w in (sg.words or []) if w.word.strip()]
    except Exception:  # noqa: BLE001
        return None


def run_renderer(spec: dict, on_progress: Callable[[dict], None], workdir: Path) -> dict:
    """Launch workers/creative_render.py, stream its PROGRESS lines. -> RESULT dict (raises RuntimeError with the worker's message)."""
    spec_path = workdir / "render_spec.json"
    spec_path.write_text(json.dumps(spec))
    p = subprocess.Popen([str(FACE_PY), str(WORKERS / "creative_render.py"), str(spec_path)], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK="1"))
    result, err = None, None
    assert p.stdout is not None
    for line in p.stdout:
        if line.startswith("PROGRESS "):
            try:
                on_progress(json.loads(line[9:]))
            except Exception:  # noqa: BLE001
                pass
        elif line.startswith("RESULT "):
            result = json.loads(line[7:])
        elif line.startswith("ERROR "):
            err = line[6:].strip()
    stderr = p.stderr.read() if p.stderr else ""
    if p.wait() != 0 or result is None:
        raise RuntimeError(err or stderr[-400:] or "creative renderer failed")
    return result


# ---------------------------------------------------------------- photo replica
def process_photo_replica(rid: str, deps) -> bool:
    """Same contract as jobs.process_replica for photo replicas: fetch photo -> check -> animate -> ready."""
    jobs = _jobs()
    t0, timings, notes = time.time(), {}, []
    with Session(db.engine) as s:
        rep = s.get(Replica, rid)
        pr = s.get(PhotoReplica, rid)
        d = jobs.replica_dir(rid)
        err = None
        try:
            d.mkdir(parents=True, exist_ok=True)
            raw = d / "photo_src"
            t = time.time(); deps.fetch(rep.train_video_url, raw); timings["fetch"] = round(time.time() - t, 2)
            if raw.stat().st_size > MAX_PHOTO_BYTES:
                raise RuntimeError("photo is larger than 25 MB")
            work = d / "photo_work"
            t = time.time(); chk = run_photo_check(raw, work); timings["check"] = round(time.time() - t, 2)
            if not chk.get("ok"):
                raise RuntimeError(chk.get("error", "photo rejected"))
            notes += list(chk.get("warnings", []))
            shutil.copyfile(work / "face.png", d / "face.png")
            shutil.copyfile(work / "photo.png", d / "photo.png")
            pr.status = "animating"; s.add(pr); s.commit()
            t = time.time()
            res = run_photo_idle(d / "photo.png", d / "listening.mp4", pr.idle_seconds, pr.head_motion)
            timings["animate"] = round(time.time() - t, 2)
            if not res.get("ok") or not (d / "listening.mp4").exists():
                raise RuntimeError(res.get("error", "photo animation failed"))
            shutil.copyfile(d / "listening.mp4", d / "source.mp4")  # base prep and the live service look for these files
            notes.append("photo replica: no voice reference (preset voice is used)")
            (d / "meta.json").write_text(json.dumps({"face": chk, "has_voice_ref": False, "notes": notes, "photo": True,
                                                     "animate": {k: res.get(k) for k in ("s_per_frame", "frames_rendered", "out_frames", "repaired_frames")}}))
            pr.animate_s = float(timings["animate"])
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        finally:
            shutil.rmtree(d / "photo_work", ignore_errors=True)
            (d / "photo_src").unlink(missing_ok=True)
        pr.status = "error" if err else "ready"
        pr.error = err or ""
        pr.warnings = json.dumps(notes)
        s.add(pr)
        rep.status = "error" if err else "ready"
        s.add(rep); s.commit()
        timings["total"] = round(time.time() - t0, 2)
        jobs._finish(s, "replica", rid, err, {"timings": timings, "notes": notes})
        from .events import on_replica_finished

        on_replica_finished(rid)
        if not err:
            jobs._prewarm_face(rid)
        return err is None


# ---------------------------------------------------------------- creative video
def _set_detail(vid: str, progress: dict) -> None:
    """Live progress in the existing job status (GET /v1/jobs/video/{id} -> detail.progress)."""
    try:
        with Session(db.engine) as s:
            c = s.get(JobClaim, f"video:{vid}")
            if c is not None:
                c.detail = json.dumps({"progress": progress})
                s.add(c); s.commit()
    except Exception:  # noqa: BLE001 - progress is best effort
        pass


def build_scenes(script: str, mode: str) -> list[str]:
    import sys

    sys.path.insert(0, str(WORKERS))
    from scenes import split_scenes  # pure python module

    return split_scenes(script) if mode != "single" else [" ".join(script.split())]


def render_video(s: Session, v: Video, rep: Replica, meta: Optional[VideoMeta], deps, vid_path: Path, data_dir: Path) -> dict:
    """TTS per scene -> word timing -> creative_render. Returns timings; raises on failure."""
    row = s.get(VideoOptions, v.id)
    o = json.loads(row.options) if row else {}
    timings: dict = {}
    scenes = build_scenes(v.script, o.get("scenes", "paragraphs"))
    rd = _jobs().replica_dir(rep.id)
    voice = meta.voice if meta else "default"
    ref = rd / "voice_ref.wav"
    work = Path(tempfile.mkdtemp(prefix=f"creative_{v.id}_", dir=str(data_dir / "videos")))
    try:
        progress = {"stage": "tts", "scene": 0, "scenes": len(scenes), "percent": 0}
        _set_detail(v.id, progress)
        t = time.time()
        wavs = []
        for i, text in enumerate(scenes):
            w = work / f"s{i}.wav"
            deps.voice.synthesize(text, w, ref if ref.exists() else None, voice)
            wavs.append(w)
            _set_detail(v.id, {**progress, "scene": i + 1, "percent": round(8 * (i + 1) / len(scenes))})
        timings["tts"] = round(time.time() - t, 2)
        want_caps = bool(o.get("captions"))
        lang = None
        try:
            from .languages import lang_for_voice

            lang = (lang_for_voice("af_heart" if voice in ("default", "") else voice) or "en").split("-")[0]
        except Exception:  # noqa: BLE001
            pass
        words = []
        t = time.time()
        for w in wavs:
            words.append(transcribe_words(w, lang) if want_caps else None)
        if want_caps:
            timings["word_timing"] = round(time.time() - t, 2)
        bg = o.get("background") or replica_background(s, rep.id)
        spec = {"replica_dir": str(rd), "out": str(vid_path), "format": o.get("format", "16:9"), "resolution": o.get("resolution", 720),
                "background": bg, "captions": o.get("captions"), "logo": o.get("logo"),
                "transition": o.get("transition", "fade"), "transition_s": o.get("transition_s", 0.4),
                "sharpen": 0.6, "restore": o.get("restore", "none"),
                "scenes": [{"audio": str(w), "text": txt, "words": ws} for w, txt, ws in zip(wavs, scenes, words)]}
        if o.get("thumbnail", True):
            spec["thumbnail"] = str(vid_path.with_suffix(".jpg"))
        if o.get("captions"):
            spec["srt"] = str(vid_path.with_suffix(".srt"))
        weights = [max(1, len(x)) for x in scenes]
        tot = float(sum(weights))
        state = {"scene": 1}

        def on_progress(p: dict):
            if p.get("stage") == "scene":
                state["scene"] = p["scene"]
            i = state["scene"] - 1
            frac = float(p.get("frac_scene", 0))
            pct = 10 + 88 * (sum(weights[:i]) + frac * weights[i]) / tot
            _set_detail(v.id, {"stage": "render", "scene": i + 1, "scenes": len(scenes), "percent": round(pct, 1)})

        t = time.time()
        res = run_renderer(spec, on_progress, work)
        timings["render"] = round(time.time() - t, 2)
        timings["renderer"] = res.get("timings", {})
        if not vid_path.exists():
            raise RuntimeError("renderer reported success but no file was written")
        return timings
    finally:
        shutil.rmtree(work, ignore_errors=True)
