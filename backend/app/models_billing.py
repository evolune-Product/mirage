"""Billing, consent, and audit tables (kept out of db.py)."""
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from .db import new_id, now


class AccountPlan(SQLModel, table=True):
    account_id: str = Field(primary_key=True)
    plan: str = "free"
    updated_at: datetime = Field(default_factory=now)


class PaymentEvent(SQLModel, table=True):
    """One row per processed provider event; the unique key makes webhooks idempotent."""
    __table_args__ = (UniqueConstraint("provider", "event_id"),)
    id: str = Field(default_factory=lambda: new_id("pe"), primary_key=True)
    provider: str
    event_id: str
    account_id: str = ""
    created_at: datetime = Field(default_factory=now)


class LedgerEntry(SQLModel, table=True):
    """seconds > 0 credits added, seconds < 0 consumed. `ref` unique when set."""
    id: str = Field(default_factory=lambda: new_id("le"), primary_key=True)
    account_id: str = Field(index=True)
    kind: str  # topup | plan | usage | grant | adjust
    seconds: int
    amount_cents: int = 0
    currency: str = "usd"
    ref: Optional[str] = Field(default=None, unique=True, index=True)
    note: str = ""
    created_at: datetime = Field(default_factory=now)


class ConsentChallenge(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("cch"), primary_key=True)
    replica_id: str = Field(index=True)
    account_id: str
    phrase: str
    expires_at: datetime
    used: bool = False
    created_at: datetime = Field(default_factory=now)


class ConsentRecord(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("cns"), primary_key=True)
    replica_id: str = Field(index=True)
    account_id: str
    speaker_name: str
    phrase: str
    transcript: str
    audio_url: str
    audio_sha256: str = ""
    verified_by: str = "transcript-match"
    revoked: bool = False
    created_at: datetime = Field(default_factory=now)


class AuditLog(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("aud"), primary_key=True)
    account_id: str = Field(index=True)
    action: str
    target: str = ""
    detail: str = ""
    created_at: datetime = Field(default_factory=now)
