"""Face-to-consent binding: does the face of the person who spoke the consent phrase match the replica's source face?

Pipeline: the browser records ONE short video+audio clip while the person reads the challenge phrase. The server
(1) transcribes the audio (existing consent flow), (2) samples frames from the SAME clip, detects the face (YuNet) and
embeds it (SFace, both commercially licensed: MIT / Apache-2.0; see workers/face_embed.py) and compares with the replica's
source face (photo, or frames of the training video), (3) runs passive liveness heuristics on the clip.

Not certified liveness (no ISO 30107 / NIST testing). It stops: a photo held up to the camera, a different person than the
photo/training video, a recording without a face. It does NOT stop: a video replay on a screen, a deepfake streamed into
the camera, a close relative or look-alike (see docs/SECURITY.md).

The face model runs in workers/.venv-face (OpenCV) as a subprocess so the API venv stays light. Reference embeddings are
cached ENCRYPTED (secretbox) in the replica's consent folder (removed on replica/account deletion); the selfie video itself
is discarded after verification (the stored evidence is audio only + sha256 of the original).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import consent_verify, secretbox, settings

ROOT = Path(__file__).resolve().parents[2]
FACE_PY = Path(os.getenv("VOCALFACE_FACE_PY", "") or ROOT / "workers" / ".venv-face" / "bin" / "python")
FACE_SCRIPT = ROOT / "workers" / "face_embed.py"
MODELS = ROOT / "models"

# Measured thresholds (docs/overnight/consent-security.md): same-person frames median 0.855 (5th pct 0.64);
# different people 99.9th pct 0.36. 0.45 is deliberately above the OpenCV-recommended 0.363.
DEFAULT_THRESHOLD = 0.45
MIN_FRAMES = 8              # usable face frames in the selfie clip (about 1.6 s at 5 fps)
MIN_FACE_RATIO = 0.6        # share of sampled frames with exactly one clear dominant face
LIVE_MIN_MOUTH = 4.0        # simulated held photos <= 2.9, real speech >= 5.6 (units: % of inter-ocular distance)
LIVE_MIN_NONRIGID = 0.8     # held photos <= 0.51, real speech >= 1.1
LIVE_MIN_RESIDUAL = 0.045   # registered mouth/chin appearance change: held/tilted/hand-shaken photos <= 0.03, real speech >= 0.052


class FaceUnavailable(RuntimeError):
    pass


def mode() -> str:
    """enforce | warn | off. Default enforce in production, warn in dev."""
    m = os.getenv("VOCALFACE_CONSENT_FACE_MATCH", "").strip().lower()
    if m in ("enforce", "warn", "off"):
        return m
    return "enforce" if settings.is_production() else "warn"


def liveness_mode() -> str:
    m = os.getenv("VOCALFACE_CONSENT_LIVENESS", "").strip().lower()
    return m if m in ("enforce", "warn", "off") else mode()


def threshold() -> float:
    try:
        return float(os.getenv("VOCALFACE_FACE_MATCH_THRESHOLD", "") or DEFAULT_THRESHOLD)
    except ValueError:
        return DEFAULT_THRESHOLD


def available() -> bool:
    return FACE_PY.exists() and FACE_SCRIPT.exists() and (MODELS / "yunet.onnx").exists() and (MODELS / "sface.onnx").exists()


def has_video(path: Path) -> bool:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=30)
        return "video" in r.stdout
    except Exception:  # noqa: BLE001
        return False


def embed_media(path: Path, *, fps: float = 5.0, max_frames: int = 40, start: float = 0.0, duration: float = 0.0) -> dict:
    if not available():
        raise FaceUnavailable("face models or the workers venv are not installed (see docs/overnight/consent-security.md)")
    cmd = [str(FACE_PY), str(FACE_SCRIPT), "--media", str(path), "--fps", str(fps), "--max-frames", str(max_frames)]
    if start:
        cmd += ["--start", str(start)]
    if duration:
        cmd += ["--duration", str(duration)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:  # noqa: BLE001
        raise FaceUnavailable("face model failed to run") from e
    if not out.get("ok"):
        raise ValueError(out.get("error") or "could not read faces")
    return out


def strip_video(src: Path, dst_dir: Path) -> Path:
    """Audio-only copy of a recording (the face video is not kept). Falls back to 16 kHz wav if stream copy fails."""
    ext = ".m4a" if src.suffix.lower() in (".mp4", ".m4a") else (src.suffix.lower() or ".webm")
    dst = dst_dir / (src.stem + ".audio" + ext)
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-c:a", "copy", str(dst)],
                       capture_output=True, timeout=120)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1000:
        dst.unlink(missing_ok=True)
        dst = dst_dir / (src.stem + ".audio.wav")
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)],
                           capture_output=True, timeout=120)
        if r.returncode != 0:
            raise ValueError("could not extract audio from the recording")
    return dst


# ---------------------------------------------------------------- reference face (photo or training video)
def _ref_cache(rid: str, url: str) -> Path:
    return consent_verify.consent_dir(rid) / f"ref_face_{hashlib.sha256(url.encode()).hexdigest()[:16]}.enc"


def reference_embeddings(rid: str, url: str, photo: bool) -> np.ndarray:
    cache = _ref_cache(rid, url)
    if cache.exists():
        try:
            return np.array(json.loads(secretbox.decrypt(cache.read_text())), dtype=np.float32)
        except Exception:  # noqa: BLE001  (corrupt or key rotated: rebuild)
            cache.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="vocalface_ref_") as td:
        src = Path(td) / ("ref.img" if photo else "ref.vid")
        consent_verify.fetch_train_video(url, src)
        if photo:
            src = src.rename(src.with_suffix(".png"))  # extension only picks the decoder path; ffmpeg sniffs content
            out = embed_media(src, max_frames=1)
        else:
            out = embed_media(src, fps=1.0, max_frames=40, duration=90)
    embs = np.array([f["emb"] for f in out["frames"]], dtype=np.float32)
    if len(embs):
        cache.write_text(secretbox.encrypt(json.dumps(embs.round(5).tolist())))
    return embs


# ---------------------------------------------------------------- decision
@dataclass
class FaceResult:
    face_status: str = "skipped"
    face_score: float | None = None
    frames_used: int = 0
    live_status: str = "skipped"
    live: dict = field(default_factory=dict)
    live_reasons: list = field(default_factory=list)
    ref_kind: str = "video"


def judge_liveness(live: dict, n_decoded: int) -> tuple[bool, list[str]]:
    why = []
    if live.get("frames_used", 0) < MIN_FRAMES or live.get("face_ratio", 0) < MIN_FACE_RATIO:
        why.append("face not clearly visible for the whole recording")
    else:
        if live.get("mouth_motion", 0) < LIVE_MIN_MOUTH:
            why.append("no natural mouth movement while speaking (static image?)")
        if live.get("mouth_residual", 0) < LIVE_MIN_RESIDUAL:
            why.append("the mouth region does not change like a live face (photo or screen held up?)")
        if live.get("nonrigid", 0) < LIVE_MIN_NONRIGID:
            why.append("face does not move like a live face (static image?)")
    return (not why), why


def compare(selfie: np.ndarray, ref: np.ndarray) -> float:
    """Median over selfie frames of the best cosine against any reference face."""
    s = selfie @ ref.T
    return float(np.median(s.max(axis=1)))


def verify(rid: str, ref_url: str, photo: bool, recording: Path) -> FaceResult:
    """Never raises for 'bad' people, only returns statuses. Raises FaceUnavailable when the model cannot run."""
    res = FaceResult(ref_kind="photo" if photo else "video")
    if not has_video(recording):
        res.face_status = "no_video"
        return res
    sel = embed_media(recording, fps=5.0, max_frames=60)
    res.frames_used = sel.get("n_face_frames", 0)
    ok, why = judge_liveness(sel["liveness"], sel.get("n_decoded", 0))
    res.live, res.live_reasons, res.live_status = sel["liveness"], why, "pass" if ok else "fail"
    if res.frames_used < MIN_FRAMES:
        res.face_status = "no_face"
        return res
    try:
        ref = reference_embeddings(rid, ref_url, photo)
    except FaceUnavailable:
        raise
    except Exception:  # noqa: BLE001  (download failure etc.)
        res.face_status = "unavailable"
        return res
    if len(ref) == 0:
        res.face_status = "no_reference"
        return res
    selfie = np.array([f["emb"] for f in sel["frames"]], dtype=np.float32)
    res.face_score = round(compare(selfie, ref), 3)
    res.face_status = "match" if res.face_score >= threshold() else "mismatch"
    return res
