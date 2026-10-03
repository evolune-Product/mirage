"""Safety tables (new; existing tables are never altered)."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import new_id, now


class ConsentVerification(SQLModel, table=True):
    """Evidence stored for a consent recording made via /consent/audio."""
    id: str = Field(default_factory=lambda: new_id("cvf"), primary_key=True)
    consent_id: str = Field(index=True)
    replica_id: str = Field(index=True)
    audio_path: str
    audio_sha256: str
    transcript: str
    phrase_score: float  # 0..1 fuzzy similarity to the challenge phrase
    code_words_ok: bool
    voice_score: Optional[float] = None  # cosine similarity to training-video voice, None if not computed
    voice_status: str = "skipped"  # match | mismatch | skipped | unavailable | no_speech
    created_at: datetime = Field(default_factory=now)


class DataDeletion(SQLModel, table=True):
    """Tombstone proving a deletion happened (no personal content kept)."""
    id: str = Field(default_factory=lambda: new_id("del"), primary_key=True)
    account_id: str = Field(index=True)
    scope: str  # replica:<id> | account
    files_removed: int = 0
    created_at: datetime = Field(default_factory=now)
