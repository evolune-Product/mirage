"""Plans, payment providers, webhook verification, ledger, margin calculator.

Providers never fake a payment: with missing keys they raise ProviderNotConfigured.
"""
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .db import Account
from .models_billing import AccountPlan, LedgerEntry, PaymentEvent


class ProviderNotConfigured(Exception):
    pass


class SignatureError(Exception):
    pass


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_cents: int
    included_minutes: int
    overage_cents_per_min: int


PLANS = {
    "free": Plan("free", "Free", 0, 10, 0),  # no overage: hard stop at 0 credits
    "starter": Plan("starter", "Starter", 1900, 120, 20),
    "pro": Plan("pro", "Pro", 7900, 600, 15),
}
# top-up packs: id -> (minutes, price_cents)
TOPUPS = {"topup_60": (60, 1200), "topup_300": (300, 5000), "topup_1000": (1000, 15000)}


def usd_inr() -> float:
    return float(os.getenv("MIRAGE_USD_INR", "85"))


def resolve_sku(kind: str, sku: str) -> tuple[int, int, str]:
    """-> (credit_seconds, price_cents_usd, description). Raises KeyError."""
    if kind == "plan":
        p = PLANS[sku]
        if p.price_cents == 0:
            raise KeyError(sku)
        return p.included_minutes * 60, p.price_cents, f"Mirage {p.name} plan (one month)"
    mins, cents = TOPUPS[sku]
    return mins * 60, cents, f"Mirage {mins} minute top-up"


# ---------------- providers ----------------
class StripeProvider:
    name = "stripe"

    def __init__(self):
        self.key = os.getenv("STRIPE_SECRET_KEY")
        self.whsec = os.getenv("STRIPE_WEBHOOK_SECRET")

    def create_checkout(self, *, account_id, kind, sku, seconds, cents, desc, success_url, cancel_url):
        if not self.key:
            raise ProviderNotConfigured("Stripe is not configured: set STRIPE_SECRET_KEY")
        data = {
            "mode": "payment",
            "success_url": success_url, "cancel_url": cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(cents),
            "line_items[0][price_data][product_data][name]": desc,
            "metadata[account_id]": account_id, "metadata[kind]": kind,
            "metadata[sku]": sku, "metadata[seconds]": str(seconds),
        }
        r = httpx.post("https://api.stripe.com/v1/checkout/sessions", data=data, auth=(self.key, ""), timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"Stripe error {r.status_code}: {r.text[:300]}")
        j = r.json()
        return {"provider": "stripe", "checkout_url": j["url"], "id": j["id"]}

    def verify(self, payload: bytes, header: str, tolerance: int = 300, now_ts: float | None = None):
        if not self.whsec:
            raise ProviderNotConfigured("Stripe webhook not configured: set STRIPE_WEBHOOK_SECRET")
        try:
            parts = dict(p.split("=", 1) for p in header.split(","))
            ts = int(parts["t"])
        except Exception:
            raise SignatureError("malformed Stripe-Signature")
        sigs = [p.split("=", 1)[1] for p in header.split(",") if p.startswith("v1=")]
        expected = hmac.new(self.whsec.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, s) for s in sigs):
            raise SignatureError("bad signature")
        if abs((now_ts or time.time()) - ts) > tolerance:
            raise SignatureError("timestamp outside tolerance")

    def parse(self, payload: bytes):
        """-> (event_id, credit dict or None)"""
        ev = json.loads(payload)
        if ev.get("type") != "checkout.session.completed":
            return ev.get("id", ""), None
        obj = ev["data"]["object"]
        if obj.get("payment_status") != "paid":
            return ev["id"], None
        m = obj.get("metadata") or {}
        return ev["id"], _credit_from_meta(m, obj.get("amount_total", 0), (obj.get("currency") or "usd"))


class RazorpayProvider:
    name = "razorpay"

    def __init__(self):
        self.key_id = os.getenv("RAZORPAY_KEY_ID")
        self.key_secret = os.getenv("RAZORPAY_KEY_SECRET")
        self.whsec = os.getenv("RAZORPAY_WEBHOOK_SECRET")

    def create_checkout(self, *, account_id, kind, sku, seconds, cents, desc, success_url, cancel_url):
        if not (self.key_id and self.key_secret):
            raise ProviderNotConfigured("Razorpay is not configured: set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET")
        paise = int(round(cents / 100 * usd_inr() * 100))
        body = {
            "amount": paise, "currency": "INR", "description": desc,
            "callback_url": success_url, "callback_method": "get",
            "notes": {"account_id": account_id, "kind": kind, "sku": sku, "seconds": str(seconds)},
        }
        r = httpx.post("https://api.razorpay.com/v1/payment_links", json=body,
                       auth=(self.key_id, self.key_secret), timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"Razorpay error {r.status_code}: {r.text[:300]}")
        j = r.json()
        return {"provider": "razorpay", "checkout_url": j["short_url"], "id": j["id"]}

    def verify(self, payload: bytes, header: str):
        if not self.whsec:
            raise ProviderNotConfigured("Razorpay webhook not configured: set RAZORPAY_WEBHOOK_SECRET")
        expected = hmac.new(self.whsec.encode(), payload, hashlib.sha256).hexdigest()
        if not header or not hmac.compare_digest(expected, header):
            raise SignatureError("bad signature")

    def parse(self, payload: bytes, event_id_header: str = ""):
        ev = json.loads(payload)
        if ev.get("event") != "payment_link.paid":
            return event_id_header, None
        pl = ev["payload"]["payment_link"]["entity"]
        pay = ev["payload"].get("payment", {}).get("entity", {})
        eid = event_id_header or pay.get("id") or pl.get("id", "")
        return eid, _credit_from_meta(pl.get("notes") or {}, pl.get("amount_paid", 0), "inr")


def _credit_from_meta(m: dict, amount: int, currency: str):
    if not m.get("account_id"):
        return None
    kind, sku = m.get("kind", "topup"), m.get("sku", "")
    try:
        seconds, cents, _ = resolve_sku(kind, sku)  # trust our catalog, not the metadata
    except KeyError:
        return None
    return {"account_id": m["account_id"], "kind": kind, "sku": sku, "seconds": seconds,
            "cents": cents, "currency": currency}


def get_provider(name: str):
    if name == "stripe":
        return StripeProvider()
    if name == "razorpay":
        return RazorpayProvider()
    raise KeyError(name)


# ---------------- ledger ----------------
def apply_credit(session: Session, provider: str, event_id: str, credit: dict) -> bool:
    """Idempotently add credits. Returns False if this event was already processed."""
    if not event_id:
        raise ValueError("event id required for idempotency")
    acc = session.get(Account, credit["account_id"])
    if not acc:
        return False
    try:
        session.add(PaymentEvent(provider=provider, event_id=event_id, account_id=acc.id))
        session.flush()
    except IntegrityError:
        session.rollback()
        return False
    acc.credits_seconds += credit["seconds"]
    session.add(acc)
    session.add(LedgerEntry(account_id=acc.id, kind=credit["kind"], seconds=credit["seconds"],
                            amount_cents=credit["cents"], currency="usd",
                            ref=f"{provider}:{event_id}", note=credit["sku"]))
    if credit["kind"] == "plan":
        row = session.get(AccountPlan, acc.id) or AccountPlan(account_id=acc.id)
        row.plan = credit["sku"]
        session.add(row)
    session.commit()
    return True


def record_usage(session: Session, acc: Account, seconds: int, ref: str, note: str = "") -> bool:
    """Write a usage ledger row (idempotent on ref, e.g. 'conv:<id>'). Does NOT touch
    Account.credits_seconds; the caller (conversation end) already deducts that."""
    try:
        session.add(LedgerEntry(account_id=acc.id, kind="usage", seconds=-abs(seconds), ref=ref, note=note))
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


def current_plan(session: Session, account_id: str) -> Plan:
    row = session.get(AccountPlan, account_id)
    return PLANS.get(row.plan if row else "free", PLANS["free"])


# ---------------- metering, allowances, overage (billing enforcement) ----------------
# Units: credit balance = seconds (Account.credits_seconds). Overage money = MILLICENTS (1/1000 cent) so per-second
# pricing never drifts. Plans are one-time monthly purchases (see /billing/plans), so "reset" = lapse + expiry at
# the calendar-month boundary (UTC), never a proration.
import math
from datetime import datetime, timedelta, timezone

from .models_platform import BillingSettings, OverageCharge

WORDS_PER_SECOND = 2.5  # ~150 wpm narration: how a video script is converted to billable seconds
MIN_VIDEO_SECONDS = 2


def period_key(now: datetime | None = None) -> str:
    n = now or datetime.now(timezone.utc)
    return f"{n.year:04d}-{n.month:02d}"


def period_bounds(period: str) -> tuple[datetime, datetime]:
    y, m = int(period[:4]), int(period[5:7])
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)
    return start, end


def overage_millicents(seconds: int, cents_per_min: int) -> int:
    """Exact per-second price, rounded UP to a whole millicent: ceil(seconds * cents_per_min * 1000 / 60)."""
    if seconds <= 0 or cents_per_min <= 0:
        return 0
    return -(-(seconds * cents_per_min * 1000) // 60)


def overage_seconds_for(millicents: int, cents_per_min: int) -> int:
    """How many whole seconds `millicents` buys (floor), the inverse of overage_millicents."""
    if millicents <= 0 or cents_per_min <= 0:
        return 0
    return (millicents * 60) // (cents_per_min * 1000)


def estimate_video_seconds(script: str) -> int:
    return max(math.ceil(len((script or "").split()) / WORDS_PER_SECOND), MIN_VIDEO_SECONDS)


def get_overage_settings(session: Session, account_id: str) -> BillingSettings:
    return session.get(BillingSettings, account_id) or BillingSettings(account_id=account_id)


def set_overage(session: Session, account_id: str, enabled: bool, cap_cents: int | None = None) -> BillingSettings:
    """Raises ValueError when the request makes no sense (free plan, missing/zero cap)."""
    plan = current_plan(session, account_id)
    row = session.get(BillingSettings, account_id) or BillingSettings(account_id=account_id)
    cap = row.overage_cap_cents if cap_cents is None else cap_cents
    if cap < 0:
        raise ValueError("cap must be >= 0")
    if enabled:
        if plan.overage_cents_per_min <= 0:
            raise ValueError(f"the {plan.name} plan has no overage rate; upgrade to enable overage")
        if cap <= 0:
            raise ValueError("a monthly spend cap greater than 0 is required to enable overage")
    row.overage_enabled, row.overage_cap_cents = enabled, cap
    row.updated_at = datetime.now(timezone.utc)
    session.add(row); session.commit(); session.refresh(row)
    return row


def overage_spent_millicents(session: Session, account_id: str, period: str) -> int:
    rows = session.exec(select(OverageCharge.amount_millicents).where(
        OverageCharge.account_id == account_id, OverageCharge.period == period)).all()
    return int(sum(rows))


def overage_headroom_seconds(session: Session, acc: Account, now: datetime | None = None) -> int:
    """Seconds of overage still purchasable this month (0 when disabled / free plan / cap reached)."""
    st = session.get(BillingSettings, acc.id)
    plan = current_plan(session, acc.id)
    if not st or not st.overage_enabled or plan.overage_cents_per_min <= 0 or st.overage_cap_cents <= 0:
        return 0
    left = st.overage_cap_cents * 1000 - overage_spent_millicents(session, acc.id, period_key(now))
    return overage_seconds_for(left, plan.overage_cents_per_min)


def session_allowance(session: Session, acc: Account, now: datetime | None = None) -> int:
    """Seconds this account may consume right now: credit balance + remaining overage headroom."""
    return max(acc.credits_seconds, 0) + overage_headroom_seconds(session, acc, now)


def authorize(session: Session, acc: Account, needed_seconds: int = 1) -> int:
    """HTTP 402 unless the account can cover `needed_seconds`; returns the allowance. Never mutates."""
    from fastapi import HTTPException

    allowance = session_allowance(session, acc)
    if allowance < max(needed_seconds, 1):
        st = session.get(BillingSettings, acc.id)
        if st and st.overage_enabled and acc.credits_seconds <= 0 and overage_headroom_seconds(session, acc) <= 0:
            raise HTTPException(402, "overage spend cap reached for this month")
        raise HTTPException(402, "out of credits")
    return allowance


def settle_usage(session: Session, acc: Account, seconds: int, ref: str, note: str = "") -> dict:
    """End-of-session accounting, idempotent on `ref` (e.g. 'conv:<id>', 'video:<id>'): spend credits first, meter
    the rest as overage (only if enabled and within this month's cap). Writes the usage ledger row (-seconds) and,
    for the overage part, an OverageCharge. Returns the split."""
    seconds = max(int(seconds), 0)
    if session.exec(select(LedgerEntry).where(LedgerEntry.ref == ref)).first() is not None:
        return {"duplicate": True, "from_credits": 0, "overage_seconds": 0, "overage_millicents": 0}
    from_credits = min(max(acc.credits_seconds, 0), seconds)
    over = seconds - from_credits
    charged_s, charged_mc = 0, 0
    if over > 0:
        plan = current_plan(session, acc.id)
        head = overage_headroom_seconds(session, acc)
        charged_s = min(over, head)
        if charged_s > 0:
            period = period_key()
            st = get_overage_settings(session, acc.id)
            mc = overage_millicents(charged_s, plan.overage_cents_per_min)
            mc = min(mc, st.overage_cap_cents * 1000 - overage_spent_millicents(session, acc.id, period))  # never bill past the cap
            charged_mc = max(mc, 0)
            if charged_mc:
                session.add(OverageCharge(account_id=acc.id, period=period, seconds=charged_s, amount_millicents=charged_mc,
                                          rate_cents_per_min=plan.overage_cents_per_min, ref=ref))
    acc.credits_seconds = max(acc.credits_seconds - from_credits, 0)
    session.add(acc)
    detail = f"credits={from_credits};overage_s={charged_s};overage_mc={charged_mc}" + (f";{note}" if note else "")
    try:
        session.add(LedgerEntry(account_id=acc.id, kind="usage", seconds=-seconds, ref=ref, note=detail))
        session.commit()
    except IntegrityError:
        session.rollback()
        return {"duplicate": True, "from_credits": 0, "overage_seconds": 0, "overage_millicents": 0}
    return {"duplicate": False, "from_credits": from_credits, "overage_seconds": charged_s, "overage_millicents": charged_mc,
            "unbilled_seconds": over - charged_s}


def charge_video(session: Session, acc: Account, video_id: str, script: str) -> dict:
    """Bill a video when a worker starts it (so every creation path, including bulk, is covered). Raises HTTP 402
    if the account cannot cover the estimated narration length; idempotent per video."""
    est = estimate_video_seconds(script)
    ref = f"video:{video_id}"
    if session.exec(select(LedgerEntry).where(LedgerEntry.ref == ref)).first() is not None:
        return {"duplicate": True}
    authorize(session, acc, est)
    out = settle_usage(session, acc, est, ref, f"video_est_s={est}")
    out["estimated_seconds"] = est
    return out


def refund_video(session: Session, video_id: str) -> bool:
    """Undo charge_video for a render that failed. Returns False if nothing to refund / already refunded."""
    ref = f"video:{video_id}"
    row = session.exec(select(LedgerEntry).where(LedgerEntry.ref == ref)).first()
    if row is None or session.exec(select(LedgerEntry).where(LedgerEntry.ref == f"refund:{video_id}")).first() is not None:
        return False
    parts = dict(p.split("=", 1) for p in row.note.split(";") if "=" in p)
    back = int(parts.get("credits", 0))
    acc = session.get(Account, row.account_id)
    if acc is None:
        return False
    acc.credits_seconds += back
    session.add(acc)
    oc = session.exec(select(OverageCharge).where(OverageCharge.ref == ref)).first()
    if oc is not None:
        session.delete(oc)
    session.add(LedgerEntry(account_id=acc.id, kind="adjust", seconds=-row.seconds, ref=f"refund:{video_id}", note="video render failed: refund"))
    session.commit()
    return True


# ---------------- reports ----------------
def usage_report(session: Session, acc: Account, period: str | None = None) -> dict:
    period = period or period_key()
    start, end = period_bounds(period)
    rows = session.exec(select(LedgerEntry).where(LedgerEntry.account_id == acc.id, LedgerEntry.created_at >= start,
                                                  LedgerEntry.created_at < end)).all()
    by = {"conversation": 0, "video": 0, "other": 0}
    daily: dict[str, int] = {}
    granted = 0
    for r in rows:
        if r.kind == "usage":
            k = "conversation" if (r.ref or "").startswith("conv:") else "video" if (r.ref or "").startswith("video:") else "other"
            by[k] += -r.seconds
            d = r.created_at.strftime("%Y-%m-%d")
            daily[d] = daily.get(d, 0) + -r.seconds
        elif r.kind in ("topup", "plan", "grant"):
            granted += r.seconds
    plan = current_plan(session, acc.id)
    st = get_overage_settings(session, acc.id)
    spent = overage_spent_millicents(session, acc.id, period)
    charges = session.exec(select(OverageCharge).where(OverageCharge.account_id == acc.id, OverageCharge.period == period)).all()
    return {
        "period": period, "period_start": start.isoformat(), "period_end": end.isoformat(),
        "plan": {"id": plan.id, "name": plan.name, "included_minutes": plan.included_minutes, "overage_cents_per_min": plan.overage_cents_per_min},
        "credits_seconds": acc.credits_seconds,
        "granted_seconds": granted,
        "used_seconds": sum(by.values()), "used_seconds_by_kind": by,
        "daily_used_seconds": [{"date": d, "seconds": v} for d, v in sorted(daily.items())],
        "overage": {"enabled": st.overage_enabled, "cap_cents": st.overage_cap_cents, "rate_cents_per_min": plan.overage_cents_per_min,
                    "seconds": sum(c.seconds for c in charges), "spent_cents": round(spent / 1000, 3),
                    "remaining_cents": round(max(st.overage_cap_cents * 1000 - spent, 0) / 1000, 3) if st.overage_enabled else 0,
                    "headroom_seconds": overage_headroom_seconds(session, acc) if period == period_key() else 0},
    }


# ---------------- monthly reset job ----------------
def run_monthly_reset(session: Session, now: datetime | None = None) -> dict:
    """Idempotent; safe to run hourly or from cron. For every account on a paid plan purchased in an EARLIER calendar month:
    1. the unused part of that month's plan allowance expires (plan credits are consumed before top-ups):
       expire = min(balance, max(plan_seconds_granted_in_purchase_month - seconds_used_in_that_month, 0)),
    2. the plan lapses to 'free' (plans are one-time monthly purchases, there is no auto-renewal),
    3. overage settings are switched off when the new plan has no overage rate (the cap itself resets via the period key).
    Top-up credits never expire. No proration: purchase day inside the month does not change the allowance."""
    n = now or datetime.now(timezone.utc)
    this = period_key(n)
    out = {"period": this, "lapsed": 0, "expired_seconds": 0, "accounts": []}
    for ap in session.exec(select(AccountPlan).where(AccountPlan.plan != "free")).all():
        bought = ap.updated_at if ap.updated_at.tzinfo else ap.updated_at.replace(tzinfo=timezone.utc)
        pk = period_key(bought)
        if pk >= this:
            continue
        start, end = period_bounds(pk)
        rows = session.exec(select(LedgerEntry).where(LedgerEntry.account_id == ap.account_id, LedgerEntry.created_at >= start,
                                                      LedgerEntry.created_at < end)).all()
        granted = sum(r.seconds for r in rows if r.kind == "plan")
        used = sum(-r.seconds for r in rows if r.kind == "usage")
        acc = session.get(Account, ap.account_id)
        expire = 0
        if acc is not None:
            expire = min(max(acc.credits_seconds, 0), max(granted - used, 0))
            if expire > 0:
                try:
                    session.add(LedgerEntry(account_id=acc.id, kind="expire", seconds=-expire, ref=f"expire:{acc.id}:{pk}",
                                            note=f"unused {ap.plan} allowance from {pk} expired"))
                    session.flush()
                    acc.credits_seconds -= expire
                    session.add(acc)
                except IntegrityError:  # already expired by an earlier run
                    session.rollback()
                    expire = 0
        old = ap.plan
        ap.plan, ap.updated_at = "free", n
        session.add(ap)
        st = session.get(BillingSettings, ap.account_id)
        if st and st.overage_enabled:
            st.overage_enabled = False
            session.add(st)
        session.commit()
        out["lapsed"] += 1; out["expired_seconds"] += expire
        out["accounts"].append({"account_id": ap.account_id, "plan": old, "expired_seconds": expire})
    return out


async def reset_loop(interval: float = 3600.0) -> None:
    """API-process background task (disable with MIRAGE_BILLING_LOOP=0). Idempotent, so extra processes are harmless."""
    import asyncio

    from . import db

    while True:
        try:
            def _run():
                with Session(db.engine) as s:
                    return run_monthly_reset(s)
            await asyncio.to_thread(_run)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(interval)


# ---------------- margin calculator ----------------
def margin(price_per_min: float, cost_per_min: float) -> dict:
    gross = price_per_min - cost_per_min
    return {"price_per_min": price_per_min, "cost_per_min": cost_per_min, "gross_per_min": gross,
            "margin_pct": (gross / price_per_min * 100) if price_per_min else float("nan")}


def gpu_cost_per_user_minute(gpu_usd_per_hour: float, concurrent_streams_per_gpu: float, utilization: float) -> float:
    """Rented-GPU cost per user-minute. utilization = fraction of paid hours with live sessions."""
    if concurrent_streams_per_gpu <= 0 or utilization <= 0:
        raise ValueError("streams and utilization must be > 0")
    return gpu_usd_per_hour / 60 / (concurrent_streams_per_gpu * utilization)


def scenarios() -> list[dict]:
    rows = []
    for name, gpu_hr, streams, util, other in [
        ("Local/free voice only (no rendered face)", 0.0, 1, 1, 0.0),
        ("Consumer GPU rental, 1 stream, 30% util", 0.40, 1, 0.30, 0.005),
        ("Consumer GPU rental, 2 streams, 50% util", 0.40, 2, 0.50, 0.005),
        ("Datacenter GPU (L4/A10-class), 3 streams, 50% util", 0.80, 3, 0.50, 0.005),
        ("Datacenter GPU, 1 stream, 20% util (bad case)", 0.80, 1, 0.20, 0.005),
    ]:
        cost = (gpu_cost_per_user_minute(gpu_hr, streams, util) if gpu_hr else 0.0) + other
        row = {"scenario": name, "cost_per_min": round(cost, 4)}
        for pid, p in PLANS.items():
            if p.price_cents:
                row[pid] = round(margin(p.price_cents / 100 / p.included_minutes, cost)["margin_pct"], 1)
        rows.append(row)
    return rows


if __name__ == "__main__":
    for r in scenarios():
        print(r)
