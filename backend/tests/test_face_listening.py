import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db, jobs
from app.main import app
from app.routers import listening_clip as lc


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setattr(lc, "_notify_lipsync", lambda rid: None)

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    yield TestClient(app), tmp_path
    app.dependency_overrides.clear()


def mkvideo(path, secs=2):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=25:duration={secs}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def signup(c, email):
    return {"x-api-key": c.post("/v1/signup", json={"email": email}).json()["api_key"]}


def make_replica(c, h):
    return c.post("/v1/replicas", json={"name": "r", "train_video_url": "x"}, headers=h).json()["id"]


def test_listening_clip_lifecycle(env):
    c, tmp = env
    h = signup(c, "a@b.com")
    rid = make_replica(c, h)
    src = tmp / "src.mp4"
    mkvideo(src)
    assert c.get(f"/v1/replicas/{rid}/listening-clip", headers=h).status_code == 404
    r = c.post(f"/v1/replicas/{rid}/listening-clip", json={"url": str(src)}, headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["width"] == 160 and j["height"] == 120 and 1.5 < j["duration_s"] < 2.5
    assert (tmp / "replicas" / rid / "listening.mp4").exists()
    assert c.get(f"/v1/replicas/{rid}/listening-clip", headers=h).json()["fps"] == 25.0
    # replace
    assert c.post(f"/v1/replicas/{rid}/listening-clip", json={"url": str(src)}, headers=h).status_code == 200
    assert c.delete(f"/v1/replicas/{rid}/listening-clip", headers=h).json() == {"deleted": True}
    assert not (tmp / "replicas" / rid / "listening.mp4").exists()
    assert c.get(f"/v1/replicas/{rid}/listening-clip", headers=h).status_code == 404
    assert c.delete(f"/v1/replicas/{rid}/listening-clip", headers=h).status_code == 404


def test_listening_clip_rejects_bad_input(env):
    c, tmp = env
    h = signup(c, "a@b.com")
    rid = make_replica(c, h)
    assert c.post(f"/v1/replicas/{rid}/listening-clip", json={"url": str(tmp / "nope.mp4")}, headers=h).status_code == 422
    junk = tmp / "junk.mp4"
    junk.write_bytes(b"not a video" * 100)
    assert c.post(f"/v1/replicas/{rid}/listening-clip", json={"url": str(junk)}, headers=h).status_code == 422
    short = tmp / "short.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=0.4",
                    "-pix_fmt", "yuv420p", str(short)], check=True)
    assert c.post(f"/v1/replicas/{rid}/listening-clip", json={"url": str(short)}, headers=h).status_code == 422
    assert not (tmp / "replicas" / rid / "listening.mp4").exists()
    assert not list((tmp / "replicas" / rid).glob("*.tmp.mp4"))


def test_listening_clip_ownership_and_auth(env):
    c, tmp = env
    h1, h2 = signup(c, "a@b.com"), signup(c, "c@d.com")
    rid = make_replica(c, h1)
    src = tmp / "src.mp4"
    mkvideo(src)
    assert c.post(f"/v1/replicas/{rid}/listening-clip", json={"url": str(src)}, headers=h2).status_code == 404
    assert c.post(f"/v1/replicas/{rid}/listening-clip", json={"url": str(src)}).status_code in (401, 403, 422)
    assert c.post("/v1/replicas/r_missing/listening-clip", json={"url": str(src)}, headers=h1).status_code == 404
