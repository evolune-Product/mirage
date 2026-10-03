import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlmodel import Session, select

from .db import Account, get_session
from .models_features import ApiKey


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def account_for_key(session: Session, key: str) -> Optional[Account]:
    """Resolve an API key to its account: hashed multi-key table first, then the legacy Account.api_key."""
    if not key:
        return None
    row = session.exec(select(ApiKey).where(ApiKey.key_hash == hash_key(key))).first()
    if row is not None:
        if row.revoked_at is not None:
            return None
        n = datetime.now(timezone.utc)
        last = row.last_used_at.replace(tzinfo=timezone.utc) if row.last_used_at and row.last_used_at.tzinfo is None else row.last_used_at
        if last is None or n - last > timedelta(seconds=60):  # avoid a write on every request
            row.last_used_at = n
            session.add(row)
            session.commit()
        return session.get(Account, row.account_id)
    return session.exec(select(Account).where(Account.api_key == key)).first()


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
READ_KEY_WRITE_ALLOW = ("/v1/files/sign", "/v1/moderation/check")  # harmless POSTs a read-only key may still use


def key_scope(session: Session, key: str) -> str:
    """'read' for a multi-key row created with scope=read, else 'full' (signup/legacy keys are always full)."""
    from .models_sec import ApiKeyScope

    row = session.exec(select(ApiKey).where(ApiKey.key_hash == hash_key(key))).first()
    if row is None:
        return "full"
    sc = session.get(ApiKeyScope, row.id)
    return sc.scope if sc else "full"


def current_account(
    request: Request, x_api_key: str = Header(...), x_workspace: Optional[str] = Header(None), session: Session = Depends(get_session)
) -> Account:
    acc = account_for_key(session, x_api_key)
    if not acc:
        raise HTTPException(401, "invalid api key")
    if request.method not in SAFE_METHODS and request.url.path not in READ_KEY_WRITE_ALLOW and key_scope(session, x_api_key) == "read":
        raise HTTPException(403, {"error": "read_only_key", "message": "this API key has read-only scope"})
    if x_workspace and not request.url.path.startswith("/v1/workspaces"):
        from . import workspaces  # opt-in team mode: run as the workspace owner, limited by the caller's role

        return workspaces.resolve(session, acc, x_workspace, request.method, request.url.path)
    return acc
