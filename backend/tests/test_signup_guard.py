"""Bot-resistant signup: honeypot, disposable emails, caps, proof of work, duplicate normalisation."""
import pytest
from fastapi.testclient import TestClient

from app import db, signup_guard as sg
from app.main import app
from app.safety import limiter
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select


@pytest.fixture()
def c(monkeypatch):
    db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db.engine)

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    limiter.reset()
    for k in ("VOCALFACE_SIGNUP_POW_BITS", "VOCALFACE_SIGNUP_PER_IP_DAY", "VOCALFACE_SIGNUP_GLOBAL_HOUR", "VOCALFACE_SIGNUP_UNIQUE_EMAIL", "VOCALFACE_BLOCKED_EMAIL_DOMAINS"):
        monkeypatch.delenv(k, raising=False)
    yield TestClient(app)
    app.dependency_overrides.clear()


def count():
    with Session(db.engine) as s:
        return len(s.exec(select(db.Account)).all())


def test_normal_signup_still_works(c):
    r = c.post("/v1/signup", json={"email": "real.person@example.org"})
    assert r.status_code == 200 and r.json()["api_key"].startswith("mk_")


def test_honeypot_fakes_success_and_creates_nothing(c):
    r = c.post("/v1/signup", json={"email": "bot@example.org", "website": "http://spam"})
    assert r.status_code == 200 and r.json()["api_key"].startswith("mk_")
    assert count() == 0
    assert c.get("/v1/usage", headers={"x-api-key": r.json()["api_key"]}).status_code == 401  # the fake key is useless


@pytest.mark.parametrize("email", ["x@mailinator.com", "x@sub.yopmail.com", "X@Guerrillamail.COM"])
def test_disposable_blocked(c, email):
    r = c.post("/v1/signup", json={"email": email})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "disposable_email" and count() == 0


def test_disposable_check_can_be_disabled_and_extended(c, monkeypatch):
    monkeypatch.setenv("VOCALFACE_BLOCKED_EMAIL_DOMAINS", "evil.test")
    assert c.post("/v1/signup", json={"email": "a@evil.test"}).status_code == 422
    monkeypatch.setenv("VOCALFACE_BLOCK_DISPOSABLE_EMAIL", "0")
    assert c.post("/v1/signup", json={"email": "a@mailinator.com"}).status_code == 200


@pytest.mark.parametrize("email", ["", "nope", "a@b", "a b@example.org", "a@@example.org", "a" * 300 + "@example.org"])
def test_invalid_email(c, email):
    assert c.post("/v1/signup", json={"email": email}).status_code == 422


def test_per_ip_daily_cap(c, monkeypatch):
    monkeypatch.setenv("VOCALFACE_SIGNUP_PER_IP_DAY", "2")
    codes = [c.post("/v1/signup", json={"email": f"u{i}@example.org"}).status_code for i in range(4)]
    assert codes == [200, 200, 429, 429]


def test_global_hourly_cap(c, monkeypatch):
    monkeypatch.setenv("VOCALFACE_SIGNUP_GLOBAL_HOUR", "1")
    assert c.post("/v1/signup", json={"email": "a@example.org"}).status_code == 200
    assert c.post("/v1/signup", json={"email": "b@example.org"}).status_code == 429


def test_unique_email_normalised(c, monkeypatch):
    monkeypatch.setenv("VOCALFACE_SIGNUP_UNIQUE_EMAIL", "1")
    assert c.post("/v1/signup", json={"email": "john.doe@gmail.com"}).status_code == 200
    r = c.post("/v1/signup", json={"email": "johndoe+promo@gmail.com"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "email_taken"
    assert c.post("/v1/signup", json={"email": "other@example.org"}).status_code == 200


def test_pow_flow(c, monkeypatch):
    assert c.get("/v1/signup/challenge").json() == {"bits": 0}
    monkeypatch.setenv("VOCALFACE_SIGNUP_POW_BITS", "10")
    ch = c.get("/v1/signup/challenge").json()
    assert ch["bits"] == 10
    r = c.post("/v1/signup", json={"email": "a@example.org"})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "pow_required"
    bad = c.post("/v1/signup", json={"email": "a@example.org", "pow_challenge": ch["challenge"], "pow_nonce": "wrong-nonce-xx"})
    assert bad.status_code == 400
    nonce = sg.solve(ch["challenge"], 10)
    ok = c.post("/v1/signup", json={"email": "a@example.org", "pow_challenge": ch["challenge"], "pow_nonce": nonce})
    assert ok.status_code == 200
    replay = c.post("/v1/signup", json={"email": "b@example.org", "pow_challenge": ch["challenge"], "pow_nonce": nonce})
    assert replay.status_code == 400  # single use


def test_pow_forged_expired_and_downgraded_challenges_rejected(c, monkeypatch):
    monkeypatch.setenv("VOCALFACE_SIGNUP_POW_BITS", "8")
    ch = c.get("/v1/signup/challenge").json()["challenge"]
    exp, rnd, bits, mac = ch.split(".")
    forged = f"{exp}.{rnd}.0.{mac}"  # lower the difficulty: MAC no longer matches
    assert not sg.verify_pow(forged, sg.solve(forged, 0))
    assert not sg.verify_pow(f"1.{rnd}.{bits}.{mac}", "0")  # tampered expiry
    import time
    payload = f"{int(time.time()) - 5}.abc.8"
    assert not sg.verify_pow(f"{payload}.{sg._mac(payload)}", sg.solve(f"{payload}.{sg._mac(payload)}", 8))  # genuinely expired
