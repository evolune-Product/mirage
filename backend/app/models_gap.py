"""Competitor-gap tables (new tables only): knowledge URL sources, citations, pronunciation glossary, voice tuning,
conversation insights, scheduled share links. Importing registers them on SQLModel.metadata."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import new_id, now


class KnowledgeSource(SQLModel, table=True):
    """Where a knowledge document came from (URL ingestion) so it can be refreshed."""
    doc_id: str = Field(primary_key=True)
    persona_id: str = Field(index=True)
    url: str
    content_hash: str = ""
    fetched_at: datetime = Field(default_factory=now)


class TurnCitation(SQLModel, table=True):
    """Knowledge excerpts the agent was given for one assistant turn (what its answer was grounded on)."""
    id: str = Field(default_factory=lambda: new_id("tc"), primary_key=True)
    conversation_id: str = Field(index=True)
    seq: int = 0  # TranscriptTurn.seq of the assistant turn
    rank: int = 0
    doc_id: str = ""
    title: str = ""
    url: str = ""
    score: float = 0.0
    snippet: str = ""


class PronunciationEntry(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("pr"), primary_key=True)
    persona_id: str = Field(index=True)
    term: str
    replacement: str  # what the TTS should read instead (a respelling such as "Nuh-VEE-uh")
    case_sensitive: bool = False
    created_at: datetime = Field(default_factory=now)


class VoiceTuning(SQLModel, table=True):
    persona_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    interruption_sensitivity: float = 0.5  # 0 = hard to interrupt, 1 = interrupts on a brief sound
    allow_interruptions: bool = True
    turn_patience_ms: int = 700  # silence that ends the user's turn
    updated_at: datetime = Field(default_factory=now)


class ConversationInsight(SQLModel, table=True):
    conversation_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    persona_id: str = Field(index=True)
    sentiment: float = 0.0  # -1..1 over the user's turns
    label: str = "neutral"  # positive | neutral | negative
    trend: str = "flat"  # improving | declining | flat (second half vs first half of user turns)
    topics: str = "[]"  # JSON list of keywords
    user_words: int = 0
    agent_words: int = 0
    questions: int = 0  # user questions
    interruptions: int = 0
    turn_sentiments: str = "[]"  # JSON [{seq, score}]
    created_at: datetime = Field(default_factory=now)


class ShareSchedule(SQLModel, table=True):
    token: str = Field(primary_key=True)  # ShareLink.token
    account_id: str = Field(index=True)
    starts_at: datetime
    ends_at: Optional[datetime] = None
    invitee_name: str = ""
    invitee_email: str = ""
    note: str = ""
    created_at: datetime = Field(default_factory=now)
