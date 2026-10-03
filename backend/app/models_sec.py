"""Consent-binding + security tables (new; existing tables are never altered)."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import new_id, now


class FaceBinding(SQLModel, table=True):
    """Result of binding the consenting person's FACE (selfie video recorded while reading the phrase) to the
    replica's source face (photo or training video). Only scores and a hash are stored, never the selfie video or any
    embedding (reference embeddings live encrypted in the consent folder and are deleted with the replica)."""
    id: str = Field(default_factory=lambda: new_id("fb"), primary_key=True)
    consent_id: str = Field(index=True)
    replica_id: str = Field(index=True)
    recording_sha256: str  # sha256 of the uploaded recording as received (the video track is discarded after checking)
    ref_kind: str = "video"  # photo | video
    face_status: str = "skipped"  # match | mismatch | no_video | no_face | no_reference | unavailable | skipped
    face_score: Optional[float] = None  # median over selfie frames of the best cosine against the reference faces
    threshold: Optional[float] = None
    frames_used: int = 0
    live_status: str = "skipped"  # pass | fail | skipped
    live_nonrigid: Optional[float] = None
    live_mouth: Optional[float] = None
    live_texture: Optional[float] = None
    live_residual: Optional[float] = None
    live_reasons: str = ""
    created_at: datetime = Field(default_factory=now)


class ApiKeyScope(SQLModel, table=True):
    """Scope of a multi-key row (keys created via POST /v1/keys). No row = full access (legacy behaviour)."""
    key_id: str = Field(primary_key=True)
    scope: str = "full"  # full | read
