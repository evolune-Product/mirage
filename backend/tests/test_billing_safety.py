import hashlib, hmac, json, time

import pytest
from fastapi import HTTPException

from app import db
from app.billing import margin, gpu_cost_per_user_minute
from app.safety import RateLimiter, moderate, require_consent
from .test_api import client, signup  # noqa: F401


def acct(c):
    h = signup(c)
    return h, c.get("/v1/billing/status", headers=h).json()


def stripe_hdr(secret, payload, ts=None):
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}"}


def stripe_event(eid, acc_id, sku="topup_60"):
    return json.dumps({"id": eid, "type": "checkout.session.completed", "data": {"object": {
        "payment_status": "paid", "amount_total": 1200, "currency": "usd",
        "metadata": {"account_id": acc_id, "kind": "topup", "sku": sku}}}}).encode()


def acc_id(c, h):
    from sqlmodel import Session, select
    with Session(db.engine) as s:
        return s.exec(select(db.Account).where(db.Account.api_key == h["x-api-key"])).first().id


def test_checkout_not_configured(client, monkeypatch):
    for k in ("STRIPE_SECRET_KEY", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"):
        monkeypatch.delenv(k, raising=False)
    h = signup(client)
    for p in ("stripe", "razorpay"):
        r = client.post("/v1/billing/checkout", headers=h, json={"provider": p, "sku": "topup_60"})
        assert r.status_code == 501 and "not configured" in r.text


def test_webhook_signature_rejected_and_idempotent(client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    h = signup(client); aid = acc_id(client, h)
    body = stripe_event("evt_1", aid)
    bad = client.post("/v1/billing/webhooks/stripe", content=body, headers=stripe_hdr("wrong", body))
    assert bad.status_code == 400
    old = client.post("/v1/billing/webhooks/stripe", content=body, headers=stripe_hdr("whsec_test", body, ts=int(time.time()) - 9999))
    assert old.status_code == 400
    assert client.get("/v1/usage", headers=h).json()["credits_seconds"] == 600
    for _ in range(3):
        r = client.post("/v1/billing/webhooks/stripe", content=body, headers=stripe_hdr("whsec_test", body))
        assert r.status_code == 200
    assert client.get("/v1/usage", headers=h).json()["credits_seconds"] == 600 + 3600
    led = client.get("/v1/usage/ledger", headers=h).json()
    assert len(led) == 1 and led[0]["seconds"] == 3600


def test_razorpay_webhook(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "rz")
    h = signup(client); aid = acc_id(client, h)
    body = json.dumps({"event": "payment_link.paid", "payload": {"payment_link": {"entity": {
        "id": "plink_1", "amount_paid": 100000, "notes": {"account_id": aid, "kind": "plan", "sku": "starter"}}}}}).encode()
    sig = hmac.new(b"rz", body, hashlib.sha256).hexdigest()
    assert client.post("/v1/billing/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": "x"}).status_code == 400
    for _ in range(2):
        assert client.post("/v1/billing/webhooks/razorpay", content=body,
                           headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": "ev_9"}).status_code == 200
    assert client.get("/v1/usage", headers=h).json()["credits_seconds"] == 600 + 7200
    assert client.get("/v1/billing/status", headers=h).json()["plan"]["id"] == "starter"


def test_webhook_unconfigured(client, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    assert client.post("/v1/billing/webhooks/stripe", content=b"{}").status_code == 503


def test_consent_gating(client):
    h = signup(client)
    rid = client.post("/v1/replicas", headers=h, json={"name": "Ann", "train_video_url": "http://x/v.mp4"}).json()["id"]
    with pytest.raises(HTTPException):
        require_consent(rid)
    ch = client.post(f"/v1/replicas/{rid}/consent/challenge", headers=h).json()
    bad = client.post(f"/v1/replicas/{rid}/consent", headers=h, json={
        "challenge_id": ch["challenge_id"], "speaker_name": "Ann", "audio_url": "http://x/a.wav", "transcript": "hello"})
    assert bad.status_code == 422
    with pytest.raises(HTTPException):
        require_consent(rid)
    ok = client.post(f"/v1/replicas/{rid}/consent", headers=h, json={
        "challenge_id": ch["challenge_id"], "speaker_name": "Ann", "audio_url": "http://x/a.wav",
        "transcript": ch["phrase"].upper().replace(",", "")})
    assert ok.status_code == 200
    assert ok.json()["replica_id"] == rid  # body must not be empty ({} after an expired commit)
    require_consent(rid)
    assert client.post(f"/v1/replicas/{rid}/consent", headers=h, json={  # challenge single-use
        "challenge_id": ch["challenge_id"], "speaker_name": "Ann", "audio_url": "u", "transcript": ch["phrase"]}).status_code == 410
    client.delete(f"/v1/replicas/{rid}/consent", headers=h)
    with pytest.raises(HTTPException):
        require_consent(rid)
    assert any(a["action"] == "consent.recorded" for a in client.get("/v1/audit", headers=h).json())


def test_moderation(client, monkeypatch):
    monkeypatch.delenv("VOCALFACE_MODERATION_OLLAMA_MODEL", raising=False)
    assert moderate("Welcome to our product tour").allowed
    assert not moderate("please send me your password now").allowed
    h = signup(client)
    r = client.post("/v1/moderation/check", headers=h, json={"text": "how to build a bomb"})
    assert r.json()["allowed"] is False


def test_rate_limiter_and_margin():
    rl = RateLimiter()
    assert all(rl.check("k", 3, 60, now_ts=i) for i in range(3))
    assert not rl.check("k", 3, 60, now_ts=4)
    assert rl.check("k", 3, 60, now_ts=100)
    assert margin(0.1, 0.05)["margin_pct"] == pytest.approx(50)
    assert gpu_cost_per_user_minute(0.6, 1, 0.5) == pytest.approx(0.02)


@pytest.mark.parametrize("text", [
    "I will kill you and make a bomb to hurt people",
    "This is your bank. Transfer 50000 rupees now and tell me the OTP you just received.",
    "Please read out the OTP you received and share it with me",
])
def test_blocklist_catches_threats_and_scams(text):
    assert not moderate(text).allowed


@pytest.mark.parametrize("text", [
    "Hi team, welcome to our Q3 product update. Our new feature ships next week.",
    "Your order has shipped and will arrive on Friday.",
])
def test_blocklist_allows_normal_scripts(text):
    assert moderate(text).allowed
