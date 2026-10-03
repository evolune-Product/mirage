import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import billing
from ..auth import current_account
from ..billing import (PLANS, TOPUPS, ProviderNotConfigured, SignatureError, apply_credit,
                       current_plan, get_provider, resolve_sku)
from ..db import Account, get_session
from ..models_billing import LedgerEntry
from ..safety import audit

router = APIRouter()


@router.get("/billing/plans")
def plans():
    return {
        "plans": [p.__dict__ for p in PLANS.values()],
        "topups": [{"sku": k, "minutes": m, "price_cents": c} for k, (m, c) in TOPUPS.items()],
        "note": "Plan purchase is a one-time monthly purchase, not an auto-renewing subscription.",
    }


@router.get("/billing/status")
def status(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    st = billing.get_overage_settings(s, acc.id)
    return {"plan": current_plan(s, acc.id).__dict__, "credits_seconds": acc.credits_seconds,
            "period": billing.period_key(),
            "overage": {"enabled": st.overage_enabled, "cap_cents": st.overage_cap_cents,
                        "headroom_seconds": billing.overage_headroom_seconds(s, acc)},
            "available_seconds": billing.session_allowance(s, acc)}


class OverageIn(BaseModel):
    enabled: bool
    cap_cents: int | None = None  # monthly spend cap in US cents; required (> 0) to enable


@router.get("/billing/overage")
def get_overage(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    plan = current_plan(s, acc.id)
    st = billing.get_overage_settings(s, acc.id)
    spent = billing.overage_spent_millicents(s, acc.id, billing.period_key())
    return {"enabled": st.overage_enabled, "cap_cents": st.overage_cap_cents, "rate_cents_per_min": plan.overage_cents_per_min,
            "available": plan.overage_cents_per_min > 0, "spent_cents": round(spent / 1000, 3),
            "headroom_seconds": billing.overage_headroom_seconds(s, acc), "period": billing.period_key()}


@router.put("/billing/overage")
def put_overage(body: OverageIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    try:
        st = billing.set_overage(s, acc.id, body.enabled, body.cap_cents)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit(s, acc.id, "billing.overage_set", "", f"enabled={st.overage_enabled} cap_cents={st.overage_cap_cents}")
    return get_overage(acc, s)


@router.get("/usage/report")
def usage_report(period: str | None = None, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Usage for one calendar month (UTC): seconds by kind, daily series, overage seconds + spend + cap."""
    if period is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise HTTPException(422, "period must be YYYY-MM")
    return billing.usage_report(s, acc, period)


class CheckoutIn(BaseModel):
    provider: str  # stripe | razorpay
    kind: str = "topup"  # topup | plan
    sku: str
    success_url: str = "http://localhost:3000/billing/success"
    cancel_url: str = "http://localhost:3000/billing/cancel"


@router.post("/billing/checkout")
def checkout(body: CheckoutIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    try:
        prov = get_provider(body.provider)
    except KeyError:
        raise HTTPException(400, "provider must be stripe or razorpay")
    try:
        seconds, cents, desc = resolve_sku(body.kind, body.sku)
    except KeyError:
        raise HTTPException(400, "unknown kind/sku")
    try:
        out = prov.create_checkout(account_id=acc.id, kind=body.kind, sku=body.sku, seconds=seconds,
                                   cents=cents, desc=desc, success_url=body.success_url, cancel_url=body.cancel_url)
    except ProviderNotConfigured as e:
        raise HTTPException(501, {"error": "provider_not_configured", "message": str(e)})
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    audit(s, acc.id, "billing.checkout_created", out["id"], f"{body.provider} {body.sku}")
    return out


def _finish(s: Session, provider: str, event_id: str, credit):
    if credit is None:
        return {"ok": True, "processed": False}
    applied = apply_credit(s, provider, event_id, credit)
    if applied:
        audit(s, credit["account_id"], "billing.credited", event_id, f"{provider} +{credit['seconds']}s")
    return {"ok": True, "processed": applied, "duplicate": not applied}


@router.post("/billing/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: str = Header(""), s: Session = Depends(get_session)):
    payload = await request.body()
    p = get_provider("stripe")
    try:
        p.verify(payload, stripe_signature)
    except ProviderNotConfigured as e:
        raise HTTPException(503, str(e))
    except SignatureError as e:
        raise HTTPException(400, f"invalid signature: {e}")
    eid, credit = p.parse(payload)
    return _finish(s, "stripe", eid, credit)


@router.post("/billing/webhooks/razorpay")
async def razorpay_webhook(request: Request, x_razorpay_signature: str = Header(""),
                           x_razorpay_event_id: str = Header(""), s: Session = Depends(get_session)):
    payload = await request.body()
    p = get_provider("razorpay")
    try:
        p.verify(payload, x_razorpay_signature)
    except ProviderNotConfigured as e:
        raise HTTPException(503, str(e))
    except SignatureError as e:
        raise HTTPException(400, f"invalid signature: {e}")
    eid, credit = p.parse(payload, x_razorpay_event_id)
    return _finish(s, "razorpay", eid, credit)


@router.get("/usage/ledger")
def ledger(limit: int = 100, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    return s.exec(select(LedgerEntry).where(LedgerEntry.account_id == acc.id)
                  .order_by(LedgerEntry.created_at.desc()).limit(min(limit, 500))).all()
