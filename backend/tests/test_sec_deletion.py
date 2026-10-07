"""Delete-my-data / delete-replica completeness: rows in EVERY table and files in EVERY data directory.

The row filler is schema-driven: a table added later that links to an account/replica/persona/... is created and must
disappear; a table with no recognised link column fails the test so someone decides how it is deleted."""
import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Integer, func, insert, select, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import db, jobs, migrate, safety
from app.data_deletion import KEEP
from app.main import app

migrate.load_all_models()
KEEP_FILES = {".secret_key", "secret.key", ".worker_heartbeat"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool))
    SQLModel.metadata.create_all(db.engine)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("VOCALFACE_SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.setenv("VOCALFACE_RL_SIGNUP", "1000/60")
    safety.limiter.reset()

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    yield TestClient(app)
    app.dependency_overrides.clear()
    safety.limiter.reset()


def signup(c):
    j = c.post("/v1/signup", json={"email": "a@b.co"}).json()
    return j["account_id"], {"x-api-key": j["api_key"]}


class Owner:
    """All ids one account owns; ids are fixed strings so rows can be located after deletion."""
    def __init__(self, tag: str, account_id: str, rid: str, vid: str):
        self.tag = tag
        self.ids = {"account_id": account_id, "owner_account_id": account_id, "replica_id": rid, "ref_id": rid, "video_id": vid,
                    "persona_id": f"{tag}_persona", "conversation_id": f"{tag}_conv", "doc_id": f"{tag}_doc", "key_id": f"{tag}_key",
                    "workspace_id": f"{tag}_ws", "batch_id": f"{tag}_batch", "consent_id": f"{tag}_cns"}
        self.rid, self.vid, self.aid = rid, vid, account_id


def _val(col, n, owner: Owner):
    if col.name in owner.ids:
        return owner.ids[col.name]
    try:
        t = col.type.python_type
    except NotImplementedError:
        t = dt.datetime if "date" in type(col.type).__name__.lower() else str
    if t is str:
        return f"{owner.tag}_{col.name}_{n}"
    if t is bool:
        return False
    if t is int:
        return 1
    if t is float:
        return 0.5
    if t is dt.datetime:
        return dt.datetime.now(dt.timezone.utc)
    if t is bytes:
        return b"x"
    return None


def fill_every_table(owner: Owner, skip=("account",)) -> dict:
    """One row per table linked to `owner` (primary keys unique per owner). Returns {table: link_columns}."""
    linked = {}
    with db.engine.begin() as c:
        for n, (name, t) in enumerate(sorted(SQLModel.metadata.tables.items())):
            if name in skip:
                continue
            link = [col.name for col in t.c if col.name in owner.ids]
            vals = {}
            for col in t.c:
                if col.primary_key and col.autoincrement is True and isinstance(col.type, Integer):
                    continue
                if col.name in owner.ids:
                    vals[col.name] = owner.ids[col.name]
                elif col.primary_key or not col.nullable:
                    vals[col.name] = _val(col, n, owner)
                if col.primary_key and col.name == "id":
                    alias = {"video": "video_id", "persona": "persona_id", "conversation": "conversation_id", "knowledgedoc": "doc_id",
                             "apikey": "key_id", "workspace": "workspace_id", "videobatch": "batch_id"}.get(name)
                    vals[col.name] = owner.ids[alias] if alias else (f"{owner.tag}_asset" if name == "creativeasset" else f"{owner.tag}_{name}")
                if name == "creativeasset" and col.name == "ext":
                    vals[col.name] = "png"
                if col.primary_key and col.name == "key":
                    vals[col.name] = f"{owner.tag}_{name}"
            c.execute(insert(t).values(**vals))
            linked[name] = link
    return linked


def count_rows(owner: Owner, tables=None, only_cols=None) -> dict:
    """Rows that still reference the owner (any link column), per table."""
    left = {}
    with db.engine.connect() as c:
        for name, t in SQLModel.metadata.tables.items():
            if name in KEEP or name == "account" or (tables and name not in tables):
                continue
            conds = [t.c[k].in_([v]) for k, v in owner.ids.items() if k in t.c and (not only_cols or k in only_cols)]
            if not conds:
                continue
            from sqlalchemy import or_
            n = c.execute(select(func.count()).select_from(t).where(or_(*conds))).scalar()
            if n:
                left[name] = n
    return left


def make_files(root: Path, owner: Owner) -> list[Path]:
    rid, vid, cid, tag = owner.rid, owner.vid, owner.ids["conversation_id"], owner.tag
    files = [
        root / "replicas" / rid / "face.png", root / "replicas" / rid / "photo.png", root / "replicas" / rid / "listening.mp4",
        root / "replicas" / rid / "idle.mp4", root / "replicas" / rid / "source.mp4", root / "replicas" / rid / "background.json",
        root / "replicas" / rid / "voice_clone" / "ref.wav", root / "replicas" / rid / "voice_clone" / "ref.json",
        root / "consent" / rid / "cns_1.audio.webm", root / "consent" / rid / "ref_face_abc.enc", root / "consent" / rid / "train_abc.npy",
        root / "videos" / f"{vid}.mp4", root / "videos" / f"{vid}.jpg", root / "videos" / f"{vid}.srt", root / "videos" / f"{vid}.wav",
        root / "videos" / f"creative_{vid}_work" / "scene0.mp4",
        root / "creative_assets" / f"{tag}_asset.png",
        root / "perception" / cid / "frame_0001.jpg", root / "perception" / cid / "frame_0002.jpg",
    ]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"data-" + tag.encode())
    return files


def leftovers(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.name not in KEEP_FILES)


def setup_two_accounts(env, tmp_path):
    out = []
    for tag in ("a", "b"):
        acc, h = signup(env)
        rid = env.post("/v1/replicas", json={"name": tag, "train_video_url": "/x.mp4"}, headers=h).json()["id"]
        o = Owner(tag, acc, rid, f"v_{tag}00000001")
        fill_every_table(o, skip=("account", "replica"))  # replica row exists via the API (video row is filled generically)
        files = make_files(tmp_path, o)
        out.append((o, h, files))
    return out


def test_every_table_has_a_link_column_the_deleter_understands(env, tmp_path):
    (a, ha, _), _b = setup_two_accounts(env, tmp_path)
    linked = fill_every_table(Owner("z", "acc_z", "r_z", "v_z"), skip=("account", "replica"))
    orphans = [n for n, cols in linked.items() if not cols and n not in KEEP]
    assert orphans == [], f"tables with no account/replica/persona/... link; decide how delete-my-data covers them: {orphans}"


def test_delete_my_data_leaves_nothing_of_the_account_in_any_table_or_directory(env, tmp_path):
    (a, ha, afiles), (b, hb, bfiles) = setup_two_accounts(env, tmp_path)
    assert count_rows(a), "the filler must have created rows"
    r = env.post("/v1/account/delete-my-data", json={"confirm": "delete-my-data"}, headers=ha)
    assert r.status_code == 200 and r.json()["files_removed"] >= len(afiles) - 1
    assert count_rows(a) == {}, f"rows left after account deletion: {count_rows(a)}"
    assert not any(str(f.relative_to(tmp_path)) in leftovers(tmp_path) for f in afiles)
    # not even empty tag leftovers of A in the data dir
    assert [p for p in leftovers(tmp_path) if "/a_" in p or "_a0" in p or f"{a.rid}" in p or "creative_assets/a_" in p] == []
    # account B untouched: rows and files
    assert count_rows(b), "B's rows must survive"
    assert all(f.exists() for f in bfiles)
    assert env.get("/v1/usage", headers=hb).status_code == 200
    # tombstone kept without content
    with db.engine.connect() as c:
        assert c.execute(text("select count(*) from datadeletion where scope='account'")).scalar() == 1


def test_delete_replica_removes_everything_hanging_off_it_but_not_the_rest(env, tmp_path):
    (a, ha, afiles), (b, hb, bfiles) = setup_two_accounts(env, tmp_path)
    only = ("replica_id", "video_id", "ref_id")
    before = count_rows(a, only_cols=only)
    assert before
    r = env.delete(f"/v1/replicas/{a.rid}", headers=ha)
    assert r.status_code == 200
    assert count_rows(a, only_cols=only) == {}, count_rows(a, only_cols=only)
    gone = [f for f in afiles if "replicas/" in str(f) or "consent/" in str(f) or "videos/" in str(f)]
    assert not [f for f in gone if f.exists()], [str(f) for f in gone if f.exists()]
    # perception frames of the conversations of personas linked to this replica
    # (persona a_persona is linked via the replica_id the filler set) are removed too
    assert not (tmp_path / "perception" / a.ids["conversation_id"]).exists()
    # account-level assets and other accounts are not touched by a replica delete
    assert (tmp_path / "creative_assets" / "a_asset.png").exists()
    assert all(f.exists() for f in bfiles)


def test_perception_frames_removed_even_when_persona_replica_link_was_cleared(env, tmp_path):
    (a, ha, afiles), _ = setup_two_accounts(env, tmp_path)
    env.post("/v1/account/delete-my-data", json={"confirm": "delete-my-data"}, headers=ha)
    assert not (tmp_path / "perception").joinpath(a.ids["conversation_id"]).exists()


def test_deleted_account_key_stops_working_and_second_delete_is_401(env, tmp_path):
    (a, ha, _), _ = setup_two_accounts(env, tmp_path)
    assert env.post("/v1/account/delete-my-data", json={"confirm": "delete-my-data"}, headers=ha).status_code == 200
    assert env.post("/v1/account/delete-my-data", json={"confirm": "delete-my-data"}, headers=ha).status_code == 401
