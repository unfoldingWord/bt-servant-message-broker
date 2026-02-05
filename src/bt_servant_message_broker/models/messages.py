"""Message models based on PRD API design."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


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
    message: str = Field(..., description="Message content")
    message_type: MessageType = Field(default=MessageType.TEXT, description="Type of message")
    audio_base64: str | None = Field(default=None, description="Base64-encoded audio data")
    audio_format: str | None = Field(default=None, description="Audio format (e.g., 'ogg', 'mp3')")
    client_id: ClientId = Field(..., description="Originating client identifier")
    client_message_id: str | None = Field(default=None, description="Client-side message ID")
    callback_url: str | None = Field(default=None, description="URL for async response delivery")


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


class HealthResponse(BaseModel):
    """Response for GET /health."""

    status: Literal["healthy", "degraded", "unhealthy"]
    redis_connected: bool
    active_queues: int
    messages_processing: int
