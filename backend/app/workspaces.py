"""Team workspaces: several accounts share the OWNER's resources and credits. Single-user accounts need nothing.

A member calls the API with their own key plus `X-Workspace: <workspace_id>`; the request then runs as the workspace
owner's account (so personas, replicas, conversations and the credit balance are the team's), limited by role:

  owner   everything (it is the owner's own account)
  admin   everything except billing, API keys, account deletion and workspace ownership
  member  read-only (GET/HEAD) + start/end conversations

Without the header nothing changes. Policy lives in `allowed()`; extend it there.
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from .db import Account
from .models_platform import Workspace, WorkspaceInvite, WorkspaceMember

ROLES = ("owner", "admin", "member")
INVITE_TTL_DAYS = 7
_ADMIN_BLOCKED = re.compile(r"^/v1/(billing|keys|account|workspaces)(/|$)")
_MEMBER_POST_OK = re.compile(r"^/v1/conversations(/[^/]+/end)?$")


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def allowed(role: str, method: str, path: str) -> bool:
    if role == "owner":
        return True
    if role == "admin":
        return not _ADMIN_BLOCKED.match(path)
    if role == "member":
        return method in ("GET", "HEAD") and not _ADMIN_BLOCKED.match(path) or (method == "POST" and bool(_MEMBER_POST_OK.match(path)))
    return False


def role_of(s: Session, workspace_id: str, account_id: str) -> Optional[str]:
    m = s.exec(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.account_id == account_id)).first()
    return m.role if m else None


def resolve(s: Session, acting: Account, workspace_id: str, method: str, path: str) -> Account:
    """The account a request should run as. 403 when not a member or the role may not do this."""
    ws = s.get(Workspace, workspace_id)
    role = role_of(s, workspace_id, acting.id) if ws else None
    if ws is None or role is None:
        raise HTTPException(403, "not a member of this workspace")  # same answer for unknown and foreign workspaces
    if not allowed(role, method, path):
        raise HTTPException(403, f"role '{role}' may not do this in the workspace")
    owner = s.get(Account, ws.owner_account_id)
    if owner is None:
        raise HTTPException(404, "workspace owner account no longer exists")
    return owner


def create_workspace(s: Session, owner: Account, name: str) -> Workspace:
    ws = Workspace(name=name.strip()[:80] or "Team", owner_account_id=owner.id)
    s.add(ws); s.flush()
    s.add(WorkspaceMember(workspace_id=ws.id, account_id=owner.id, role="owner"))
    s.commit(); s.refresh(ws)
    return ws


def require_role(s: Session, workspace_id: str, account_id: str, *roles: str) -> str:
    role = role_of(s, workspace_id, account_id)
    if role is None:
        raise HTTPException(404, "workspace not found")
    if role not in roles:
        raise HTTPException(403, f"requires role: {' or '.join(roles)}")
    return role


def invite(s: Session, ws: Workspace, inviter: Account, email: str, role: str) -> tuple[WorkspaceInvite, str]:
    if role not in ("admin", "member"):
        raise HTTPException(422, "role must be admin or member")
    email = email.strip().lower()
    if "@" not in email or len(email) > 200:
        raise HTTPException(422, "valid email required")
    token = "wsinv_" + secrets.token_urlsafe(24)
    inv = WorkspaceInvite(workspace_id=ws.id, email=email, role=role, token_hash=hash_token(token), invited_by=inviter.id,
                          expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS))
    s.add(inv); s.commit(); s.refresh(inv)
    return inv, token


def accept(s: Session, acc: Account, token: str) -> WorkspaceMember:
    inv = s.exec(select(WorkspaceInvite).where(WorkspaceInvite.token_hash == hash_token(token))).first()
    if inv is None or inv.revoked_at or inv.accepted_at:
        raise HTTPException(404, "invite not found or already used")
    if _utc(inv.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(410, "invite expired")
    if acc.email.strip().lower() != inv.email:
        raise HTTPException(403, "this invite was sent to a different email address")
    if role_of(s, inv.workspace_id, acc.id):
        raise HTTPException(409, "already a member")
    m = WorkspaceMember(workspace_id=inv.workspace_id, account_id=acc.id, role=inv.role)
    inv.accepted_at = datetime.now(timezone.utc)
    s.add_all([m, inv]); s.commit(); s.refresh(m)
    return m
