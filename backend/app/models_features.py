"""Tables for the feature layer (transcripts, webhooks, persona config, keys, analytics, bulk video, share links).

All NEW tables: existing tables in db.py / models_extra.py are never altered, so old sqlite files keep working
(create_all only adds the missing tables). Importing this module registers them on SQLModel.metadata.
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import new_id, now


class ApiKey(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("key"), primary_key=True)
    account_id: str = Field(index=True)
    name: str = "default"
    prefix: str = ""  # first chars of the key, for display only
    key_hash: str = Field(index=True, unique=True)  # sha256 hex of the full key; the key itself is never stored
    created_at: datetime = Field(default_factory=now)
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ConversationMeta(SQLModel, table=True):
    """Per-conversation options + lifecycle flags (Conversation has no room for them)."""
    conversation_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    persona_id: str = ""
    participant_id: str = ""  # memory scope; "" = shared persona memory
    context: str = ""  # extra per-conversation instructions appended to the system prompt
    variables: str = "{}"  # JSON {name: value}; rendered into greeting/system prompt/context via {{name}}
    max_seconds: Optional[int] = None  # hard cap for this conversation
    language: Optional[str] = None  # overrides persona language
    share_token: Optional[str] = None
    summary: str = ""
    finalized_at: Optional[datetime] = None
    end_reason: str = ""
    last_closed_at: Optional[datetime] = None  # last time the WebSocket closed (idle reaper)
    created_at: datetime = Field(default_factory=now)


class TranscriptTurn(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("tt"), primary_key=True)
    conversation_id: str = Field(index=True)
    seq: int = 0
    role: str  # user | assistant | system(tool results)
    text: str
    t_ms: int = 0  # ms since the conversation session started
    first_audio_ms: Optional[int] = None  # assistant turns: latency from end of user speech to first audio
    interrupted: bool = False
    created_at: datetime = Field(default_factory=now)


class ConversationMetric(SQLModel, table=True):
    conversation_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    persona_id: str = ""
    user_turns: int = 0
    agent_turns: int = 0
    avg_first_audio_ms: Optional[float] = None
    p95_first_audio_ms: Optional[float] = None
    interruptions: int = 0
    tool_calls: int = 0
    guardrail_hits: int = 0
    created_at: datetime = Field(default_factory=now)


class MemoryScope(SQLModel, table=True):
    memory_id: str = Field(primary_key=True)  # MemorySummary.id
    persona_id: str = Field(index=True)
    participant_id: str = Field(default="", index=True)


class PersonaConfig(SQLModel, table=True):
    persona_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    language: str = "en"
    greeting: str = ""  # non-empty -> agent speaks first
    objectives: str = "[]"  # JSON list of {name, description, success_criteria?, output_variables?}
    guardrails: str = "[]"  # JSON list of {name?, rule, forbidden_phrases?}
    guardrail_fallback: str = "Sorry, I can't help with that."
    memory_enabled: bool = True
    llm_base_url: str = ""  # OpenAI-compatible endpoint; "" = persona.llm (local Ollama)
    llm_model: str = ""
    llm_api_key_enc: str = ""  # encrypted (secretbox); never returned by the API
    stt_model: str = ""  # whisper model override ("" = default for language)
    updated_at: datetime = Field(default_factory=now)


class PersonaTool(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("tool"), primary_key=True)
    persona_id: str = Field(index=True)
    account_id: str = Field(index=True)
    name: str
    description: str = ""
    parameters: str = "{}"  # JSON schema of the arguments
    webhook_url: str
    secret_enc: str = ""  # optional HMAC secret for signing tool calls (encrypted)
    timeout_s: float = 8.0
    created_at: datetime = Field(default_factory=now)


class ObjectiveProgress(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("obj"), primary_key=True)
    conversation_id: str = Field(index=True)
    name: str
    completed: bool = False
    evidence: str = ""
    variables: str = "{}"
    completed_at: Optional[datetime] = None


class ToolCallLog(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("tc"), primary_key=True)
    conversation_id: str = Field(index=True)
    tool_name: str
    arguments: str = "{}"
    result: str = ""
    ok: bool = True
    duration_ms: int = 0
    created_at: datetime = Field(default_factory=now)


class WebhookEndpoint(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("wh"), primary_key=True)
    account_id: str = Field(index=True)
    url: str
    secret: str  # signing secret ("whsec_..."); shown on create, usable for HMAC verification
    events: str = "*"  # comma separated event names or "*"
    active: bool = True
    description: str = ""
    created_at: datetime = Field(default_factory=now)


class WebhookDelivery(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("whd"), primary_key=True)
    account_id: str = Field(index=True)
    endpoint_id: str = Field(index=True)
    event: str
    event_id: str = ""
    payload: str  # JSON body that is sent
    status: str = "pending"  # pending | delivered | failed (gave up)
    attempts: int = 0
    next_attempt_at: datetime = Field(default_factory=now, index=True)
    last_status_code: Optional[int] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=now)
    delivered_at: Optional[datetime] = None


class VideoBatch(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("vb"), primary_key=True)
    account_id: str = Field(index=True)
    kind: str = "bulk"  # bulk | translate
    replica_id: str = ""
    total: int = 0
    created_at: datetime = Field(default_factory=now)


class VideoBatchItem(SQLModel, table=True):
    video_id: str = Field(primary_key=True)
    batch_id: str = Field(index=True)
    row_index: int = 0
    language: str = ""
    variables: str = "{}"
    rendered_script: str = ""


class ShareLink(SQLModel, table=True):
    token: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    persona_id: str = Field(index=True)
    label: str = ""
    max_seconds: int = 300  # per session
    max_total_seconds: int = 3600  # cost cap across all sessions of this link
    max_sessions_per_hour: int = 20  # per link
    max_sessions_per_ip_hour: int = 5
    used_seconds: int = 0
    sessions_started: int = 0
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now)


class ShareSession(SQLModel, table=True):
    conversation_id: str = Field(primary_key=True)
    token: str = Field(index=True)
    ip: str = ""
    allowed_seconds: int = 0
    started_at: datetime = Field(default_factory=now)
    counted: bool = False  # used_seconds already added to the link
