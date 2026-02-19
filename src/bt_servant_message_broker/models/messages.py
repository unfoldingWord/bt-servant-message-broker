"""Message models based on PRD API design."""

import ipaddress
from enum import Enum
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


class MessageType(str, Enum):
    """Type of message content."""

    TEXT = "text"
    AUDIO = "audio"


class ClientId(str, Enum):
    """Supported client identifiers."""

    WHATSAPP = "whatsapp"
    WEB = "web"
    TELEGRAM = "telegram"


class _BaseMessageRequest(BaseModel):
    """Shared fields and validation for message submission requests.

    Both MessageRequest (callback delivery) and StreamRequest (SSE streaming)
    inherit from this base class.
    """

    user_id: str = Field(..., description="Unique user identifier")
    org_id: str = Field(..., description="Organization identifier")
    message: str = Field(
        default="", description="Message content (required for text, optional for audio)"
    )
    message_type: MessageType = Field(default=MessageType.TEXT, description="Type of message")
    audio_base64: str | None = Field(default=None, description="Base64-encoded audio data")
    audio_format: str | None = Field(default=None, description="Audio format (e.g., 'ogg', 'mp3')")
    client_id: ClientId = Field(..., description="Originating client identifier")
    client_message_id: str | None = Field(default=None, description="Client-side message ID")

    @model_validator(mode="after")
    def validate_audio_requirements(self) -> Self:
        """Validate audio-specific requirements.

        When message_type is audio:
        - audio_base64 and audio_format are required
        - message can be empty

        When message_type is text:
        - message is required (non-empty)
        """
        if self.message_type == MessageType.AUDIO:
            if not self.audio_base64:
                raise ValueError("audio_base64 is required when message_type is 'audio'")
            if not self.audio_format:
                raise ValueError("audio_format is required when message_type is 'audio'")
        elif self.message_type == MessageType.TEXT:
            if not self.message:
                raise ValueError("message is required when message_type is 'text'")
        return self


class MessageRequest(_BaseMessageRequest):
    """Request body for POST /api/v1/message."""

    callback_url: str | None = Field(
        default=None,
        description="URL for async response delivery (HTTPS required, omit for SSE streaming)",
    )

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url_security(cls, v: str | None) -> str | None:
        """Validate callback URL to prevent SSRF.

        Requires HTTPS scheme and blocks private/loopback/link-local IP ranges.
        Skips validation when callback_url is None (SSE streaming mode).
        """
        if v is None:
            return v
        parsed = urlparse(v)
        if parsed.scheme != "https":
            raise ValueError("callback_url must use HTTPS")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("callback_url must have a valid hostname")
        if hostname == "localhost":
            raise ValueError("callback_url must not target localhost")
        try:
            addr = ipaddress.ip_address(hostname)
        except ValueError:
            pass  # Not an IP address (it's a hostname) - OK
        else:
            if (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_reserved
                or addr.is_unspecified
            ):
                raise ValueError("callback_url must not target private/internal networks")
        return v


class StreamRequest(_BaseMessageRequest):
    """Request body for POST /api/v1/stream (SSE streaming, no callback)."""


class QueuedResponse(BaseModel):
    """Response when message is queued for async processing."""

    status: Literal["queued"] = "queued"
    queue_position: int = Field(..., description="Position in user's queue")
    message_id: str = Field(..., description="Broker-assigned message ID")


class QueueStatusResponse(BaseModel):
    """Response for GET /api/v1/queue/{user_id}."""

    user_id: str
    queue_length: int
    is_processing: bool
    current_message_id: str | None = None


class MessageResponse(BaseModel):
    """Union response - either queued or completed."""

    status: Literal["queued", "completed"]
    message_id: str
    queue_position: int | None = None  # Only for queued
    responses: list[str] | None = None  # Only for completed
    response_language: str | None = None  # Only for completed
    voice_audio_base64: str | None = None  # Only for completed


class HealthResponse(BaseModel):
    """Response for GET /health."""

    status: Literal["healthy", "degraded", "unhealthy"]
    redis_connected: bool
    active_queues: int
    messages_processing: int
    worker_connected: bool = False
