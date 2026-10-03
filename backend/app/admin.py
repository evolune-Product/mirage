"""Operator CLI. Run from backend/ against whatever MIRAGE_DB_URL points at (never prints API keys).

  python -m app.admin stats
  python -m app.admin accounts [--search EMAIL_PART] [--limit 50]
  python -m app.admin account <account_id|email>
  python -m app.admin grant <account_id|email> --minutes 60 [--seconds N] [--note "support credit"]
  python -m app.admin plan <account_id|email> <free|starter|pro>
  python -m app.admin billing-reset                  # run the monthly lapse/expiry job now (idempotent)
  python -m app.admin usage <account_id|email> [--period YYYY-MM]
  python -m app.admin migrate status|upgrade|check   # same as python -m app.migrate

Add --json to any read command for machine-readable output.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys

from sqlalchemy import func
from sqlmodel import Session, select

from . import billing, db, migrate
from .models_billing import AccountPlan, AuditLog, LedgerEntry


def _find(s: Session, ident: str) -> db.Account:
    acc = s.get(db.Account, ident) or s.exec(select(db.Account).where(db.Account.email == ident)).first()
    if acc is None:
        raise SystemExit(f"no account matching {ident!r}")
    return acc


def _out(args, data, text: str) -> None:
    print(json.dumps(data, indent=2, default=str) if args.json else text)


def cmd_stats(args, s: Session) -> int:
    from .models_features import WebhookDelivery

    n = lambda m, *w: s.exec(select(func.count()).select_from(m).where(*w) if w else select(func.count()).select_from(m)).one()  # noqa: E731
    m = migrate.status()
    data = {
        "accounts": n(db.Account), "replicas": n(db.Replica), "replicas_ready": n(db.Replica, db.Replica.status == "ready"),
        "personas": n(db.Persona), "conversations": n(db.Conversation), "conversations_active": n(db.Conversation, db.Conversation.status == "active"),
        "videos": n(db.Video), "videos_queued": n(db.Video, db.Video.status == "queued"), "videos_ready": n(db.Video, db.Video.status == "ready"),
        "seconds_used_total": int(s.exec(select(func.coalesce(func.sum(db.Conversation.seconds_used), 0))).one()),
        "webhook_failed": n(WebhookDelivery, WebhookDelivery.status == "failed"),
        "plans": {p: n(AccountPlan, AccountPlan.plan == p) for p in billing.PLANS if p != "free"},
        "overage_millicents_this_month": int(s.exec(select(func.coalesce(func.sum(billing.OverageCharge.amount_millicents), 0)).where(
            billing.OverageCharge.period == billing.period_key())).one()),
        "migration": m, "db": db.engine.dialect.name,
    }
    _out(args, data, "\n".join(f"{k:32} {v}" for k, v in data.items()))
    return 0


def cmd_accounts(args, s: Session) -> int:
    q = select(db.Account).order_by(db.Account.created_at.desc()).limit(args.limit)
    if args.search:
        q = q.where(db.Account.email.contains(args.search))
    rows = s.exec(q).all()
    data = [{"id": a.id, "email": a.email, "credits_seconds": a.credits_seconds, "plan": billing.current_plan(s, a.id).id,
             "created_at": a.created_at} for a in rows]
    _out(args, data, "\n".join(f"{d['id']:18} {d['email']:34} {d['plan']:8} {d['credits_seconds']:>8}s  {str(d['created_at'])[:19]}" for d in data) or "(none)")
    return 0


def cmd_account(args, s: Session) -> int:
    a = _find(s, args.ident)
    rep = billing.usage_report(s, a)
    data = {"id": a.id, "email": a.email, "credits_seconds": a.credits_seconds, "created_at": a.created_at, "this_month": rep,
            "replicas": s.exec(select(func.count()).select_from(db.Replica).where(db.Replica.account_id == a.id)).one(),
            "conversations": s.exec(select(func.count()).select_from(db.Conversation).where(db.Conversation.account_id == a.id)).one()}
    _out(args, data, json.dumps(data, indent=2, default=str))
    return 0


def cmd_grant(args, s: Session) -> int:
    a = _find(s, args.ident)
    secs = (args.minutes or 0) * 60 + (args.seconds or 0)
    if secs == 0:
        raise SystemExit("give --minutes and/or --seconds (negative values remove credits)")
    a.credits_seconds = max(a.credits_seconds + secs, 0)
    s.add(a)
    s.add(LedgerEntry(account_id=a.id, kind="grant" if secs > 0 else "adjust", seconds=secs, ref="admin:" + secrets.token_hex(6), note=args.note or "admin"))
    s.add(AuditLog(account_id=a.id, action="admin.credits", target=a.id, detail=f"{secs:+d}s {args.note}"[:300]))
    s.commit()
    print(f"{a.id} ({a.email}): {secs:+d}s -> balance {a.credits_seconds}s")
    return 0


def cmd_plan(args, s: Session) -> int:
    if args.plan not in billing.PLANS:
        raise SystemExit(f"plan must be one of {list(billing.PLANS)}")
    a = _find(s, args.ident)
    row = s.get(AccountPlan, a.id) or AccountPlan(account_id=a.id)
    row.plan = args.plan
    row.updated_at = db.now()
    s.add(row)
    s.add(AuditLog(account_id=a.id, action="admin.plan", target=a.id, detail=args.plan))
    s.commit()
    print(f"{a.id}: plan -> {args.plan} (no credits granted; use `grant`)")
    return 0


def cmd_usage(args, s: Session) -> int:
    a = _find(s, args.ident)
    rep = billing.usage_report(s, a, args.period)
    _out(args, rep, json.dumps(rep, indent=2, default=str))
    return 0


def cmd_reset(args, s: Session) -> int:
    out = billing.run_monthly_reset(s)
    _out(args, out, f"period {out['period']}: {out['lapsed']} plan(s) lapsed, {out['expired_seconds']}s of unused allowance expired")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.admin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("accounts"); p.add_argument("--search"); p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("account"); p.add_argument("ident")
    p = sub.add_parser("grant"); p.add_argument("ident"); p.add_argument("--minutes", type=int); p.add_argument("--seconds", type=int); p.add_argument("--note", default="")
    p = sub.add_parser("plan"); p.add_argument("ident"); p.add_argument("plan")
    p = sub.add_parser("usage"); p.add_argument("ident"); p.add_argument("--period")
    sub.add_parser("billing-reset")
    p = sub.add_parser("migrate"); p.add_argument("action", nargs="?", default="status")
    args = ap.parse_args(argv)
    migrate.load_all_models()
    if args.cmd == "migrate":
        return migrate.main([args.action])
    with Session(db.engine) as s:
        return {"stats": cmd_stats, "accounts": cmd_accounts, "account": cmd_account, "grant": cmd_grant, "plan": cmd_plan,
                "usage": cmd_usage, "billing-reset": cmd_reset}[args.cmd](args, s)


if __name__ == "__main__":
    sys.exit(main())
