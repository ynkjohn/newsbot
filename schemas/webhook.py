"""Pydantic schemas for the WhatsApp webhook endpoint."""

from pydantic import BaseModel, Field, field_validator


class WhatsAppKeyPayload(BaseModel):
    remoteJid: str = Field(..., min_length=1)


class WhatsAppMessagePayload(BaseModel):
    conversation: str = Field(..., min_length=1)


class WhatsAppWebhookPayload(BaseModel):
    key: WhatsAppKeyPayload
    message: WhatsAppMessagePayload

    @field_validator("key", mode="before")
    @classmethod
    def validate_key(cls, value):
        if not isinstance(value, dict):
            raise ValueError("key must be a dict")
        return value

    @field_validator("message", mode="before")
    @classmethod
    def validate_message(cls, value):
        if not isinstance(value, dict):
            raise ValueError("message must be a dict")
        return value
