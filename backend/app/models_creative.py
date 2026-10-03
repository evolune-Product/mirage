"""Creative-feature tables (photo avatars, backgrounds, per-video render options, uploaded assets).

Separate module so no existing table is altered; importing registers them on SQLModel.metadata (db.init_db creates them)."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import new_id, now


class PhotoReplica(SQLModel, table=True):
    """Marks a Replica as created from a single photo. The worker animates the photo into an idle clip stored as
    <data>/replicas/<id>/listening.mp4 (+ source.mp4), so the live Wav2Lip service and offline renders work unchanged."""
    replica_id: str = Field(primary_key=True)
    photo_url: str = ""
    idle_seconds: float = 4.0
    head_motion: float = 1.0
    status: str = "queued"          # queued | animating | ready | error
    error: str = ""
    warnings: str = "[]"            # json list of photo-quality warnings
    animate_s: float = 0.0
    created_at: datetime = Field(default_factory=now)


class ReplicaBackground(SQLModel, table=True):
    """Default background of a replica (live conversations + offline videos). `spec` is a json background spec (workers/background.py)."""
    replica_id: str = Field(primary_key=True)
    spec: str = "{}"
    updated_at: datetime = Field(default_factory=now)


class VideoOptions(SQLModel, table=True):
    """Per-video render options (format, background, captions, logo, scenes, transition). Presence of a row routes the video
    through the Wav2Lip creative renderer instead of the legacy LivePortrait amplitude renderer."""
    video_id: str = Field(primary_key=True)
    options: str = "{}"
    created_at: datetime = Field(default_factory=now)


class CreativeAsset(SQLModel, table=True):
    """Uploaded background image / logo. File: <data>/creative_assets/<id>.<ext>."""
    id: str = Field(default_factory=lambda: new_id("asset"), primary_key=True)
    account_id: str = Field(index=True)
    kind: str = "image"             # background | logo | image
    filename: str = ""
    ext: str = "png"
    sha256: str = ""
    bytes: int = 0
    width: int = 0
    height: int = 0
    created_at: datetime = Field(default_factory=now)
