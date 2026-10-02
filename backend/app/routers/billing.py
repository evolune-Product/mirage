from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

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
    return {"plan": current_plan(s, acc.id).__dict__, "credits_seconds": acc.credits_seconds}


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
