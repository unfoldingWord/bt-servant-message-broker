"""Tests for Pydantic models."""

import pytest
from pydantic import ValidationError

from bt_servant_message_broker.models import (
    ClientId,
    HealthResponse,
    MessageRequest,
    MessageType,
    QueuedResponse,
    QueueStatusResponse,
)


class TestMessageRequest:
    """Tests for MessageRequest model."""

    def test_minimal_request(self) -> None:
        """Test creating a request with only required fields."""
        request = MessageRequest(
            user_id="user123",
            org_id="org456",
            message="Hello",
            client_id=ClientId.WEB,
        )
        assert request.user_id == "user123"
        assert request.org_id == "org456"
        assert request.message == "Hello"
        assert request.client_id == ClientId.WEB
        assert request.message_type == MessageType.TEXT
        assert request.audio_base64 is None
        assert request.audio_format is None
        assert request.client_message_id is None
        assert request.callback_url is None

    def test_full_request(self) -> None:
        """Test creating a request with all fields."""
        request = MessageRequest(
            user_id="user123",
            org_id="org456",
            message="",
            message_type=MessageType.AUDIO,
            audio_base64="base64data",
            audio_format="ogg",
            client_id=ClientId.WHATSAPP,
            client_message_id="msg789",
            callback_url="https://example.com/callback",
        )
        assert request.message_type == MessageType.AUDIO
        assert request.audio_base64 == "base64data"
        assert request.audio_format == "ogg"
        assert request.client_id == ClientId.WHATSAPP
        assert request.client_message_id == "msg789"
        assert request.callback_url == "https://example.com/callback"

    def test_missing_required_fields(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            MessageRequest(user_id="user123")  # type: ignore[call-arg]

    def test_invalid_client_id(self) -> None:
        """Test that invalid client_id raises ValidationError."""
        with pytest.raises(ValidationError):
            MessageRequest(
                user_id="user123",
                org_id="org456",
                message="Hello",
                client_id="invalid",  # type: ignore[arg-type]
            )

    def test_invalid_message_type(self) -> None:
        """Test that invalid message_type raises ValidationError."""
        with pytest.raises(ValidationError):
            MessageRequest(
                user_id="user123",
                org_id="org456",
                message="Hello",
                client_id=ClientId.WEB,
                message_type="invalid",  # type: ignore[arg-type]
            )

    def test_audio_requires_audio_base64(self) -> None:
        """Test that audio message_type requires audio_base64."""
        with pytest.raises(ValidationError) as exc_info:
            MessageRequest(
                user_id="user123",
                org_id="org456",
                message="",
                message_type=MessageType.AUDIO,
                audio_format="ogg",
                client_id=ClientId.WEB,
            )
        assert "audio_base64 is required" in str(exc_info.value)

    def test_audio_requires_audio_format(self) -> None:
        """Test that audio message_type requires audio_format."""
        with pytest.raises(ValidationError) as exc_info:
            MessageRequest(
                user_id="user123",
                org_id="org456",
                message="",
                message_type=MessageType.AUDIO,
                audio_base64="base64data",
                client_id=ClientId.WEB,
            )
        assert "audio_format is required" in str(exc_info.value)

    def test_text_requires_message(self) -> None:
        """Test that text message_type requires non-empty message."""
        with pytest.raises(ValidationError) as exc_info:
            MessageRequest(
                user_id="user123",
                org_id="org456",
                message="",
                message_type=MessageType.TEXT,
                client_id=ClientId.WEB,
            )
        assert "message is required" in str(exc_info.value)

    def test_audio_allows_empty_message(self) -> None:
        """Test that audio message_type allows empty message."""
        request = MessageRequest(
            user_id="user123",
            org_id="org456",
            message="",
            message_type=MessageType.AUDIO,
            audio_base64="base64data",
            audio_format="ogg",
            client_id=ClientId.WEB,
        )
        assert request.message == ""
        assert request.audio_base64 == "base64data"


class TestQueuedResponse:
    """Tests for QueuedResponse model."""

    def test_create_response(self) -> None:
        """Test creating a queued response."""
        response = QueuedResponse(
            queue_position=3,
            message_id="msg123",
        )
        assert response.status == "queued"
        assert response.queue_position == 3
        assert response.message_id == "msg123"

    def test_status_is_literal(self) -> None:
        """Test that status is always 'queued'."""
        response = QueuedResponse(queue_position=1, message_id="msg")
        assert response.status == "queued"


class TestQueueStatusResponse:
    """Tests for QueueStatusResponse model."""

    def test_idle_queue(self) -> None:
        """Test response for an idle queue."""
        response = QueueStatusResponse(
            user_id="user123",
            queue_length=0,
            is_processing=False,
        )
        assert response.user_id == "user123"
        assert response.queue_length == 0
        assert response.is_processing is False
        assert response.current_message_id is None

    def test_active_queue(self) -> None:
        """Test response for an active queue."""
        response = QueueStatusResponse(
            user_id="user123",
            queue_length=5,
            is_processing=True,
            current_message_id="msg789",
        )
        assert response.queue_length == 5
        assert response.is_processing is True
        assert response.current_message_id == "msg789"


class TestHealthResponse:
    """Tests for HealthResponse model."""

    def test_healthy_status(self) -> None:
        """Test healthy status response."""
        response = HealthResponse(
            status="healthy",
            redis_connected=True,
            active_queues=10,
            messages_processing=3,
        )
        assert response.status == "healthy"
        assert response.redis_connected is True
        assert response.active_queues == 10
        assert response.messages_processing == 3

    def test_degraded_status(self) -> None:
        """Test degraded status response."""
        response = HealthResponse(
            status="degraded",
            redis_connected=True,
            active_queues=0,
            messages_processing=0,
        )
        assert response.status == "degraded"

    def test_unhealthy_status(self) -> None:
        """Test unhealthy status response."""
        response = HealthResponse(
            status="unhealthy",
            redis_connected=False,
            active_queues=0,
            messages_processing=0,
        )
        assert response.status == "unhealthy"
        assert response.redis_connected is False


class TestClientId:
    """Tests for ClientId enum."""

    def test_all_values(self) -> None:
        """Test all client ID values are accessible."""
        assert ClientId.WHATSAPP.value == "whatsapp"
        assert ClientId.WEB.value == "web"
        assert ClientId.TELEGRAM.value == "telegram"


class TestMessageType:
    """Tests for MessageType enum."""

    def test_all_values(self) -> None:
        """Test all message type values are accessible."""
        assert MessageType.TEXT.value == "text"
        assert MessageType.AUDIO.value == "audio"
