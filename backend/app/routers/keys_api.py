import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import convo_runtime as cr
from ..auth import current_account, hash_key
from ..db import Account, get_session, now
from ..models_features import ApiKey

router = APIRouter()


class KeyIn(BaseModel):
    name: str = "key"


def _out(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "created_at": k.created_at,
            "last_used_at": k.last_used_at, "revoked_at": k.revoked_at}


@router.post("/keys")
def create_key(body: KeyIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure()
    if len(s.exec(select(ApiKey).where(ApiKey.account_id == acc.id, ApiKey.revoked_at == None)).all()) >= 25:  # noqa: E711
        raise HTTPException(409, "key limit reached (25 active keys)")
    key = "mk_" + secrets.token_urlsafe(32)
    k = ApiKey(account_id=acc.id, name=body.name[:60], prefix=key[:8], key_hash=hash_key(key))
    s.add(k); s.commit(); s.refresh(k)
    return {**_out(k), "key": key}  # the only time the full key is shown


@router.get("/keys")
def list_keys(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    cr.ensure()
    rows = s.exec(select(ApiKey).where(ApiKey.account_id == acc.id).order_by(ApiKey.created_at)).all()
    legacy = {"id": "legacy", "name": "default (created at signup)", "prefix": acc.api_key[:8], "created_at": acc.created_at,
              "last_used_at": None, "revoked_at": None, "legacy": True}
    return [legacy] + [_out(k) for k in rows]


@router.delete("/keys/{kid}")
def revoke_key(kid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    if kid == "legacy":
        raise HTTPException(409, "the signup key cannot be revoked; rotate it with POST /v1/keys/legacy/rotate")
    k = s.get(ApiKey, kid)
    if not k or k.account_id != acc.id:
        raise HTTPException(404, "key not found")
    k.revoked_at = k.revoked_at or now()
    s.add(k); s.commit()
    return _out(k)


@router.post("/keys/legacy/rotate")
def rotate_legacy(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Replaces the signup key (Account.api_key) with a fresh one; the old value stops working immediately."""
    acc.api_key = "mk_" + secrets.token_urlsafe(24)
    s.add(acc); s.commit()
    return {"id": "legacy", "key": acc.api_key, "prefix": acc.api_key[:8]}
