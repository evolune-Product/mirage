"""Extra tables owned by the jobs + knowledge modules (db.py is not edited).

Importing this module registers the tables on SQLModel.metadata, so db.init_db()
creates them; `ensure_tables(engine)` does it lazily for the worker process.
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import new_id, now

_ensured: set[int] = set()


def ensure_tables(engine) -> None:
    if id(engine) in _ensured:
        return
    SQLModel.metadata.create_all(engine)
    _ensured.add(id(engine))


class JobClaim(SQLModel, table=True):
    """Queue bookkeeping for Replica/Video rows. PK = '<kind>:<ref_id>' so inserting the
    row is an atomic claim in SQLite (second inserter gets IntegrityError)."""
    key: str = Field(primary_key=True)
    kind: str  # replica | video
    ref_id: str = Field(index=True)
    attempts: int = 1
    locked_at: datetime = Field(default_factory=now)
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    detail: str = ""  # JSON: timings, notes


class VideoMeta(SQLModel, table=True):
    video_id: str = Field(primary_key=True)
    callback_url: Optional[str] = None
    voice: str = "default"
    webhook_status: Optional[str] = None  # delivered | failed: <why>


class KnowledgeDoc(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("kd"), primary_key=True)
    persona_id: str = Field(index=True)
    title: str
    source: str = "text"  # text | pdf | file
    n_chunks: int = 0
    created_at: datetime = Field(default_factory=now)


class KnowledgeChunk(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("kc"), primary_key=True)
    doc_id: str = Field(index=True)
    persona_id: str = Field(index=True)
    idx: int = 0
    text: str
    embedding: Optional[bytes] = None  # float32 little-endian, L2-normalised
    embed_model: Optional[str] = None


class MemorySummary(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("m"), primary_key=True)
    persona_id: str = Field(index=True)
    conversation_id: Optional[str] = Field(default=None, index=True)
    summary: str
    created_at: datetime = Field(default_factory=now)
