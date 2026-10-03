"""Creative features through the real API + job code, with the heavy worker steps faked (see creative_jobs.py)."""
import io
import json
import wave

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import creative_jobs as cj, db, jobs, safety
from app.main import app
from app.models_creative import PhotoReplica
from app.models_extra import JobClaim
from app.routers import photo_replica as pr_router


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setattr(safety, "has_consent", lambda session, rid: True)
    monkeypatch.setattr(pr_router, "_lipsync_invalidate", lambda rid: None)
    monkeypatch.setattr(jobs, "_prewarm_face", lambda rid: None)

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    yield TestClient(app), tmp_path
    app.dependency_overrides.clear()


def hdr(c, email="a@b.com"):
    return {"x-api-key": c.post("/v1/signup", json={"email": email}).json()["api_key"]}


def png_bytes(w=64, h=48, color=(200, 30, 30)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


def upload(c, h, kind="background", data=None):
    return c.post("/v1/creative/assets", files={"file": ("x.png", data or png_bytes(), "image/png")}, data={"kind": kind}, headers=h)


class FakeVoice:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, out_wav, voice_ref, voice):
        self.calls.append(text)
        with wave.open(str(out_wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\0\0" * 24000)


def deps(voice=None):
    def fetch(url, dest):
        dest.write_bytes(png_bytes(640, 640))

    return jobs.Deps(fetch=fetch, extract_audio=lambda v, o: False, extract_face=lambda v, o: {"ok": True},
                     voice=voice or FakeVoice(), render=lambda *a: {"ok": False, "error": "legacy renderer must not run"},
                     webhook=lambda u, p: "delivered")


# ------------------------------------------------------------------ photo replica
def fake_check(ok=True, error=None, warnings=()):
    def run(image, out_dir):
        if not ok:
            return {"ok": False, "error": error}
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "face.png").write_bytes(png_bytes())
        (out_dir / "photo.png").write_bytes(png_bytes(640, 640))
        return {"ok": True, "warnings": list(warnings), "face_px": 200, "jaw_open": 0.01}
    return run


def fake_idle(ok=True):
    def run(photo, out_mp4, seconds, head):
        if not ok:
            return {"ok": False, "error": "LivePortrait crashed"}
        out_mp4.write_bytes(b"mp4")
        return {"ok": True, "s_per_frame": 2.3, "frames_rendered": 50, "out_frames": 99, "repaired_frames": 0}
    return run


def test_photo_replica_lifecycle(env, monkeypatch):
    c, tmp = env
    h = hdr(c)
    monkeypatch.setattr(cj, "run_photo_check", fake_check(warnings=["face is small in the frame"]))
    monkeypatch.setattr(cj, "run_photo_idle", fake_idle())
    r = c.post("/v1/replicas/photo", json={"name": "p", "photo_url": "https://x/p.jpg", "idle_seconds": 3}, headers=h)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert r.json()["status"] == "awaiting_consent"
    assert c.get(f"/v1/replicas/{rid}/photo", headers=h).json()["status"] == "queued"
    assert jobs.run_once(deps()) == ("replica", rid, True)
    j = c.get(f"/v1/replicas/{rid}/photo", headers=h).json()
    assert j["status"] == "ready" and j["replica_status"] == "ready" and j["warnings"][0].startswith("face is small")
    assert j["idle_url"].endswith("/idle.mp4")
    d = jobs.replica_dir(rid)
    assert (d / "listening.mp4").exists() and (d / "source.mp4").exists() and (d / "face.png").exists()
    assert not (d / "photo_work").exists() and not (d / "photo_src").exists()  # scratch removed
    assert c.get(f"/v1/replicas/{rid}", headers=h).json()["status"] == "ready"
    meta = json.loads((d / "meta.json").read_text())
    assert meta["photo"] is True and meta["has_voice_ref"] is False
    st = c.get(f"/v1/jobs/replica/{rid}", headers=h).json()
    assert st["error"] is None and "animate" in st["detail"]["timings"]


@pytest.mark.parametrize("check,idle,msg", [
    (fake_check(ok=False, error="mouth is open in the photo"), fake_idle(), "mouth is open"),
    (fake_check(), fake_idle(ok=False), "LivePortrait crashed"),
])
def test_photo_replica_failures_surface(env, monkeypatch, check, idle, msg):
    c, _ = env
    h = hdr(c)
    monkeypatch.setattr(cj, "run_photo_check", check)
    monkeypatch.setattr(cj, "run_photo_idle", idle)
    rid = c.post("/v1/replicas/photo", json={"name": "p", "photo_url": "https://x/p.jpg"}, headers=h).json()["id"]
    assert jobs.run_once(deps())[2] is False
    st = c.get(f"/v1/jobs/replica/{rid}", headers=h).json()
    assert st["status"] == "error" and msg in st["error"]
    p = c.get(f"/v1/replicas/{rid}/photo", headers=h).json()
    assert p["status"] == "error" and msg in p["error"] and p["idle_url"] is None


def test_photo_replica_needs_consent_and_owner(env, monkeypatch):
    c, _ = env
    h, h2 = hdr(c), hdr(c, "z@z.com")
    monkeypatch.setattr(safety, "has_consent", lambda session, rid: False)
    rid = c.post("/v1/replicas/photo", json={"name": "p", "photo_url": "https://x/p.jpg"}, headers=h).json()["id"]
    assert jobs.run_once(deps()) is None  # consent gate applies to photo replicas too
    assert c.get(f"/v1/replicas/{rid}", headers=h).json()["status"] == "awaiting_consent"
    assert c.get(f"/v1/replicas/{rid}/photo", headers=h2).status_code == 404
    assert c.post("/v1/replicas/photo", json={"name": "p", "photo_url": "x", "idle_seconds": 99}, headers=h).status_code == 422


def test_photo_voice_check_skipped_in_consent(env):
    from sqlmodel import Session as S

    c, _ = env
    h = hdr(c)
    rid = c.post("/v1/replicas/photo", json={"name": "p", "photo_url": "https://x/p.jpg"}, headers=h).json()["id"]
    assert cj.is_photo_replica(rid)
    other = c.post("/v1/replicas", json={"name": "v", "train_video_url": "x"}, headers=h).json()["id"]
    assert not cj.is_photo_replica(other)


# ------------------------------------------------------------------ backgrounds + assets
def ready_replica(c, h, tmp):
    rid = c.post("/v1/replicas", json={"name": "v", "train_video_url": "x"}, headers=h).json()["id"]
    d = tmp / "replicas" / rid
    d.mkdir(parents=True)
    (d / "face.png").write_bytes(b"x")
    with Session(db.engine) as s:
        r = s.get(db.Replica, rid); r.status = "ready"; s.add(r); s.commit()
    return rid


def test_background_crud_and_validation(env):
    c, tmp = env
    h = hdr(c)
    rid = ready_replica(c, h, tmp)
    assert c.get(f"/v1/replicas/{rid}/background", headers=h).status_code == 404
    r = c.post(f"/v1/replicas/{rid}/background", json={"type": "color", "color": "#10203F"}, headers=h)
    assert r.status_code == 200 and r.json()["background"] == {"type": "color", "color": "#10203f"}
    assert json.loads((tmp / "replicas" / rid / "background.json").read_text())["color"] == "#10203f"  # lipsync service reads this
    g = c.post(f"/v1/replicas/{rid}/background", json={"type": "gradient", "colors": ["#ff0000", "#0000ff"], "angle": 45}, headers=h)
    assert g.status_code == 200
    assert c.get(f"/v1/replicas/{rid}/background", headers=h).json()["background"]["type"] == "gradient"
    for bad in ({"type": "color", "color": "red"}, {"type": "gradient", "colors": ["#fff"]}, {"type": "nope"},
                {"type": "image"}, {"type": "image", "asset_id": "asset_deadbeef"}, {"type": "blur", "radius": 1}):
        assert c.post(f"/v1/replicas/{rid}/background", json=bad, headers=h).status_code == 422, bad
    assert c.delete(f"/v1/replicas/{rid}/background", headers=h).json() == {"deleted": True}
    assert not (tmp / "replicas" / rid / "background.json").exists()
    assert c.delete(f"/v1/replicas/{rid}/background", headers=h).status_code == 404
    # another account cannot touch it
    assert c.post(f"/v1/replicas/{rid}/background", json={"type": "color", "color": "#000000"}, headers=hdr(c, "o@o.com")).status_code == 404


def test_assets_upload_use_and_ownership(env):
    c, tmp = env
    h, h2 = hdr(c), hdr(c, "z@z.com")
    rid = ready_replica(c, h, tmp)
    a = upload(c, h)
    assert a.status_code == 200, a.text
    aid = a.json()["id"]
    assert a.json()["width"] == 64 and (tmp / "creative_assets" / f"{aid}.png").exists()
    assert c.post(f"/v1/replicas/{rid}/background", json={"type": "image", "asset_id": aid, "blur": 5}, headers=h).status_code == 200
    stored = json.loads((tmp / "replicas" / rid / "background.json").read_text())
    assert stored["path"].endswith(f"{aid}.png") and stored["blur"] == 5
    assert "path" not in c.get(f"/v1/replicas/{rid}/background", headers=h).json()["background"]  # server paths never leak
    # other account cannot use it; junk and wrong kind rejected
    rid2 = ready_replica(c, h2, tmp)
    assert c.post(f"/v1/replicas/{rid2}/background", json={"type": "image", "asset_id": aid}, headers=h2).status_code == 422
    assert upload(c, h, data=b"not an image").status_code == 422
    assert upload(c, h, kind="bogus").status_code == 422
    assert len(c.get("/v1/creative/assets", headers=h).json()) == 1 and c.get("/v1/creative/assets", headers=h2).json() == []
    assert c.delete(f"/v1/creative/assets/{aid}", headers=h2).status_code == 404
    assert c.delete(f"/v1/creative/assets/{aid}", headers=h).json() == {"deleted": True}
    assert not (tmp / "creative_assets" / f"{aid}.png").exists()
    # JSON url form (local path allowed outside production)
    f = tmp / "bg.png"; f.write_bytes(png_bytes())
    assert c.post("/v1/creative/assets", json={"url": str(f), "kind": "logo"}, headers=h).json()["kind"] == "logo"


def test_creative_options_listing(env):
    c, _ = env
    o = c.get("/v1/creative/options", headers=hdr(c)).json()
    assert o["formats"] == ["16:9", "9:16", "1:1"] and "bold" in o["caption_styles"] and "slide" in o["transitions"]


# ------------------------------------------------------------------ creative video
def test_render_endpoint_validation(env):
    c, tmp = env
    h = hdr(c)
    rid = ready_replica(c, h, tmp)
    base = {"replica_id": rid, "script": "Hello there."}
    ok = c.post("/v1/video-jobs/render", json={**base, "format": "9:16", "captions": {"style": "bold"}}, headers=h)
    assert ok.status_code == 200, ok.text
    j = ok.json()
    assert j["status"] == "queued" and j["scenes"] == 1 and j["options"]["format"] == "9:16" and j["options"]["captions"]["style"] == "bold"
    for bad in ({"format": "4:3"}, {"resolution": 999}, {"captions": {"style": "comic"}}, {"transition": "spin"},
                {"logo": {"asset_id": "asset_00"}}, {"scenes": "all"}, {"background": {"type": "color", "color": "zz"}}):
        r = c.post("/v1/video-jobs/render", json={**base, **bad}, headers=h)
        assert r.status_code == 422, (bad, r.text)
    assert c.post("/v1/video-jobs/render", json={**base, "replica_id": "r_nope"}, headers=h).status_code == 404
    two = c.post("/v1/video-jobs/render", json={**base, "script": "Scene one is here.\n\nScene two follows right after it."}, headers=h)
    assert two.json()["scenes"] == 2
    pre = c.post("/v1/videos/scenes/preview", json={"script": "One paragraph of text that is long enough.\n\nAnother paragraph that is long enough too."}, headers=h)
    assert [s["index"] for s in pre.json()["scenes"]] == [1, 2]
    assert c.post("/v1/video-jobs/render", json={**base, "script": "x\n\n" * 3000}, headers=h).status_code == 422  # too long


def fake_renderer(record, fail=None):
    def run(spec, on_progress, workdir):
        record.append(spec)
        if fail:
            raise RuntimeError(fail)
        n = len(spec["scenes"])
        for i in range(n):
            on_progress({"stage": "scene", "scene": i + 1, "scenes": n, "frac_scene": 0})
            on_progress({"frac_scene": 0.5})
        from pathlib import Path

        Path(spec["out"]).write_bytes(b"mp4")
        if spec.get("thumbnail"):
            Path(spec["thumbnail"]).write_bytes(b"jpg")
        if spec.get("srt"):
            Path(spec["srt"]).write_text("1\n")
        return {"ok": True, "timings": {"total_s": 1.0}}
    return run


def test_creative_video_end_to_end_with_fake_renderer(env, monkeypatch):
    c, tmp = env
    h = hdr(c)
    rid = ready_replica(c, h, tmp)
    a = upload(c, h, "logo").json()["id"]
    bg = upload(c, h, "background").json()["id"]
    rec, progress = [], []
    monkeypatch.setattr(cj, "run_renderer", fake_renderer(rec))
    monkeypatch.setattr(cj, "transcribe_words", lambda wav, lang=None: [{"w": "Hello", "s": 0.1, "e": 0.4}])
    orig = cj._set_detail
    monkeypatch.setattr(cj, "_set_detail", lambda vid, p: (progress.append(p), orig(vid, p)))
    r = c.post("/v1/video-jobs/render", json={
        "replica_id": rid, "script": "First scene text here.\n\nSecond scene text here.\n\nThird scene text here.", "voice": "af_heart",
        "format": "1:1", "resolution": 480, "background": {"type": "image", "asset_id": bg},
        "captions": {"style": "karaoke", "accent": "#00ffcc"}, "logo": {"asset_id": a, "position": "bottom-left", "scale": 0.2},
        "transition": "slide"}, headers=h)
    vid = r.json()["id"]
    voice = FakeVoice()
    assert jobs.run_once(deps(voice)) == ("video", vid, True)
    assert len(voice.calls) == 3 and voice.calls[1].startswith("Second scene")  # one TTS call per scene
    spec = rec[0]
    assert spec["format"] == "1:1" and spec["resolution"] == 480 and spec["transition"] == "slide"
    assert spec["background"]["path"].endswith(f"{bg}.png") and spec["logo"]["path"].endswith(f"{a}.png")
    assert spec["captions"] == {"style": "karaoke", "accent": "#00ffcc"} and len(spec["scenes"]) == 3
    assert spec["scenes"][0]["words"][0]["w"] == "Hello" and spec["srt"].endswith(".srt") and spec["thumbnail"].endswith(".jpg")
    assert not list((tmp / "videos").glob("creative_*"))  # scratch dir removed
    # progress went tts -> render per scene, monotonic
    stages = [p["stage"] for p in progress]
    assert stages[0] == "tts" and "render" in stages
    pct = [p["percent"] for p in progress]
    assert pct == sorted(pct) and max(pct) < 100
    assert {p["scene"] for p in progress if p["stage"] == "render"} == {1, 2, 3}
    done = c.get(f"/v1/videos/{vid}/creative", headers=h).json()
    assert done["status"] == "ready" and done["progress"]["percent"] == 100
    assert done["thumbnail_url"].endswith("/thumbnail.jpg") and done["captions_url"].endswith("/captions.srt")
    assert done["options"]["logo"].get("path") is None and done["options"]["background"].get("path") is None
    # signed download of the thumbnail
    s = c.post("/v1/files/sign", json={"path": done["thumbnail_url"]}, headers=h)
    assert s.status_code == 200, s.text
    assert c.get(s.json()["url"]).content == b"jpg"
    assert c.post("/v1/files/sign", json={"path": done["thumbnail_url"]}, headers=hdr(c, "z@z.com")).status_code == 404
    assert c.get(done["thumbnail_url"] + "?exp=1&sig=bad").status_code == 403


def test_creative_video_failure_and_legacy_path(env, monkeypatch):
    c, tmp = env
    h = hdr(c)
    rid = ready_replica(c, h, tmp)
    monkeypatch.setattr(cj, "run_renderer", fake_renderer([], fail="face lost in frame 12"))
    vid = c.post("/v1/video-jobs/render", json={"replica_id": rid, "script": "Hi there."}, headers=h).json()["id"]
    assert jobs.run_once(deps())[2] is False
    st = c.get(f"/v1/jobs/video/{vid}", headers=h).json()
    assert st["status"] == "error" and "face lost" in st["error"]
    # a plain video without options still takes the legacy renderer (unchanged behaviour)
    plain = c.post("/v1/video-jobs", json={"replica_id": rid, "script": "Plain video."}, headers=h).json()["id"]
    d = deps()
    d.render = lambda img, wav, out, fps: (out.write_bytes(b"legacy"), {"ok": True})[1]
    assert jobs.run_once(d) == ("video", plain, True)
    assert jobs.video_path(plain).read_bytes() == b"legacy"


def test_replica_default_background_routes_video_to_creative(env, monkeypatch):
    c, tmp = env
    h = hdr(c)
    rid = ready_replica(c, h, tmp)
    c.post(f"/v1/replicas/{rid}/background", json={"type": "color", "color": "#223344"}, headers=h)
    rec = []
    monkeypatch.setattr(cj, "run_renderer", fake_renderer(rec))
    vid = c.post("/v1/video-jobs", json={"replica_id": rid, "script": "Plain script."}, headers=h).json()["id"]
    assert jobs.run_once(deps()) == ("video", vid, True)
    assert rec[0]["background"] == {"type": "color", "color": "#223344"} and rec[0]["format"] == "16:9"
    assert "captions" in rec[0] and rec[0]["captions"] is None


def test_bulk_and_translate_accept_options(env, monkeypatch):
    c, tmp = env
    h = hdr(c)
    rid = ready_replica(c, h, tmp)
    r = c.post("/v1/video-jobs/bulk", json={"replica_id": rid, "script_template": "Hi {{name}}", "rows": [{"name": "A"}, {"name": "B"}],
                                            "options": {"format": "9:16", "captions": {"style": "bold"}}}, headers=h)
    assert r.status_code == 200, r.text
    ids = [i["video_id"] for i in r.json()["items"]]
    from app.models_creative import VideoOptions

    with Session(db.engine) as s:
        assert all(json.loads(s.get(VideoOptions, i).options)["format"] == "9:16" for i in ids)
    bad = c.post("/v1/video-jobs/bulk", json={"replica_id": rid, "script_template": "Hi {{name}}", "rows": [{"name": "A"}],
                                              "options": {"format": "2:1"}}, headers=h)
    assert bad.status_code == 422
