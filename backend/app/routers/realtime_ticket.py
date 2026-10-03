"""POST /v1/realtime/ticket: trade the API key (sent in a header, never in a URL) for a short-lived, single-use WebSocket
ticket bound to one conversation. Browsers cannot set headers on `new WebSocket()`, which is why the key used to ride in the URL."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from .. import wsguard
from ..auth import current_account
from ..db import Account, Conversation, get_session

router = APIRouter()


class TicketIn(BaseModel):
    conversation_id: str


@router.post("/realtime/ticket")
def realtime_ticket(body: TicketIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    conv = s.get(Conversation, body.conversation_id)
    if not conv or conv.account_id != acc.id:
        raise HTTPException(404, "conversation not found")
    if conv.status == "ended":
        raise HTTPException(409, "conversation ended")
    t = wsguard.mint_ticket(acc.id, conv.id)
    if t is None:
        raise HTTPException(429, "too many unused tickets", headers={"Retry-After": "30"})
    ttl = wsguard.ticket_ttl()
    return {"ticket": t, "expires_in": ttl, "ws_path": f"/v1/conversations/{conv.id}/stream?ticket={t}"}
