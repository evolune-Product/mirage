"""Lead capture tables (new tables only). Importing registers them on SQLModel.metadata."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from .db import new_id, now


class Lead(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("ld"), primary_key=True)
    account_id: str = Field(index=True)
    conversation_id: str = Field(index=True)  # one lead per conversation (later capture_lead calls merge into it)
    persona_id: str = Field(index=True)
    name: str = ""
    email: str = ""
    phone: str = ""  # normalised: optional leading +, digits only
    company: str = ""
    interest: str = ""
    notes: str = ""
    extra: str = "{}"  # JSON: any additional string fields the model supplied
    consent: bool = False  # the person agreed to be contacted / to have these details stored
    consent_text: str = ""  # how the data use was disclosed (the persona's disclosure text at capture time)
    source: str = "tool"  # tool | api
    created_at: datetime = Field(default_factory=now, index=True)
    updated_at: Optional[datetime] = None


class LeadCaptureConfig(SQLModel, table=True):
    """Per-persona lead capture settings. A persona without a row has lead capture OFF (legacy personas are unchanged);
    personas created from a template get a row with enabled=True."""
    persona_id: str = Field(primary_key=True)
    account_id: str = Field(index=True)
    enabled: bool = True
    required_fields: str = '["name"]'  # JSON; at least one of email/phone is always required as well
    require_consent: bool = True  # the tool refuses to save unless the person agreed
    disclosure: str = ("Your details are used only so our team can follow up with you, and you can ask us to delete them at any time.")
    updated_at: datetime = Field(default_factory=now)
