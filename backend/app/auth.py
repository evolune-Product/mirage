import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException
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


def current_account(
    x_api_key: str = Header(...), session: Session = Depends(get_session)
) -> Account:
    acc = account_for_key(session, x_api_key)
    if not acc:
        raise HTTPException(401, "invalid api key")
    return acc
