"""Platform tables: overage settings + charges (billing enforcement) and team workspaces. All new tables (nothing altered)."""
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from .db import new_id, now


class BillingSettings(SQLModel, table=True):
    """Per-account overage policy. Overage = usage beyond the credit balance, billed per second at the plan's overage rate,
    never beyond `overage_cap_cents` per calendar month (UTC)."""
    account_id: str = Field(primary_key=True)
    overage_enabled: bool = False
    overage_cap_cents: int = 0
    updated_at: datetime = Field(default_factory=now)


class OverageCharge(SQLModel, table=True):
    """One row per metered session/video that ran past the credit balance. Amounts are in MILLICENTS (1/1000 cent) so
    per-second pricing has no rounding drift; `ref` is unique, which makes settlement idempotent."""
    id: str = Field(default_factory=lambda: new_id("ovc"), primary_key=True)
    account_id: str = Field(index=True)
    period: str = Field(index=True)  # 'YYYY-MM' (UTC)
    seconds: int
    amount_millicents: int
    rate_cents_per_min: int
    ref: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=now)


class Workspace(SQLModel, table=True):
    """A team that shares one billing account (the owner's). Single-user accounts keep working without any workspace."""
    id: str = Field(default_factory=lambda: new_id("ws"), primary_key=True)
    name: str
    owner_account_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=now)


class WorkspaceMember(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("workspace_id", "account_id"),)
    id: str = Field(default_factory=lambda: new_id("wsm"), primary_key=True)
    workspace_id: str = Field(index=True)
    account_id: str = Field(index=True)
    role: str = "member"  # owner | admin | member
    created_at: datetime = Field(default_factory=now)


class WorkspaceInvite(SQLModel, table=True):
    """Invite by email; the token is stored hashed (shown once to the inviter)."""
    id: str = Field(default_factory=lambda: new_id("wsi"), primary_key=True)
    workspace_id: str = Field(index=True)
    email: str
    role: str = "member"
    token_hash: str = Field(index=True)
    invited_by: str = ""
    expires_at: datetime
    accepted_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now)
