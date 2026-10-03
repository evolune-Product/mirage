"""Voice-cloning tables (new; existing tables are never altered).

`replica_id` is a column name that data_deletion._purge_cols already scans, so deleting a replica or an account removes
these rows automatically; the audio lives under the replica's own directory (jobs.replica_dir(rid)/voice_clone/) which is
removed with it."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import new_id, now


class ReplicaVoice(SQLModel, table=True):
    replica_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    consent_id: str = ""  # the ConsentRecord that authorised this clone
    status: str = "queued"  # queued | processing | ready | failed | revoked
    engine: str = ""  # e.g. chatterbox-mlx
    ref_seconds: float = 0.0
    ref_quality: str = ""  # JSON of refprep.RefStats
    similarity: Optional[float] = None  # WeSpeaker cosine(reference, clone speaking a fixed test sentence); None = not measured
    wer: Optional[float] = None  # Whisper round-trip word error rate on that test sentence
    synth_rtf: Optional[float] = None  # real-time factor of the test sentence on this machine
    error: str = ""
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
