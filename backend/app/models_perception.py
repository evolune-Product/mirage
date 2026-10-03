"""Perception (the agent can SEE the user) - persona-level config. New table only; nothing existing is altered."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import now


class PerceptionConfig(SQLModel, table=True):
    persona_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    enabled: bool = False  # master switch: frames are ignored unless True
    consent_acknowledged: bool = False  # the account owner confirms users are told about camera/screen analysis
    require_user_consent: bool = True  # each end user must opt in per conversation ({"type":"perception","enabled":true})
    camera: bool = True
    screen: bool = True
    store_frames: bool = False  # default: frames live in RAM only, never on disk
    vlm_model: str = ""  # "" = MIRAGE_VLM_MODEL (default moondream)
    interval_s: float = 3.0  # min seconds between analysed frames per source
    updated_at: Optional[datetime] = Field(default_factory=now)
