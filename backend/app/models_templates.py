"""Tables for persona templates, the embeddable widget and per-persona integrations (new tables only)."""
from datetime import datetime

from sqlmodel import Field, SQLModel

from .db import now


class TemplateInstance(SQLModel, table=True):
    """Which template a persona was created from (provenance + analytics)."""
    persona_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    template_id: str = Field(index=True)
    template_version: int = 1
    created_at: datetime = Field(default_factory=now)


class WidgetConfig(SQLModel, table=True):
    """Embed widget settings for one guest share token (ShareLink.token) plus the allowed embedding domains."""
    token: str = Field(primary_key=True)  # ShareLink.token
    account_id: str = Field(index=True)
    persona_id: str = Field(index=True)
    allowed_domains: str = "[]"  # JSON list of host patterns ("example.com", "*.example.com", "localhost:8000"); [] = any site
    label: str = "Talk to us"
    color: str = "#6d5efc"
    position: str = "bottom-right"  # bottom-right | bottom-left
    greeting: str = ""  # teaser bubble text next to the button
    language: str = ""  # conversation language ("" = persona default)
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class PersonaIntegration(SQLModel, table=True):
    """Optional webhooks behind the built-in book_meeting and send_notification tools."""
    persona_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    booking_url: str = ""
    booking_secret_enc: str = ""
    notify_url: str = ""
    notify_secret_enc: str = ""
    updated_at: datetime = Field(default_factory=now)
