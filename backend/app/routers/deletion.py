"""Data deletion: DELETE /v1/replicas/{rid} and POST /v1/account/delete-my-data."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import data_deletion
from ..auth import current_account
from ..db import Account, Replica, get_session
from ..safety import audit

router = APIRouter()


@router.delete("/replicas/{rid}")
def delete_replica(rid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    r = s.get(Replica, rid)
    if not r or r.account_id != acc.id:
        raise HTTPException(404, "replica not found")
    audit(s, acc.id, "replica.deleted", rid)
    n = data_deletion.delete_replica(s, acc, rid)
    return {"deleted": rid, "files_removed": n}


class DeleteAccountIn(BaseModel):
    confirm: str  # must equal "delete-my-data"


@router.post("/account/delete-my-data")
def delete_my_data(body: DeleteAccountIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    """Irreversible: removes replicas, videos, consent evidence, personas, knowledge, conversations, keys and the
    account itself. Ledger and audit rows are retained (financial/compliance records) without content."""
    if body.confirm != "delete-my-data":
        raise HTTPException(422, 'set confirm to "delete-my-data"')
    audit(s, acc.id, "account.deleted", acc.id)
    n = data_deletion.delete_account(s, acc)
    return {"deleted": True, "files_removed": n}
