"""Face-to-consent binding (facematch.py + routers/consent.py): policy tests with a faked model, plus real-model tests
(SFace + YuNet) on the owner's founder video when the models and footage exist on this machine."""
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import consent_verify, db, facematch, jobs, safety, voiceprint
from app.main import app
from app.models_billing import ConsentRecord
from app.models_sec import FaceBinding

ROOT = Path(__file__).resolve().parents[2]
FOUNDER = Path.home() / "Desktop/SpendVeto Video and PPT/SpendVeto_Founder_Video_1min.mp4"
OTHER = ROOT / "workers/LivePortrait/assets/examples/driving/d6.mp4"
REAL = facematch.available() and FOUNDER.exists() and OTHER.exists()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool))
    SQLModel.metadata.create_all(db.engine)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("MIRAGE_SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.setenv("MIRAGE_RL_SIGNUP", "1000/60")
    monkeypatch.setenv("MIRAGE_CONSENT_VOICE_MATCH", "off")
    safety.limiter.reset()

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    yield TestClient(app)
    app.dependency_overrides.clear()
    safety.limiter.reset()


def hdr(c):
    return {"x-api-key": c.post("/v1/signup", json={"email": "a@b.co"}).json()["api_key"]}


def ff(*args):
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr.decode()[-300:]


def synth_av(path: Path, seconds=6):
    """Tiny mp4 with a video track and a speech-like audio track (content irrelevant: face model is faked)."""
    ff("-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=10:duration={seconds}", "-f", "lavfi",
       "-i", f"sine=frequency=220:duration={seconds}", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))


def new_replica(c, h, url="/x.mp4", photo=False):
    if photo:
        rid = c.post("/v1/replicas/photo", json={"name": "Bob", "photo_url": url}, headers=h).json()["id"]
    else:
        rid = c.post("/v1/replicas", json={"name": "Bob", "train_video_url": url}, headers=h).json()["id"]
    return rid, c.post(f"/v1/replicas/{rid}/consent/challenge", headers=h).json()


def post(c, h, rid, ch, media: Path):
    with open(media, "rb") as f:
        return c.post(f"/v1/replicas/{rid}/consent/audio", headers=h, data={"challenge_id": ch["challenge_id"], "speaker_name": "Bob"},
                      files={"file": (media.name, f, "video/mp4")})


def say_phrase(monkeypatch, ch):
    monkeypatch.setattr(consent_verify, "transcribe", lambda w: ch["phrase"].replace("-", " ").lower())


# ------------------------------------------------------------- pure functions
def test_compare_uses_best_reference_frame_and_median():
    ref = np.eye(4, dtype=np.float32)[:2]  # two reference "faces"
    selfie = np.array([[1, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
    assert facematch.compare(selfie, ref) == pytest.approx(0.9)  # per-frame best: [1, .9, 1, 0, 0] -> median .9


def test_liveness_judgement_thresholds():
    real = {"frames_used": 30, "face_ratio": 1.0, "mouth_motion": 6.4, "nonrigid": 1.1, "mouth_residual": 0.074}
    assert facematch.judge_liveness(real, 30) == (True, [])
    for static in ({"frames_used": 30, "face_ratio": 1.0, "mouth_motion": 1.2, "nonrigid": 0.22, "mouth_residual": 0.007},   # still image
                   {"frames_used": 30, "face_ratio": 1.0, "mouth_motion": 2.5, "nonrigid": 0.50, "mouth_residual": 0.013},   # hand-held photo
                   {"frames_used": 30, "face_ratio": 1.0, "mouth_motion": 6.7, "nonrigid": 1.37, "mouth_residual": 0.030}):  # shaken photo, noisy landmarks
        ok, why = facematch.judge_liveness(static, 30)
        assert not ok and why
    assert not facematch.judge_liveness({"frames_used": 3, "face_ratio": 0.1}, 30)[0]  # no face for most of the clip


def test_mode_and_threshold_defaults(monkeypatch):
    for k in ("MIRAGE_CONSENT_FACE_MATCH", "MIRAGE_CONSENT_LIVENESS", "MIRAGE_FACE_MATCH_THRESHOLD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MIRAGE_ENV", "dev")
    assert facematch.mode() == "warn" and facematch.threshold() == 0.45
    monkeypatch.setenv("MIRAGE_ENV", "production")
    assert facematch.mode() == "enforce" and facematch.liveness_mode() == "enforce"
    monkeypatch.setenv("MIRAGE_CONSENT_LIVENESS", "warn")
    assert facematch.liveness_mode() == "warn"


def test_strip_video_keeps_audio_only(tmp_path):
    src = tmp_path / "rec.mp4"; synth_av(src)
    assert facematch.has_video(src)
    out = facematch.strip_video(src, tmp_path)
    assert out.exists() and not facematch.has_video(out) and out.stat().st_size > 1000


# ------------------------------------------------------------- endpoint policy with a faked model
def fake_verify(monkeypatch, **kw):
    def v(rid, url, photo, path):
        return facematch.FaceResult(ref_kind="photo" if photo else "video", **kw)
    monkeypatch.setattr(facematch, "verify", v)


GOOD = dict(face_status="match", face_score=0.8, frames_used=25, live_status="pass",
            live={"nonrigid": 1.3, "mouth_motion": 7.0, "texture_motion": 15.0})


def test_enforce_accepts_match_stores_audio_only_and_scores(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "enforce")
    h = hdr(env); rid, ch = new_replica(env, h, photo=True)
    say_phrase(monkeypatch, ch); fake_verify(monkeypatch, **GOOD)
    media = tmp_path / "rec.mp4"; synth_av(media)
    r = post(env, h, rid, ch, media)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["face_status"] == "match" and j["face_score"] == 0.8 and j["liveness"] == "pass"
    files = list((tmp_path / "consent" / rid).glob("*"))
    assert files and not any(facematch.has_video(f) for f in files if f.suffix in (".webm", ".mp4", ".m4a", ".wav"))
    assert not (tmp_path / "consent" / rid / f"{j['id']}.mp4").exists()  # the original selfie video is gone
    with Session(db.engine) as s:
        fb = s.exec(select(FaceBinding)).one()
        assert fb.face_status == "match" and len(fb.recording_sha256) == 64 and fb.ref_kind == "photo" and fb.live_status == "pass"
    v = env.get(f"/v1/replicas/{rid}/consent/{j['id']}/verification", headers=h).json()
    assert v["face"]["face_score"] == 0.8


@pytest.mark.parametrize("kw,code,status", [
    (dict(face_status="mismatch", face_score=0.12, live_status="pass"), "face_mismatch", 422),
    (dict(face_status="no_video"), "face_no_video", 422),
    (dict(face_status="no_face", live_status="fail"), "face_no_face", 422),
    (dict(face_status="no_reference"), "face_no_reference", 422),
    (dict(face_status="unavailable"), "face_unavailable", 503),
    (dict(face_status="match", face_score=0.9, live_status="fail", live_reasons=["static image?"]), "liveness_failed", 422),
])
def test_enforce_rejects_and_stores_nothing(env, tmp_path, monkeypatch, kw, code, status):
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "enforce")
    h = hdr(env); rid, ch = new_replica(env, h)
    say_phrase(monkeypatch, ch); fake_verify(monkeypatch, **kw)
    media = tmp_path / "rec.mp4"; synth_av(media)
    r = post(env, h, rid, ch, media)
    assert r.status_code == status and r.json()["detail"]["error"] == code, r.text
    with Session(db.engine) as s:
        assert not s.exec(select(ConsentRecord)).all() and not s.exec(select(FaceBinding)).all()
    assert not list((tmp_path / "consent" / rid).glob("*"))  # no recording kept
    # the challenge is NOT burned by a failed attempt: a good retry succeeds
    fake_verify(monkeypatch, **GOOD)
    assert post(env, h, rid, ch, media).status_code == 200


def test_missing_model_fails_closed_in_enforce(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "enforce")
    h = hdr(env); rid, ch = new_replica(env, h)
    say_phrase(monkeypatch, ch)

    def boom(*a, **k):
        raise facematch.FaceUnavailable("no model")
    monkeypatch.setattr(facematch, "verify", boom)
    media = tmp_path / "rec.mp4"; synth_av(media)
    r = post(env, h, rid, ch, media)
    assert r.status_code == 503 and r.json()["detail"]["error"] == "face_unavailable"


def test_warn_mode_records_but_allows_and_off_skips(env, tmp_path, monkeypatch):
    h = hdr(env)
    media = tmp_path / "rec.mp4"; synth_av(media)
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "warn")
    rid, ch = new_replica(env, h); say_phrase(monkeypatch, ch)
    fake_verify(monkeypatch, face_status="mismatch", face_score=0.1, live_status="fail", live_reasons=["x"])
    r = post(env, h, rid, ch, media)
    assert r.status_code == 200 and r.json()["face_status"] == "mismatch"  # visible, not blocking (dev)
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "off")
    rid, ch = new_replica(env, h); say_phrase(monkeypatch, ch)

    def never(*a, **k):
        raise AssertionError("must not run")
    monkeypatch.setattr(facematch, "verify", never)
    assert post(env, h, rid, ch, media).json()["face_status"] == "skipped"


def test_audio_only_recording_dev_ok_production_rejected(env, tmp_path, monkeypatch):
    """The old audio-only flow still works in dev; production needs the face video."""
    wav = tmp_path / "a.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=300:duration=4", "-ar", "16000", str(wav))
    h = hdr(env)
    rid, ch = new_replica(env, h); say_phrase(monkeypatch, ch)
    with open(wav, "rb") as f:
        r = env.post(f"/v1/replicas/{rid}/consent/audio", headers=h, data={"challenge_id": ch["challenge_id"], "speaker_name": "B"},
                     files={"file": ("a.wav", f, "audio/wav")})
    assert r.status_code == 200 and r.json()["face_status"] == "no_video"
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "enforce")
    rid, ch = new_replica(env, h); say_phrase(monkeypatch, ch)
    with open(wav, "rb") as f:
        r = env.post(f"/v1/replicas/{rid}/consent/audio", headers=h, data={"challenge_id": ch["challenge_id"], "speaker_name": "B"},
                     files={"file": ("a.wav", f, "audio/wav")})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "face_no_video"


def test_typed_consent_is_dev_only_and_has_no_face_binding(env, monkeypatch):
    """Typed consent never creates a FaceBinding, so production worker gate (require_consent) refuses it."""
    h = hdr(env); rid, ch = new_replica(env, h)
    r = env.post(f"/v1/replicas/{rid}/consent", json={"challenge_id": ch["challenge_id"], "speaker_name": "B",
                                                      "audio_url": "x", "transcript": ch["phrase"]}, headers=h)
    assert r.status_code == 200 and r.json()["verified_by"].startswith("typed")
    safety.require_consent(rid)  # dev: fine
    monkeypatch.setenv("MIRAGE_ENV", "production")
    with pytest.raises(safety.ConsentRequired):
        safety.require_consent(rid)
    monkeypatch.setenv("MIRAGE_ALLOW_TYPED_CONSENT", "0")
    assert env.post(f"/v1/replicas/{rid}/consent", json={"challenge_id": ch["challenge_id"], "speaker_name": "B",
                                                         "audio_url": "x", "transcript": ch["phrase"]}, headers=h).status_code == 403


def test_worker_gate_needs_a_matching_face_binding_in_production(env, tmp_path, monkeypatch):
    h = hdr(env); rid, ch = new_replica(env, h)
    say_phrase(monkeypatch, ch); fake_verify(monkeypatch, **GOOD)
    media = tmp_path / "rec.mp4"; synth_av(media)
    monkeypatch.setenv("MIRAGE_ENV", "production")
    assert post(env, h, rid, ch, media).status_code == 200
    safety.require_consent(rid)  # voice+phrase+face on file
    with Session(db.engine) as s:  # a consent whose face did not match must not unlock the replica
        fb = s.exec(select(FaceBinding)).one(); fb.face_status = "mismatch"; s.add(fb); s.commit()
    with pytest.raises(safety.ConsentRequired):
        safety.require_consent(rid)


def test_revoking_consent_deletes_reference_face_templates(env, tmp_path):
    h = hdr(env); rid, _ = new_replica(env, h)
    ref = consent_verify.consent_dir(rid) / "ref_face_abc.enc"; ref.write_text("x")
    assert env.delete(f"/v1/replicas/{rid}/consent", headers=h).status_code == 200
    assert not ref.exists()


def test_reference_embeddings_are_cached_encrypted(env, tmp_path, monkeypatch):
    calls = []

    def fake_embed(path, **k):
        calls.append(1)
        return {"frames": [{"emb": [0.6, 0.8]}], "liveness": {}}
    monkeypatch.setattr(facematch, "embed_media", fake_embed)
    monkeypatch.setattr(consent_verify, "fetch_train_video", lambda url, dest: dest.write_bytes(b"x"))
    a = facematch.reference_embeddings("rep_1", "http://x/p.png", True)
    b = facematch.reference_embeddings("rep_1", "http://x/p.png", True)
    assert calls == [1] and np.allclose(a, b)
    blob = next((tmp_path / "consent" / "rep_1").glob("ref_face_*.enc")).read_text()
    assert blob.startswith(("f1:", "h1:")) and "0.6" not in blob  # not plaintext


# ------------------------------------------------------------- real models on real footage
def real_clip(tmp, name, src, ss, t, audio=True):
    out = tmp / name
    args = ["-ss", str(ss), "-t", str(t), "-i", str(src)]
    if not audio:
        args = ["-ss", str(ss), "-t", str(t), "-i", str(src), "-f", "lavfi", "-i", f"anoisesrc=d={t}:a=0.1", "-map", "0:v", "-map", "1:a", "-shortest"]
    ff(*args, "-vf", "scale=640:-2", "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out))
    return out


@pytest.mark.skipif(not REAL, reason="needs models/{yunet,sface}.onnx, workers/.venv-face and local footage")
def test_real_same_person_passes_and_binds_to_a_photo(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "enforce")
    photo = tmp_path / "photo.png"
    ff("-ss", "50", "-i", str(FOUNDER), "-frames:v", "1", str(photo))
    h = hdr(env); rid, ch = new_replica(env, h, url=str(photo), photo=True)
    say_phrase(monkeypatch, ch)
    sel = real_clip(tmp_path, "selfie.mp4", FOUNDER, 8, 7)
    r = post(env, h, rid, ch, sel)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["face_status"] == "match" and j["face_score"] > 0.6 and j["liveness"] == "pass", j


@pytest.mark.skipif(not REAL, reason="needs models/{yunet,sface}.onnx, workers/.venv-face and local footage")
def test_real_different_person_is_rejected(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "enforce")
    photo = tmp_path / "photo.png"
    ff("-ss", "50", "-i", str(FOUNDER), "-frames:v", "1", str(photo))
    h = hdr(env); rid, ch = new_replica(env, h, url=str(photo), photo=True)
    say_phrase(monkeypatch, ch)
    other = real_clip(tmp_path, "other.mp4", OTHER, 0, 6, audio=False)
    r = post(env, h, rid, ch, other)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "face_mismatch", r.text
    assert r.json()["detail"]["face_score"] < facematch.threshold()


@pytest.mark.skipif(not REAL, reason="needs models/{yunet,sface}.onnx, workers/.venv-face and local footage")
@pytest.mark.parametrize("kind", ["still", "handheld"])
def test_real_photo_held_to_camera_fails_liveness(env, tmp_path, monkeypatch, kind):
    """The right person's face, but a still image (or a hand-held one) instead of a live person."""
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "enforce")
    photo = tmp_path / "photo.png"
    ff("-ss", "50", "-i", str(FOUNDER), "-frames:v", "1", str(photo))
    h = hdr(env); rid, ch = new_replica(env, h, url=str(photo), photo=True)
    say_phrase(monkeypatch, ch)
    vf = "scale=640:-2,noise=alls=3:allf=t"
    if kind == "handheld":
        vf = "scale=700:-2,crop=640:ih-60:'30+10*sin(t*3)':'30+8*cos(t*2.3)',noise=alls=3:allf=t"
    fake = tmp_path / f"{kind}.mp4"
    ff("-loop", "1", "-framerate", "25", "-t", "7", "-i", str(photo), "-f", "lavfi", "-i", "anoisesrc=d=7:a=0.1",
       "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(fake))
    r = post(env, h, rid, ch, fake)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "liveness_failed", r.text


@pytest.mark.skipif(not REAL, reason="needs models/{yunet,sface}.onnx, workers/.venv-face and local footage")
def test_real_training_video_replica_binds_face_to_video(env, tmp_path, monkeypatch):
    """Video replica: the consenting person's face is compared with the faces in the training video (face-voice tie)."""
    monkeypatch.setenv("MIRAGE_CONSENT_FACE_MATCH", "enforce")
    h = hdr(env); rid, ch = new_replica(env, h, url=str(FOUNDER))
    say_phrase(monkeypatch, ch)
    assert post(env, h, rid, ch, real_clip(tmp_path, "other.mp4", OTHER, 0, 6, audio=False)).status_code == 422
    r = post(env, h, rid, ch, real_clip(tmp_path, "me.mp4", FOUNDER, 31, 7))
    assert r.status_code == 200 and r.json()["face_status"] == "match", r.text
