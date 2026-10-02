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
