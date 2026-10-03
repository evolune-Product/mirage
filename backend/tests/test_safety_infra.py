"""Safety/infra: signed URLs, rate limits, size limits, headers, consent audio, deletion."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import consent_verify, db, hardening, jobs, safety, settings, signing, voiceprint
from app.main import app
from app.models_billing import ConsentRecord

MODELS = Path(__file__).resolve().parents[2] / "models"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", create_engine("sqlite://", connect_args={"check_same_thread": False},
                                                    poolclass=StaticPool))
    SQLModel.metadata.create_all(db.engine)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("MIRAGE_SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.setenv("MIRAGE_RL_SIGNUP", "1000/60")
    safety.limiter.reset()

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    yield TestClient(app)
    app.dependency_overrides.clear()
    safety.limiter.reset()


def signup(c, ip=None):
    r = c.post("/v1/signup", json={"email": "a@b.co"})
    return {"x-api-key": r.json()["api_key"]}


# ---------- phrase matching ----------
PHRASE = "I consent to Mirage creating an AI replica of my face and voice named Bob. My verification code is amber-river-stone."


def test_phrase_tolerates_asr_noise_but_needs_code_words():
    ok = "I consent to Mirage creating an AI replica of my face and voice named Bob. My verification code is amber, river, stone."
    assert safety.code_words_present(PHRASE, ok) and safety.phrase_score(PHRASE, ok) > 0.9
    slip = ok.replace("river", "rivers")
    assert safety.code_words_present(PHRASE, slip)
    assert not safety.code_words_present(PHRASE, ok.replace("amber", "ember"))  # a different code word
    assert not safety.code_words_present(PHRASE, ok.replace("stone", "cedar"))
    assert not safety.code_words_present(PHRASE, "amber stone river")  # order matters
    assert not safety.code_words_present(PHRASE, "I consent to everything")
    assert safety.phrase_score(PHRASE, "hello world") < 0.3


# ---------- signed URLs ----------
def test_signing_roundtrip_expiry_tamper(monkeypatch):
    monkeypatch.setenv("MIRAGE_SECRET_KEY", "test-secret-key-0123456789")
    u = signing.sign_path("/v1/files/videos/v_ab12.mp4", ttl=60)
    path, q = u.split("?")
    kv = dict(x.split("=") for x in q.split("&"))
    assert signing.verify(path, kv["exp"], kv["sig"])
    assert not signing.verify(path.replace("ab12", "ab13"), kv["exp"], kv["sig"])
    assert not signing.verify(path, str(int(kv["exp"]) + 1), kv["sig"])
    assert not signing.verify(path, kv["exp"], kv["sig"], now=time.time() + 120)
    assert not signing.verify(path, None, None)


def _ready_replica(env, h, tmp_path):
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/x.mp4"}, headers=h).json()["id"]
    d = jobs.replica_dir(rid); d.mkdir(parents=True)
    (d / "face.png").write_bytes(b"png")
    return rid


def test_files_require_signature_in_production_mode(env, tmp_path, monkeypatch):
    h = signup(env)
    rid = _ready_replica(env, h, tmp_path)
    url = f"/v1/files/replicas/{rid}/face.png"
    assert env.get(url).status_code == 200  # dev default: legacy public
    monkeypatch.setenv("MIRAGE_ALLOW_PUBLIC_FILES", "0")
    assert env.get(url).status_code == 403
    s = env.post("/v1/files/sign", json={"path": url}, headers=h)
    assert s.status_code == 200
    assert env.get(s.json()["url"]).content == b"png"
    assert env.get(s.json()["url"].replace("sig=", "sig=x")).status_code == 403
    # another account cannot sign it
    h2 = signup(env)
    assert env.post("/v1/files/sign", json={"path": url}, headers=h2).status_code == 404
    assert env.post("/v1/files/sign", json={"path": "/etc/passwd"}, headers=h).status_code == 422
    # a bad signature never falls back to public access even in dev mode
    monkeypatch.setenv("MIRAGE_ALLOW_PUBLIC_FILES", "1")
    assert env.get(url + "?exp=1&sig=bad").status_code == 403


def test_output_url_is_signed_in_responses(env, tmp_path):
    h = signup(env)
    rid = _ready_replica(env, h, tmp_path)
    with Session(db.engine) as s:
        r = s.get(db.Replica, rid); r.status = "ready"; s.add(r)
        v = db.Video(account_id=r.account_id, replica_id=rid, script="hi", status="ready",
                     output_url="/v1/files/videos/v_ab12.mp4")
        s.add(v); s.commit(); vid = v.id
    out = env.get(f"/v1/videos/{vid}", headers=h).json()
    assert "?exp=" in out["output_url"] and "&sig=" in out["output_url"]
    # user-controlled strings are NOT rewritten (replica name equal to a file path)
    n = env.post("/v1/replicas", json={"name": "/v1/files/videos/v_ab12.mp4", "train_video_url": "/x"}, headers=h).json()
    assert env.get(f"/v1/replicas/{n['id']}", headers=h).json()["name"] == "/v1/files/videos/v_ab12.mp4"


# ---------- rate limit / size / headers ----------
def test_rate_limit_429_retry_after(env, monkeypatch):
    monkeypatch.setenv("MIRAGE_RL_CHECKOUT", "3/60")
    h = signup(env)
    codes = [env.post("/v1/billing/checkout", json={"provider": "stripe", "kind": "topup", "sku": "topup_60"},
                      headers=h).status_code for _ in range(5)]
    assert codes[:3] == [501, 501, 501] and codes[3:] == [429, 429]
    r = env.post("/v1/billing/checkout", json={}, headers=h)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    h2 = signup(env)  # another key is unaffected
    assert env.post("/v1/billing/checkout", json={"provider": "stripe", "kind": "topup", "sku": "topup_60"},
                    headers=h2).status_code == 501


def test_signup_limited_per_ip(env, monkeypatch):
    monkeypatch.setenv("MIRAGE_RL_SIGNUP", "2/60")
    codes = [env.post("/v1/signup", json={"email": "x@y.co"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_rate_limit_can_be_disabled(env, monkeypatch):
    monkeypatch.setenv("MIRAGE_RL_SIGNUP", "1/60")
    monkeypatch.setenv("MIRAGE_RATE_LIMIT", "off")
    assert all(env.post("/v1/signup", json={"email": "x@y.co"}).status_code == 200 for _ in range(3))


def test_body_size_limit_and_security_headers(env, monkeypatch):
    monkeypatch.setenv("MIRAGE_MAX_BODY_BYTES", "1000")
    r = env.post("/v1/signup", json={"email": "a" * 5000})
    assert r.status_code == 413
    ok = env.get("/health")
    assert ok.headers["x-content-type-options"] == "nosniff" and ok.headers["x-frame-options"] == "DENY"
    assert ok.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors" in env.get("/v1/usage", headers=signup(env)).headers["content-security-policy"]


def test_cors_headers_survive_429(env, monkeypatch):
    monkeypatch.setenv("MIRAGE_RL_SIGNUP", "1/60")
    env.post("/v1/signup", json={"email": "x@y.co"}, headers={"origin": "http://localhost:3000"})
    r = env.post("/v1/signup", json={"email": "x@y.co"}, headers={"origin": "http://localhost:3000"})
    assert r.status_code == 429 and r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_production_config_validation(monkeypatch):
    monkeypatch.setenv("MIRAGE_ENV", "production")
    monkeypatch.delenv("MIRAGE_SECRET_KEY", raising=False)
    monkeypatch.setenv("MIRAGE_CORS_ORIGINS", "*")
    errs = settings.validate_production()
    assert len(errs) == 2
    monkeypatch.setenv("MIRAGE_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("MIRAGE_CORS_ORIGINS", "https://app.example.com")
    assert settings.validate_production() == []
    assert not settings.allow_public_files() and not settings.allow_typed_consent()
    assert settings.voice_match_mode() == "enforce"
    assert "***" in settings.redact("key mk_abcdefghijk1234") and "abcdefghijk" not in settings.redact("mk_abcdefghijk1234")


# ---------- consent ----------
def test_typed_consent_can_be_disabled(env, monkeypatch):
    h = signup(env)
    rid = env.post("/v1/replicas", json={"name": "n", "train_video_url": "/x"}, headers=h).json()["id"]
    c = env.post(f"/v1/replicas/{rid}/consent/challenge", headers=h).json()
    body = {"challenge_id": c["challenge_id"], "speaker_name": "Bob", "audio_url": "x", "transcript": c["phrase"]}
    monkeypatch.setenv("MIRAGE_ALLOW_TYPED_CONSENT", "0")
    r = env.post(f"/v1/replicas/{rid}/consent", json=body, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "typed_consent_disabled"
    monkeypatch.setenv("MIRAGE_ALLOW_TYPED_CONSENT", "1")
    assert env.post(f"/v1/replicas/{rid}/consent", json=body, headers=h).status_code == 200


def _consent_setup(env, h, train_url):
    rid = env.post("/v1/replicas", json={"name": "Bob", "train_video_url": train_url}, headers=h).json()["id"]
    c = env.post(f"/v1/replicas/{rid}/consent/challenge", headers=h).json()
    return rid, c


def _upload(env, h, rid, c, wav_path, name="Bob"):
    with open(wav_path, "rb") as f:
        return env.post(f"/v1/replicas/{rid}/consent/audio", headers=h,
                        data={"challenge_id": c["challenge_id"], "speaker_name": name},
                        files={"file": ("consent.wav", f, "audio/wav")})


def test_consent_audio_mocked_asr(env, tmp_path, monkeypatch):
    import numpy as np, soundfile as sf
    wav = tmp_path / "a.wav"
    sf.write(wav, (np.random.randn(16000 * 3) * 0.1).astype("float32"), 16000)
    monkeypatch.setenv("MIRAGE_CONSENT_VOICE_MATCH", "off")
    h = signup(env)
    rid, c = _consent_setup(env, h, "/x.mp4")
    monkeypatch.setattr(consent_verify, "transcribe", lambda w: "I like turtles")
    r = _upload(env, h, rid, c, wav)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "phrase_mismatch"
    with Session(db.engine) as s:
        assert not s.exec(select(ConsentRecord)).all()  # nothing stored on failure
    monkeypatch.setattr(consent_verify, "transcribe", lambda w: c["phrase"].replace("-", " ").lower())
    r = _upload(env, h, rid, c, wav)
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["audio_sha256"]) == 64 and j["voice_status"] == "skipped"
    assert env.get(f"/v1/replicas/{rid}/consent", headers=h).json()["has_consent"]
    assert env.get(f"/v1/replicas/{rid}/consent/{j['id']}/audio", headers=h).status_code == 200
    assert env.get(f"/v1/replicas/{rid}/consent/{j['id']}/audio", headers=signup(env)).status_code == 404
    assert _upload(env, h, rid, c, wav).status_code == 410  # challenge is single use


@pytest.mark.skipif(not (MODELS / "kokoro-v1.0.onnx").exists() or not voiceprint.available(), reason="needs local models")
def test_consent_audio_real_asr_and_voice_match(env, tmp_path, monkeypatch):
    import numpy as np, soundfile as sf
    from kokoro_onnx import Kokoro
    k = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))

    def say(text, voice, path):
        a, sr = k.create(text, voice=voice, speed=1.0)
        sf.write(path, a, sr)

    train = tmp_path / "train.wav"
    say("Welcome to our quarterly update. Today I will walk you through the numbers and what they mean for the team.",
        "af_heart", train)
    monkeypatch.setenv("MIRAGE_CONSENT_VOICE_MATCH", "enforce")
    h = signup(env)
    rid, c = _consent_setup(env, h, str(train))
    same = tmp_path / "same.wav"; say(c["phrase"], "af_heart", same)
    other = tmp_path / "other.wav"; say(c["phrase"], "am_adam", other)
    r = _upload(env, h, rid, c, other)  # right words, wrong voice
    assert r.status_code == 422 and r.json()["detail"]["error"] == "voice_mismatch", r.text
    assert r.json()["detail"]["voice_score"] < 0.5
    r = _upload(env, h, rid, c, same)
    assert r.status_code == 200, r.text
    assert r.json()["voice_status"] == "match" and r.json()["voice_score"] > 0.5
    assert r.json()["verified_by"] == "asr-phrase+voice-match"


# ---------- deletion ----------
def test_replica_and_account_deletion(env, tmp_path):
    h = signup(env)
    rid, c = _consent_setup(env, h, "/x.mp4")
    env.post(f"/v1/replicas/{rid}/consent", json={"challenge_id": c["challenge_id"], "speaker_name": "B",
                                                   "audio_url": "x", "transcript": c["phrase"]}, headers=h)
    d = jobs.replica_dir(rid); d.mkdir(parents=True); (d / "face.png").write_bytes(b"p")
    (tmp_path / "consent" / rid).mkdir(parents=True); (tmp_path / "consent" / rid / "a.wav").write_bytes(b"x")
    pid = env.post("/v1/personas", json={"name": "p", "system_prompt": "s", "replica_id": rid}, headers=h).json()["id"]
    other = signup(env)
    assert env.delete(f"/v1/replicas/{rid}", headers=other).status_code == 404
    r = env.delete(f"/v1/replicas/{rid}", headers=h)
    assert r.status_code == 200 and r.json()["files_removed"] == 2
    assert not d.exists() and not (tmp_path / "consent" / rid).exists()
    assert env.get(f"/v1/replicas/{rid}", headers=h).status_code == 404
    assert [p for p in env.get("/v1/personas", headers=h).json() if p["id"] == pid][0]["replica_id"] is None
    with Session(db.engine) as s:
        assert not s.exec(select(ConsentRecord)).all()
    assert env.post("/v1/account/delete-my-data", json={"confirm": "no"}, headers=h).status_code == 422
    assert env.post("/v1/account/delete-my-data", json={"confirm": "delete-my-data"}, headers=h).status_code == 200
    assert env.get("/v1/usage", headers=h).status_code == 401
    assert env.get("/v1/usage", headers=other).status_code == 200
