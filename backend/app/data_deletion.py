"""Delete a replica (or a whole account) with all derived assets: DB rows in every table that references the
ids (discovered from SQLModel metadata, so tables added later are covered) and files on disk."""
import shutil
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlmodel import Session, SQLModel

from . import jobs, settings
from .db import Account, Persona, Replica, Video
from .models_safety import DataDeletion

# Kept on purpose: financial records and the audit trail (document in SECURITY.md).
KEEP = {"ledgerentry", "paymentevent", "auditlog", "datadeletion"}


def _purge_cols(s: Session, cols: dict[str, list[str]], skip: set[str]) -> None:
    for name, t in SQLModel.metadata.tables.items():
        if name in KEEP or name in skip:
            continue
        for col, ids in cols.items():
            if ids and col in t.c:
                s.exec(delete(t).where(t.c[col].in_(ids)))


def _rm(p: Path) -> int:
    if p.is_dir():
        n = sum(1 for x in p.rglob("*") if x.is_file())
        shutil.rmtree(p, ignore_errors=True)
        return n
    if p.exists():
        p.unlink(missing_ok=True)
        return 1
    return 0


def delete_replica(s: Session, acc: Account, rid: str) -> int:
    """Remove replica, its videos, consent evidence, job rows, files. Personas keep existing but lose the link."""
    vids = [r[0] for r in s.execute(select(Video.id).where(Video.replica_id == rid, Video.account_id == acc.id))]
    files = 0
    for vid in vids:
        files += _rm(jobs.video_path(vid))
    files += _rm(jobs.replica_dir(rid))
    files += _rm(settings.data_dir() / "consent" / rid)
    s.exec(update(Persona).where(Persona.replica_id == rid, Persona.account_id == acc.id).values(replica_id=None))
    _purge_cols(s, {"replica_id": [rid], "video_id": vids, "ref_id": [rid] + vids}, skip={"persona", "replica", "video"})
    s.exec(delete(Video).where(Video.replica_id == rid, Video.account_id == acc.id))
    s.exec(delete(Replica).where(Replica.id == rid, Replica.account_id == acc.id))
    s.add(DataDeletion(account_id=acc.id, scope=f"replica:{rid}", files_removed=files))
    s.commit()
    return files


def delete_account(s: Session, acc: Account) -> int:
    """Delete everything owned by the account, including the account row (the API key stops working)."""
    aid = acc.id
    rids = [r[0] for r in s.execute(select(Replica.id).where(Replica.account_id == aid))]
    vids = [r[0] for r in s.execute(select(Video.id).where(Video.account_id == aid))]
    pids = [r[0] for r in s.execute(select(Persona.id).where(Persona.account_id == aid))]
    files = 0
    for vid in vids:
        files += _rm(jobs.video_path(vid))
    for rid in rids:
        files += _rm(jobs.replica_dir(rid)) + _rm(settings.data_dir() / "consent" / rid)
    cids = []
    for name, t in SQLModel.metadata.tables.items():
        if name == "conversation":
            cids = [r[0] for r in s.execute(select(t.c.id).where(t.c.account_id == aid))]
    docs = []
    if "knowledgedoc" in SQLModel.metadata.tables:
        t = SQLModel.metadata.tables["knowledgedoc"]
        docs = [r[0] for r in s.execute(select(t.c.id).where(t.c.account_id == aid))] if "account_id" in t.c else []
    cols = {"replica_id": rids, "video_id": vids, "persona_id": pids, "conversation_id": cids, "doc_id": docs,
            "ref_id": rids + vids}
    _purge_cols(s, cols, skip={"account", "replica", "video", "persona", "conversation", "knowledgedoc"})
    for name, t in SQLModel.metadata.tables.items():  # everything keyed by account_id (incl. core tables)
        if name in KEEP or name == "account":
            continue
        if "account_id" in t.c:
            s.exec(delete(t).where(t.c.account_id == aid))
    s.exec(delete(Account).where(Account.id == aid))
    s.add(DataDeletion(account_id=aid, scope="account", files_removed=files))
    s.commit()
    return files
