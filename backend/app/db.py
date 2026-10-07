import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine

import os

DB_URL = os.environ.get("VOCALFACE_DB_URL", "sqlite:///vocalface.db")


def engine_kwargs(url: str) -> dict:
    """check_same_thread is a sqlite-only option (psycopg rejects it); other databases get pooled, health-checked connections."""
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_size": int(os.environ.get("VOCALFACE_DB_POOL", "10")), "max_overflow": 10}


engine = create_engine(DB_URL, **engine_kwargs(DB_URL))


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class Account(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("acc"), primary_key=True)
    email: str
    api_key: str = Field(default_factory=lambda: "mk_" + secrets.token_urlsafe(24), index=True)
    credits_seconds: int = 600
    created_at: datetime = Field(default_factory=now)


class Replica(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("r"), primary_key=True)
    account_id: str = Field(index=True)
    name: str
    train_video_url: str
    status: str = "training"  # training | ready | error
    created_at: datetime = Field(default_factory=now)


class Persona(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("p"), primary_key=True)
    account_id: str = Field(index=True)
    name: str
    system_prompt: str
    replica_id: Optional[str] = None
    llm: str = "ollama/llama3.2:3b"
    tts_voice: str = "default"
    knowledge: str = ""
    created_at: datetime = Field(default_factory=now)


class Conversation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("c"), primary_key=True)
    account_id: str = Field(index=True)
    persona_id: str
    status: str = "active"  # active | ended
    room_url: str = ""
    started_at: datetime = Field(default_factory=now)
    ended_at: Optional[datetime] = None
    seconds_used: int = 0


class Video(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("v"), primary_key=True)
    account_id: str = Field(index=True)
    replica_id: str
    script: str
    status: str = "queued"  # queued | rendering | ready | error
    output_url: Optional[str] = None
    created_at: datetime = Field(default_factory=now)


def init_db() -> None:
    # Dev default: create missing tables. Production with Alembic: VOCALFACE_AUTO_CREATE=0 and run `python -m app.migrate upgrade`.
    if os.environ.get("VOCALFACE_AUTO_CREATE", "1") != "0":
        SQLModel.metadata.create_all(engine)
    from . import migrate

    migrate.warn_if_behind(engine)


def get_session():
    with Session(engine) as s:
        yield s
