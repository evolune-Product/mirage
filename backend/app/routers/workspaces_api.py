"""Team workspaces (see app/workspaces.py). These endpoints always act as the CALLER's own account (not X-Workspace)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import workspaces as W
from ..auth import current_account
from ..db import Account, get_session
from ..models_platform import Workspace, WorkspaceInvite, WorkspaceMember
from ..safety import audit

router = APIRouter()


class WsIn(BaseModel):
    name: str


class InviteIn(BaseModel):
    email: str
    role: str = "member"


class AcceptIn(BaseModel):
    token: str


class RoleIn(BaseModel):
    role: str


def _ws_out(ws: Workspace, role: str) -> dict:
    return {"id": ws.id, "name": ws.name, "owner_account_id": ws.owner_account_id, "role": role, "created_at": ws.created_at}


@router.post("/workspaces")
def create(body: WsIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    ws = W.create_workspace(s, acc, body.name)
    audit(s, acc.id, "workspace.created", ws.id, ws.name)
    return _ws_out(ws, "owner")


@router.get("/workspaces")
def list_mine(acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    rows = s.exec(select(WorkspaceMember).where(WorkspaceMember.account_id == acc.id)).all()
    out = []
    for m in rows:
        ws = s.get(Workspace, m.workspace_id)
        if ws:
            out.append(_ws_out(ws, m.role))
    return out


@router.get("/workspaces/{wid}/members")
def members(wid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    W.require_role(s, wid, acc.id, "owner", "admin", "member")
    out = []
    for m in s.exec(select(WorkspaceMember).where(WorkspaceMember.workspace_id == wid)).all():
        a = s.get(Account, m.account_id)
        out.append({"account_id": m.account_id, "email": a.email if a else "", "role": m.role, "joined_at": m.created_at})
    return out


@router.post("/workspaces/{wid}/invites")
def create_invite(wid: str, body: InviteIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    W.require_role(s, wid, acc.id, "owner", "admin")
    if body.role == "admin":
        W.require_role(s, wid, acc.id, "owner")  # only the owner mints admins
    ws = s.get(Workspace, wid)
    inv, token = W.invite(s, ws, acc, body.email, body.role)
    audit(s, acc.id, "workspace.invited", wid, f"{inv.email} as {inv.role}")
    return {"id": inv.id, "email": inv.email, "role": inv.role, "expires_at": inv.expires_at, "token": token,
            "note": "the token is shown once; send it to the invitee, who calls POST /v1/workspaces/invites/accept"}


@router.get("/workspaces/{wid}/invites")
def list_invites(wid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    W.require_role(s, wid, acc.id, "owner", "admin")
    rows = s.exec(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == wid)).all()
    return [{"id": i.id, "email": i.email, "role": i.role, "expires_at": i.expires_at,
             "status": "accepted" if i.accepted_at else "revoked" if i.revoked_at else "pending"} for i in rows]


@router.delete("/workspaces/{wid}/invites/{iid}")
def revoke_invite(wid: str, iid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    W.require_role(s, wid, acc.id, "owner", "admin")
    inv = s.get(WorkspaceInvite, iid)
    if not inv or inv.workspace_id != wid:
        raise HTTPException(404, "invite not found")
    from datetime import datetime, timezone
    inv.revoked_at = inv.revoked_at or datetime.now(timezone.utc)
    s.add(inv); s.commit()
    return {"revoked": True}


@router.post("/workspaces/invites/accept")
def accept(body: AcceptIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    m = W.accept(s, acc, body.token)
    ws = s.get(Workspace, m.workspace_id)
    audit(s, acc.id, "workspace.joined", ws.id, m.role)
    return _ws_out(ws, m.role)


@router.put("/workspaces/{wid}/members/{account_id}")
def set_role(wid: str, account_id: str, body: RoleIn, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    W.require_role(s, wid, acc.id, "owner")
    if body.role not in ("admin", "member"):
        raise HTTPException(422, "role must be admin or member")
    m = s.exec(select(WorkspaceMember).where(WorkspaceMember.workspace_id == wid, WorkspaceMember.account_id == account_id)).first()
    if not m:
        raise HTTPException(404, "member not found")
    if m.role == "owner":
        raise HTTPException(409, "the owner's role cannot be changed")
    m.role = body.role
    s.add(m); s.commit()
    return {"account_id": account_id, "role": m.role}


@router.delete("/workspaces/{wid}/members/{account_id}")
def remove_member(wid: str, account_id: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    my = W.require_role(s, wid, acc.id, "owner", "admin", "member")
    m = s.exec(select(WorkspaceMember).where(WorkspaceMember.workspace_id == wid, WorkspaceMember.account_id == account_id)).first()
    if not m:
        raise HTTPException(404, "member not found")
    if m.role == "owner":
        raise HTTPException(409, "the owner cannot leave or be removed (delete the workspace instead)")
    if account_id != acc.id:  # removing someone else
        if my == "member" or (my == "admin" and m.role != "member"):
            raise HTTPException(403, "not allowed to remove this member")
    s.delete(m); s.commit()
    audit(s, acc.id, "workspace.member_removed", wid, account_id)
    return {"removed": account_id}


@router.delete("/workspaces/{wid}")
def delete_workspace(wid: str, acc: Account = Depends(current_account), s: Session = Depends(get_session)):
    W.require_role(s, wid, acc.id, "owner")
    for m in s.exec(select(WorkspaceMember).where(WorkspaceMember.workspace_id == wid)).all():
        s.delete(m)
    for i in s.exec(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == wid)).all():
        s.delete(i)
    s.delete(s.get(Workspace, wid)); s.commit()
    return {"deleted": wid}
