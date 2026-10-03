import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import db, jobs
from app.db import Replica, Video
from app.main import app
from app.models_extra import JobClaim, VideoMeta


@pytest.fixture()
def env(consent_ok,tmp_path, monkeypatch):
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    yield TestClient(app)
    app.dependency_overrides.clear()


class FakeVoice:
    def synthesize(self, text, out_wav, voice_ref, voice):
        with wave.open(str(out_wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0" * 8000)


def make_deps(face_ok=True, audio_ok=True, hooks=None):
    def fetch(url, dest):
        if "missing" in url:
            raise FileNotFoundError(url)
        dest.write_bytes(b"video")

    def face(video, out):
        if face_ok:
            out.write_bytes(b"png")
        return {"ok": face_ok, **({} if face_ok else {"error": "no face detected in video"})}

    def render(img, wav, out, fps):
        out.write_bytes(b"mp4")
        return {"ok": True}

    def hook(url, payload):
        (hooks if hooks is not None else []).append((url, payload))
        return "delivered"

    return jobs.Deps(fetch=fetch, extract_audio=lambda v, o: (o.write_bytes(b"x" * 5000), audio_ok)[1],
                     extract_face=face, voice=FakeVoice(), render=render, webhook=hook)


def mk(client):
    h = {"x-api-key": client.post("/v1/signup", json={"email": "a@b.com"}).json()["api_key"]}
    return h


def test_replica_training_success(env):
    h = mk(env)
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/x.mp4"}, headers=h).json()["id"]
    assert jobs.run_once(make_deps()) == ("replica", rid, True)
    assert env.get(f"/v1/replicas/{rid}", headers=h).json()["status"] == "ready"
    assert (jobs.replica_dir(rid) / "face.png").exists() and (jobs.replica_dir(rid) / "voice_ref.wav").exists()
    assert jobs.run_once(make_deps()) is None  # claimed + finished: not picked again
    st = env.get(f"/v1/jobs/replica/{rid}", headers=h).json()
    assert st["error"] is None and "total" in st["detail"]["timings"]


def test_replica_errors(env):
    h = mk(env)
    r1 = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/missing.mp4"}, headers=h).json()["id"]
    assert jobs.run_once(make_deps())[2] is False
    r2 = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/ok.mp4"}, headers=h).json()["id"]
    jobs.run_once(make_deps(face_ok=False))
    for rid, msg in [(r1, "FileNotFound"), (r2, "no face")]:
        st = env.get(f"/v1/jobs/replica/{rid}", headers=h).json()
        assert st["status"] == "error" and msg in st["error"]


def test_no_audio_is_warning_not_error(env):
    h = mk(env)
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/ok.mp4"}, headers=h).json()["id"]
    jobs.run_once(make_deps(audio_ok=False))
    st = env.get(f"/v1/jobs/replica/{rid}", headers=h).json()
    assert st["status"] == "ready" and "no usable audio" in st["detail"]["notes"][0]


def test_double_claim_is_atomic(env):
    h = mk(env)
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/ok.mp4"}, headers=h).json()["id"]
    with Session(db.engine) as a, Session(db.engine) as b:
        assert jobs._claim(a, "replica", rid) is not None
        assert jobs._claim(b, "replica", rid) is None


def test_stale_claim_is_retaken_once(env, monkeypatch):
    h = mk(env)
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/ok.mp4"}, headers=h).json()["id"]
    with Session(db.engine) as s:
        jobs._claim(s, "replica", rid)
        monkeypatch.setattr(jobs, "STALE_SECONDS", -1)
        assert jobs._claim(s, "replica", rid).attempts == 2
        assert jobs._claim(s, "replica", rid) is None  # MAX_ATTEMPTS reached


def test_video_flow_with_webhook(env):
    h = mk(env)
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/ok.mp4"}, headers=h).json()["id"]
    jobs.run_once(make_deps())
    hooks = []
    v = env.post("/v1/video-jobs", json={"replica_id": rid, "script": "hello", "callback_url": "http://x.test/hook"}, headers=h).json()
    assert v["status"] == "queued"
    assert jobs.run_once(make_deps(hooks=hooks)) == ("video", v["id"], True)
    out = env.get(f"/v1/videos/{v['id']}", headers=h).json()
    assert out["status"] == "ready" and out["output_url"].split("?")[0].endswith(f"{v['id']}.mp4")
    assert hooks[0][0] == "http://x.test/hook" and hooks[0][1]["event"] == "video.ready"
    assert env.get(out["output_url"]).content == b"mp4"
    assert env.get(f"/v1/jobs/video/{v['id']}", headers=h).json()["webhook"] == "delivered"


def test_video_job_validation_and_plain_videos_endpoint(env):
    h = mk(env)
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/ok.mp4"}, headers=h).json()["id"]
    assert env.post("/v1/video-jobs", json={"replica_id": rid, "script": "x"}, headers=h).status_code == 409
    jobs.run_once(make_deps())
    assert env.post("/v1/video-jobs", json={"replica_id": rid, "script": "x", "callback_url": "ftp://a"}, headers=h).status_code == 422
    # videos created through the stock endpoint (no VideoMeta) are processed too
    v = env.post("/v1/videos", json={"replica_id": rid, "script": "hi"}, headers=h).json()
    assert jobs.run_once(make_deps())[:2] == ("video", v["id"])


def test_render_failure_marks_error(env):
    h = mk(env)
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/ok.mp4"}, headers=h).json()["id"]
    jobs.run_once(make_deps())
    v = env.post("/v1/videos", json={"replica_id": rid, "script": "hi"}, headers=h).json()
    d = make_deps()
    d.render = lambda *a: {"ok": False, "error": "boom"}
    assert jobs.run_once(d)[2] is False
    st = env.get(f"/v1/jobs/video/{v['id']}", headers=h).json()
    assert st["status"] == "error" and "boom" in st["error"]


def test_file_endpoints_reject_traversal(env):
    assert env.get("/v1/files/videos/..%2f..%2fetc.mp4").status_code == 404
    assert env.get("/v1/files/replicas/nope/face.png").status_code == 404


def test_audio_driver_template(tmp_path):
    import sys, math, struct
    sys.path.insert(0, str(jobs.WORKERS_DIR))
    import audio_driver
    p = tmp_path / "a.wav"
    sr = 16000
    samples = [int(12000 * math.sin(i / 20)) if (i // 8000) % 2 == 0 else 0 for i in range(sr * 2)]  # 0.5 s on / 0.5 s off
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    if not audio_driver.DEFAULT_BASE.exists():
        pytest.skip("LivePortrait templates not present")
    env_ = audio_driver.envelope(*audio_driver.read_wav_mono(str(p)), fps=10)
    assert env_[2] > 0.5 and env_[7] < 0.1


@pytest.fixture()
def consent_ok(monkeypatch):
    from app import safety
    monkeypatch.setattr(safety, "has_consent", lambda session, rid: True)


def test_replica_without_consent_is_not_trained(env, monkeypatch):
    from app import safety
    monkeypatch.setattr(safety, "has_consent", lambda session, rid: False)
    h = {"x-api-key": env.post("/v1/signup", json={"email": "a@b.com"}).json()["api_key"]}
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/x.mp4"}, headers=h).json()["id"]
    assert jobs.run_once(make_deps()) is None  # nothing claimable without consent
    assert env.get(f"/v1/replicas/{rid}", headers=h).json()["status"] == "awaiting_consent"
