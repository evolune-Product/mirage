"""Face-quality tables (separate from db.py / models_extra.py; creating them never alters existing tables).

Importing registers the table on SQLModel.metadata so db.init_db() creates it."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import now


class ListeningClip(SQLModel, table=True):
    """Optional closed-mouth 'listening' idle clip for a replica. The file lives at
    <data>/replicas/<replica_id>/listening.mp4; this row is the metadata."""
    replica_id: str = Field(primary_key=True)
    source_url: str = ""
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bytes: int = 0
    created_at: datetime = Field(default_factory=now)
    updated_at: Optional[datetime] = None
