"""Message models based on PRD API design."""

from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class MessageType(str, Enum):
    """Type of message content."""

    TEXT = "text"
    AUDIO = "audio"


class ClientId(str, Enum):
    """Supported client identifiers."""

    WHATSAPP = "whatsapp"
    WEB = "web"
    TELEGRAM = "telegram"


class MessageRequest(BaseModel):
    """Request body for POST /api/v1/message."""

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
    callback_url: str | None = Field(default=None, description="URL for async response delivery")

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
