from fastapi import Depends, Header, HTTPException
from sqlmodel import Session, select

from .db import Account, get_session


def current_account(
    x_api_key: str = Header(...), session: Session = Depends(get_session)
) -> Account:
    acc = session.exec(select(Account).where(Account.api_key == x_api_key)).first()
    if not acc:
        raise HTTPException(401, "invalid api key")
    return acc
