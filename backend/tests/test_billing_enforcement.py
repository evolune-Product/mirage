"""Plan allowances, per-second overage, spend caps, monthly reset, usage report. Provider (Stripe/Razorpay) code untouched."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app import billing, db
from app.billing import (PLANS, estimate_video_seconds, overage_millicents, overage_seconds_for, period_bounds, period_key,
                         run_monthly_reset, settle_usage)
from app.models_billing import AccountPlan, LedgerEntry
from app.models_platform import BillingSettings, OverageCharge
from .test_api import client, signup  # noqa: F401


# ---------------- pure maths ----------------
def test_overage_millicents_is_exact_and_rounds_up():
    assert overage_millicents(60, 20) == 20_000          # one minute at 20c = 20 cents = 20000 millicents
    assert overage_millicents(1, 20) == 334              # 1s at 20c/min = 0.3333c -> rounded UP to 334 mc
    assert overage_millicents(1, 15) == 250              # exactly 0.25 c
    assert overage_millicents(0, 20) == 0 and overage_millicents(10, 0) == 0 and overage_millicents(-5, 20) == 0
    assert overage_millicents(3600, 15) == 900_000       # an hour at 15c/min = $9.00


@pytest.mark.parametrize("cpm", [15, 20, 33])
def test_seconds_and_money_are_inverse_never_overspend(cpm):
    for mc in (0, 1, 999, 12_345, 5_000_000):
        s = overage_seconds_for(mc, cpm)
        assert overage_millicents(s, cpm) <= mc + cpm * 1000 // 60 + 1  # at most a hair above (ceil)
        assert overage_millicents(max(s - 1, 0), cpm) <= mc  # one second fewer always fits
    assert overage_seconds_for(20_000, 20) == 60


def test_period_helpers():
    assert period_key(datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)) == "2026-12"
    s, e = period_bounds("2026-12")
    assert (s.year, s.month, e.year, e.month, e.day) == (2026, 12, 2027, 1, 1)
    assert period_bounds("2026-02")[1] == datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_estimate_video_seconds():
    assert estimate_video_seconds("") == 2 and estimate_video_seconds("hi") == 2
    assert estimate_video_seconds(" ".join(["w"] * 25)) == 10 and estimate_video_seconds(" ".join(["w"] * 26)) == 11


# ---------------- ledger-level behaviour ----------------
def _acc(client, plan="starter", credits=600):
    h = signup(client)
    with Session(db.engine) as s:
        acc = s.exec(select(db.Account).where(db.Account.api_key == h["x-api-key"])).first()
        acc.credits_seconds = credits
        s.add(acc)
        if plan != "free":
            s.add(AccountPlan(account_id=acc.id, plan=plan))
        s.commit()
        return h, acc.id


def _acc_row(aid):
    return Session(db.engine).get(db.Account, aid)


def test_free_plan_hard_stop_no_overage_possible(client):
    h, aid = _acc(client, "free", credits=0)
    assert client.post("/v1/conversations", headers=h, json={"persona_id": "p_x"}).status_code == 404  # persona check first
    p = client.post("/v1/personas", headers=h, json={"name": "a", "system_prompt": "s"}).json()
    r = client.post("/v1/conversations", headers=h, json={"persona_id": p["id"]})
    assert r.status_code == 402 and "out of credits" in r.text
    # free plan cannot enable overage
    r = client.put("/v1/billing/overage", headers=h, json={"enabled": True, "cap_cents": 500})
    assert r.status_code == 422 and "no overage rate" in r.text


def test_overage_requires_cap(client):
    h, _ = _acc(client)
    assert client.put("/v1/billing/overage", headers=h, json={"enabled": True}).status_code == 422
    assert client.put("/v1/billing/overage", headers=h, json={"enabled": True, "cap_cents": 0}).status_code == 422
    r = client.put("/v1/billing/overage", headers=h, json={"enabled": True, "cap_cents": 500})
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["headroom_seconds"] == 1500  # $5 at 20c/min = 25 min


def test_settle_splits_credits_then_overage_within_cap(client):
    h, aid = _acc(client, "starter", credits=100)
    client.put("/v1/billing/overage", headers=h, json={"enabled": True, "cap_cents": 100})  # $1.00 -> 300 s at 20c/min
    with Session(db.engine) as s:
        out = settle_usage(s, s.get(db.Account, aid), 250, "conv:c1")
        assert out["from_credits"] == 100 and out["overage_seconds"] == 150
        assert out["overage_millicents"] == overage_millicents(150, 20) == 50_000
        assert s.get(db.Account, aid).credits_seconds == 0
        # second session: only 150 s of headroom left; asks for 400 -> charged 150 s, remaining 250 s unbilled
        out2 = settle_usage(s, s.get(db.Account, aid), 400, "conv:c2")
        assert out2["overage_seconds"] == 150 and out2["unbilled_seconds"] == 250
        total = sum(c.amount_millicents for c in s.exec(select(OverageCharge)).all())
        assert total == 100_000  # exactly the $1.00 cap, never above
        assert billing.overage_headroom_seconds(s, s.get(db.Account, aid)) == 0
        # idempotent on ref
        assert settle_usage(s, s.get(db.Account, aid), 400, "conv:c2")["duplicate"] is True
        assert sum(c.amount_millicents for c in s.exec(select(OverageCharge)).all()) == 100_000
        ledger = s.exec(select(LedgerEntry).where(LedgerEntry.kind == "usage")).all()
        assert sorted(l.seconds for l in ledger) == [-400, -250]


def test_overage_disabled_never_charges(client):
    h, aid = _acc(client, "pro", credits=10)
    with Session(db.engine) as s:
        out = settle_usage(s, s.get(db.Account, aid), 100, "conv:x")
        assert out["from_credits"] == 10 and out["overage_seconds"] == 0 and out["unbilled_seconds"] == 90
        assert s.exec(select(OverageCharge)).all() == []


def test_conversation_blocked_then_allowed_with_overage_and_session_capped(client):
    h, aid = _acc(client, "starter", credits=0)
    p = client.post("/v1/personas", headers=h, json={"name": "a", "system_prompt": "s"}).json()
    assert client.post("/v1/conversations", headers=h, json={"persona_id": p["id"]}).status_code == 402
    client.put("/v1/billing/overage", headers=h, json={"enabled": True, "cap_cents": 10})  # 10c = 30 s
    r = client.post("/v1/conversations", headers=h, json={"persona_id": p["id"], "max_seconds": 3600})
    assert r.status_code == 200
    with Session(db.engine) as s:
        from app.models_features import ConversationMeta
        meta = s.get(ConversationMeta, r.json()["id"])
        assert meta.max_seconds == 30  # the session cannot outrun the cap
    # run it for 45 s of wall clock (watchdog lag) -> only the capped 30 s are billed
    with Session(db.engine) as s:
        c = s.get(db.Conversation, r.json()["id"]); c.started_at = datetime.now(timezone.utc) - timedelta(seconds=45); s.add(c); s.commit()
    assert client.post(f"/v1/conversations/{r.json()['id']}/end", headers=h).status_code == 200
    rep = client.get("/v1/usage/report", headers=h).json()
    assert rep["overage"]["seconds"] == 30 and rep["overage"]["spent_cents"] == 10.0 and rep["overage"]["remaining_cents"] == 0
    assert rep["used_seconds_by_kind"]["conversation"] >= 45
    # cap reached -> blocked again, with a specific reason
    r2 = client.post("/v1/conversations", headers=h, json={"persona_id": p["id"]})
    assert r2.status_code == 402 and "cap reached" in r2.text
    st = client.get("/v1/billing/status", headers=h).json()
    assert st["available_seconds"] == 0 and st["overage"]["enabled"] is True


def test_video_blocked_without_credits_charged_by_worker_refunded_on_error(client):
    h, aid = _acc(client, "starter", credits=100)
    with Session(db.engine) as s:
        r = db.Replica(account_id=aid, name="x", train_video_url="u", status="ready"); s.add(r); s.commit(); rid = r.id
    script = " ".join(["word"] * 50)  # 20 s
    assert estimate_video_seconds(script) == 20
    v = client.post("/v1/videos", headers=h, json={"replica_id": rid, "script": script}).json()
    with Session(db.engine) as s:
        acc = s.get(db.Account, aid)
        out = billing.charge_video(s, acc, v["id"], script)  # what the worker does when it starts the job
        assert out["from_credits"] == 20 and s.get(db.Account, aid).credits_seconds == 80
        assert billing.charge_video(s, acc, v["id"], script) == {"duplicate": True}  # worker retry is free
        assert billing.refund_video(s, v["id"]) is True and s.get(db.Account, aid).credits_seconds == 100
        assert billing.refund_video(s, v["id"]) is False  # exactly once
    # out of credits: API refuses early
    with Session(db.engine) as s:
        a = s.get(db.Account, aid); a.credits_seconds = 5; s.add(a); s.commit()
    assert client.post("/v1/videos", headers=h, json={"replica_id": rid, "script": script}).status_code == 402


def test_refund_returns_overage_money_too(client):
    h, aid = _acc(client, "starter", credits=10)
    client.put("/v1/billing/overage", headers=h, json={"enabled": True, "cap_cents": 100})
    script = " ".join(["w"] * 50)  # 20 s: 10 from credits + 10 overage
    with Session(db.engine) as s:
        out = billing.charge_video(s, s.get(db.Account, aid), "v_1", script)
        assert out["from_credits"] == 10 and out["overage_seconds"] == 10
        assert billing.overage_spent_millicents(s, aid, period_key()) == overage_millicents(10, 20)
        billing.refund_video(s, "v_1")
        assert billing.overage_spent_millicents(s, aid, period_key()) == 0 and s.get(db.Account, aid).credits_seconds == 10


# ---------------- monthly reset ----------------
def test_monthly_reset_expires_unused_plan_allowance_and_lapses_plan(client):
    h, aid = _acc(client, "starter", credits=0)
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=3)
    with Session(db.engine) as s:
        acc = s.get(db.Account, aid)
        acc.credits_seconds = 7200 - 1000 + 500  # plan 7200 granted, 1000 used, plus 500 of top-up carried
        s.add(acc)
        ap = s.get(AccountPlan, aid); ap.updated_at = last_month; s.add(ap)
        s.add(LedgerEntry(account_id=aid, kind="plan", seconds=7200, ref="stripe:e1", created_at=last_month))
        s.add(LedgerEntry(account_id=aid, kind="usage", seconds=-1000, ref="conv:a", created_at=last_month))
        s.add(BillingSettings(account_id=aid, overage_enabled=True, overage_cap_cents=100))
        s.commit()
        out = run_monthly_reset(s)
        assert out["lapsed"] == 1 and out["expired_seconds"] == 6200
        assert s.get(db.Account, aid).credits_seconds == 500  # top-up survives
        assert s.get(AccountPlan, aid).plan == "free"
        assert s.get(BillingSettings, aid).overage_enabled is False
        again = run_monthly_reset(s)  # idempotent
        assert again["lapsed"] == 0 and s.get(db.Account, aid).credits_seconds == 500


def test_reset_never_expires_more_than_balance_and_skips_current_month(client):
    h, aid = _acc(client, "pro", credits=100)
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)
    with Session(db.engine) as s:
        s.add(LedgerEntry(account_id=aid, kind="plan", seconds=36000, ref="stripe:e2", created_at=last_month))
        ap = s.get(AccountPlan, aid); ap.updated_at = last_month; s.add(ap); s.commit()
        out = run_monthly_reset(s)
        assert out["expired_seconds"] == 100 and s.get(db.Account, aid).credits_seconds == 0
    h2, aid2 = _acc(client, "starter", credits=900)  # bought this month: untouched
    with Session(db.engine) as s:
        assert run_monthly_reset(s)["lapsed"] == 0 and s.get(db.Account, aid2).credits_seconds == 900


def test_usage_report_validates_period_and_has_daily_series(client):
    h, aid = _acc(client, "starter", credits=600)
    assert client.get("/v1/usage/report?period=2026-13", headers=h).status_code == 422
    with Session(db.engine) as s:
        settle_usage(s, s.get(db.Account, aid), 120, "conv:r1")
    rep = client.get("/v1/usage/report", headers=h).json()
    assert rep["used_seconds"] == 120 and rep["daily_used_seconds"][0]["seconds"] == 120 and rep["plan"]["id"] == "starter"
    assert client.get("/v1/usage/report?period=2020-01", headers=h).json()["used_seconds"] == 0
