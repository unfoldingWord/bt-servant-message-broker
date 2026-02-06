"""Tests for MessageProcessor."""

import json
from unittest.mock import AsyncMock

import pytest

from bt_servant_message_broker.services.message_processor import MessageProcessor
from bt_servant_message_broker.services.worker_client import (
    WorkerError,
    WorkerResponse,
    WorkerTimeoutError,
)


@pytest.fixture
def mock_queue_manager() -> AsyncMock:
    """Create a mock QueueManager."""
    mock = AsyncMock()
    mock.dequeue = AsyncMock(return_value=None)
    mock.mark_complete = AsyncMock(return_value=True)
    mock.get_queue_length = AsyncMock(return_value=0)
    return mock


@pytest.fixture
def mock_worker_client() -> AsyncMock:
    """Create a mock WorkerClient."""
    mock = AsyncMock()
    mock.send_message = AsyncMock(
        return_value=WorkerResponse(
            responses=["Hello!"],
            response_language="en",
            voice_audio_base64=None,
        )
    )
    mock.health_check = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def processor(mock_queue_manager: AsyncMock, mock_worker_client: AsyncMock) -> MessageProcessor:
    """Create a MessageProcessor with mocked dependencies."""
    return MessageProcessor(mock_queue_manager, mock_worker_client)


class TestMessageProcessor:
    """Tests for MessageProcessor."""

    @pytest.mark.asyncio
    async def test_process_message_not_first_in_queue(
        self, processor: MessageProcessor, mock_queue_manager: AsyncMock
    ) -> None:
        """Test that None is returned when message is not first in queue."""
        # queue_position > 1 means we're not first
        result = await processor.process_message("user1", "msg1", "{}", queue_position=2)

        assert result is None
        # Should not even try to dequeue
        mock_queue_manager.dequeue.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_message_user_busy(
        self, processor: MessageProcessor, mock_queue_manager: AsyncMock
    ) -> None:
        """Test that None is returned when user is already processing."""
        mock_queue_manager.dequeue.return_value = None

        result = await processor.process_message("user1", "msg1", "{}", queue_position=1)

        assert result is None
        mock_queue_manager.dequeue.assert_called_once_with("user1")

    @pytest.mark.asyncio
    async def test_process_message_success(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test successful message processing."""
        message_data = json.dumps(
            {
                "user_id": "user1",
                "org_id": "org1",
                "message": "Hello",
                "client_id": "web",
            }
        )
        mock_queue_manager.dequeue.return_value = ("msg1", message_data)

        result = await processor.process_message("user1", "msg1", message_data, queue_position=1)

        assert result is not None
        assert result.responses == ["Hello!"]
        mock_worker_client.send_message.assert_called_once()
        mock_queue_manager.mark_complete.assert_called_once_with("user1", "msg1")

    @pytest.mark.asyncio
    async def test_process_message_always_marks_complete(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that mark_complete is called even on worker errors."""
        message_data = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg1", message_data)
        mock_worker_client.send_message.side_effect = WorkerError(500, "Server error")

        with pytest.raises(WorkerError):
            await processor.process_message("user1", "msg1", message_data, queue_position=1)

        # mark_complete should still be called
        mock_queue_manager.mark_complete.assert_called_once_with("user1", "msg1")

    @pytest.mark.asyncio
    async def test_process_message_timeout_marks_complete(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that mark_complete is called on timeout."""
        message_data = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg1", message_data)
        mock_worker_client.send_message.side_effect = WorkerTimeoutError(60.0)

        with pytest.raises(WorkerTimeoutError):
            await processor.process_message("user1", "msg1", message_data, queue_position=1)

        mock_queue_manager.mark_complete.assert_called_once_with("user1", "msg1")

    @pytest.mark.asyncio
    async def test_message_id_mismatch_returns_none(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that mismatched message IDs return None (don't return wrong response)."""
        msg = json.dumps({"user_id": "user1"})
        # Return different message ID than expected - this is a race condition
        mock_queue_manager.dequeue.return_value = ("msg_different", msg)

        result = await processor.process_message("user1", "msg1", msg, queue_position=1)

        # Should return None to avoid returning wrong response to client
        assert result is None
        # The mismatched message should still be processed and marked complete
        mock_worker_client.send_message.assert_called_once()
        mock_queue_manager.mark_complete.assert_called_once_with("user1", "msg_different")

    @pytest.mark.asyncio
    async def test_message_id_mismatch_handles_worker_error(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that worker errors during mismatch processing are handled gracefully."""
        msg = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg_different", msg)
        mock_worker_client.send_message.side_effect = WorkerError(500, "Server error")

        # Should not raise - error is logged but we return None
        result = await processor.process_message("user1", "msg1", msg, queue_position=1)

        assert result is None
        # mark_complete should still be called for the mismatched message
        mock_queue_manager.mark_complete.assert_called_once_with("user1", "msg_different")

    @pytest.mark.asyncio
    async def test_no_queue_draining_in_request_path(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that queue is not drained in the request path (no recursive processing)."""
        message_data = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg1", message_data)
        # Simulate more messages in queue
        mock_queue_manager.get_queue_length.return_value = 5

        await processor.process_message("user1", "msg1", message_data, queue_position=1)

        # Should only call dequeue once (not recursively drain queue)
        assert mock_queue_manager.dequeue.call_count == 1
        assert mock_queue_manager.mark_complete.call_count == 1
