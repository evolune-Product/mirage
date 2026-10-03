from sqlmodel import Session, select

from app import db
from .test_api import client  # noqa: F401


def mk(c, email):
    r = c.post("/v1/signup", json={"email": email}).json()
    return {"x-api-key": r["api_key"]}, r["account_id"]


def team(c):
    owner, oid = mk(c, "owner@team.io")
    admin, aid = mk(c, "admin@team.io")
    member, mid = mk(c, "member@team.io")
    ws = c.post("/v1/workspaces", headers=owner, json={"name": "Acme"}).json()
    for h, email, role in ((admin, "admin@team.io", "admin"), (member, "member@team.io", "member")):
        inv = c.post(f"/v1/workspaces/{ws['id']}/invites", headers=owner, json={"email": email, "role": role}).json()
        assert c.post("/v1/workspaces/invites/accept", headers=h, json={"token": inv["token"]}).json()["role"] == role
    return ws["id"], owner, admin, member, (oid, aid, mid)


def test_single_user_accounts_unaffected(client):
    h, _ = mk(client, "solo@x.io")
    assert client.post("/v1/personas", headers=h, json={"name": "a", "system_prompt": "s"}).status_code == 200
    assert client.get("/v1/workspaces", headers=h).json() == []
    assert client.get("/v1/usage", headers=h).json()["credits_seconds"] == 600


def test_roles_and_shared_resources(client):
    wid, owner, admin, member, (oid, aid, mid) = team(client)
    W = {"x-workspace": wid}
    # owner creates a persona; admin sees + edits it through the workspace, member only reads
    p = client.post("/v1/personas", headers=owner, json={"name": "shared", "system_prompt": "s"}).json()
    assert [x["id"] for x in client.get("/v1/personas", headers={**admin, **W}).json()] == [p["id"]]
    assert client.get("/v1/personas", headers=admin).json() == []  # without the header: own (empty) account
    assert client.put(f"/v1/personas/{p['id']}", headers={**admin, **W}, json={"name": "renamed", "system_prompt": "s"}).status_code == 200
    assert client.get("/v1/personas", headers={**member, **W}).status_code == 200
    r = client.post("/v1/personas", headers={**member, **W}, json={"name": "no", "system_prompt": "s"})
    assert r.status_code == 403 and "member" in r.text
    # member can run a conversation (billed to the owner)
    c = client.post("/v1/conversations", headers={**member, **W}, json={"persona_id": p["id"]})
    assert c.status_code == 200
    assert client.post(f"/v1/conversations/{c.json()['id']}/end", headers={**member, **W}).status_code == 200
    with Session(db.engine) as s:
        assert s.get(db.Account, oid).credits_seconds < 600 and s.get(db.Account, mid).credits_seconds == 600
    # admin/member cannot touch billing, keys or account deletion in the workspace
    for h in (admin, member):
        assert client.get("/v1/billing/status", headers={**h, **W}).status_code == 403
        assert client.get("/v1/keys", headers={**h, **W}).status_code == 403
    assert client.get("/v1/billing/status", headers={**owner, **W}).status_code == 200


def test_non_member_and_unknown_workspace_look_identical(client):
    wid, owner, admin, member, _ = team(client)
    stranger, _ = mk(client, "stranger@x.io")
    a = client.get("/v1/personas", headers={**stranger, "x-workspace": wid})
    b = client.get("/v1/personas", headers={**stranger, "x-workspace": "ws_doesnotexist"})
    assert a.status_code == b.status_code == 403 and a.json() == b.json()
    assert client.get(f"/v1/workspaces/{wid}/members", headers=stranger).status_code == 404


def test_invite_rules(client):
    wid, owner, admin, member, (oid, aid, mid) = team(client)
    # admin may invite members but not admins; members may not invite
    assert client.post(f"/v1/workspaces/{wid}/invites", headers=admin, json={"email": "n@x.io", "role": "member"}).status_code == 200
    assert client.post(f"/v1/workspaces/{wid}/invites", headers=admin, json={"email": "n@x.io", "role": "admin"}).status_code == 403
    assert client.post(f"/v1/workspaces/{wid}/invites", headers=member, json={"email": "n@x.io"}).status_code == 403
    assert client.post(f"/v1/workspaces/{wid}/invites", headers=owner, json={"email": "n@x.io", "role": "owner"}).status_code == 422
    inv = client.post(f"/v1/workspaces/{wid}/invites", headers=owner, json={"email": "new@x.io"}).json()
    wrong, _ = mk(client, "other@x.io")
    assert client.post("/v1/workspaces/invites/accept", headers=wrong, json={"token": inv["token"]}).status_code == 403  # wrong email
    right, _ = mk(client, "NEW@x.io")
    assert client.post("/v1/workspaces/invites/accept", headers=right, json={"token": inv["token"]}).status_code == 200
    assert client.post("/v1/workspaces/invites/accept", headers=right, json={"token": inv["token"]}).status_code == 404  # single use
    # token stored hashed
    with Session(db.engine) as s:
        from app.models_platform import WorkspaceInvite
        assert all(inv["token"] not in (i.token_hash or "") and len(i.token_hash) == 64 for i in s.exec(select(WorkspaceInvite)).all())
    # revoke + expiry
    inv2 = client.post(f"/v1/workspaces/{wid}/invites", headers=owner, json={"email": "late@x.io"}).json()
    assert client.delete(f"/v1/workspaces/{wid}/invites/{inv2['id']}", headers=owner).status_code == 200
    late, _ = mk(client, "late@x.io")
    assert client.post("/v1/workspaces/invites/accept", headers=late, json={"token": inv2["token"]}).status_code == 404
    inv3 = client.post(f"/v1/workspaces/{wid}/invites", headers=owner, json={"email": "exp@x.io"}).json()
    from datetime import datetime, timedelta, timezone
    with Session(db.engine) as s:
        i = s.get(WorkspaceInvite, inv3["id"]); i.expires_at = datetime.now(timezone.utc) - timedelta(days=1); s.add(i); s.commit()
    exp, _ = mk(client, "exp@x.io")
    assert client.post("/v1/workspaces/invites/accept", headers=exp, json={"token": inv3["token"]}).status_code == 410


def test_member_management(client):
    wid, owner, admin, member, (oid, aid, mid) = team(client)
    assert {m["role"] for m in client.get(f"/v1/workspaces/{wid}/members", headers=member).json()} == {"owner", "admin", "member"}
    assert client.put(f"/v1/workspaces/{wid}/members/{mid}", headers=admin, json={"role": "admin"}).status_code == 403  # owner only
    assert client.put(f"/v1/workspaces/{wid}/members/{mid}", headers=owner, json={"role": "admin"}).status_code == 200
    assert client.put(f"/v1/workspaces/{wid}/members/{oid}", headers=owner, json={"role": "member"}).status_code == 409
    assert client.delete(f"/v1/workspaces/{wid}/members/{oid}", headers=admin).status_code == 409  # owner is permanent
    assert client.delete(f"/v1/workspaces/{wid}/members/{aid}", headers=member).status_code == 403  # member (now admin) cannot remove an admin
    assert client.delete(f"/v1/workspaces/{wid}/members/{mid}", headers=member).status_code == 200  # leave
    assert client.get("/v1/personas", headers={**member, "x-workspace": wid}).status_code == 403  # access gone at once
    assert client.delete(f"/v1/workspaces/{wid}", headers=admin).status_code == 403
    assert client.delete(f"/v1/workspaces/{wid}", headers=owner).status_code == 200
    assert client.get("/v1/workspaces", headers=admin).json() == []
