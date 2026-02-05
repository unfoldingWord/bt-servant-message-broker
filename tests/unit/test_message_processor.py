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
    async def test_process_message_user_busy(
        self, processor: MessageProcessor, mock_queue_manager: AsyncMock
    ) -> None:
        """Test that None is returned when user is already processing."""
        mock_queue_manager.dequeue.return_value = None

        result = await processor.process_message("user1", "msg1", "{}")

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

        result = await processor.process_message("user1", "msg1", message_data)

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
            await processor.process_message("user1", "msg1", message_data)

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
            await processor.process_message("user1", "msg1", message_data)

        mock_queue_manager.mark_complete.assert_called_once_with("user1", "msg1")

    @pytest.mark.asyncio
    async def test_process_message_processes_next_in_queue(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that next message is processed after completing one."""
        first_msg = json.dumps({"user_id": "user1", "message": "First"})
        second_msg = json.dumps({"user_id": "user1", "message": "Second"})

        # First dequeue returns msg1, second returns msg2, third returns None
        mock_queue_manager.dequeue.side_effect = [
            ("msg1", first_msg),
            ("msg2", second_msg),
            None,
        ]
        mock_queue_manager.get_queue_length.side_effect = [1, 0]

        await processor.process_message("user1", "msg1", first_msg)

        # Worker should be called twice (for both messages)
        assert mock_worker_client.send_message.call_count == 2
        # mark_complete should be called twice
        assert mock_queue_manager.mark_complete.call_count == 2

    @pytest.mark.asyncio
    async def test_process_next_handles_errors_gracefully(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that errors in _process_next don't propagate."""
        first_msg = json.dumps({"user_id": "user1", "message": "First"})
        second_msg = json.dumps({"user_id": "user1", "message": "Second"})

        mock_queue_manager.dequeue.side_effect = [
            ("msg1", first_msg),
            ("msg2", second_msg),
            None,
        ]
        mock_queue_manager.get_queue_length.side_effect = [1, 0]

        # Second message fails
        mock_worker_client.send_message.side_effect = [
            WorkerResponse(responses=["First!"], response_language="en"),
            WorkerError(500, "Server error"),
        ]

        # Should not raise - the error in _process_next is logged but not raised
        result = await processor.process_message("user1", "msg1", first_msg)

        assert result is not None
        assert result.responses == ["First!"]
        # Both mark_complete calls should happen
        assert mock_queue_manager.mark_complete.call_count == 2

    @pytest.mark.asyncio
    async def test_process_next_stops_on_empty_queue(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
    ) -> None:
        """Test that _process_next stops when queue is empty."""
        msg = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg1", msg)
        mock_queue_manager.get_queue_length.return_value = 0

        await processor.process_message("user1", "msg1", msg)

        # dequeue should only be called once (for the initial message)
        assert mock_queue_manager.dequeue.call_count == 1

    @pytest.mark.asyncio
    async def test_dequeued_message_id_mismatch_logs_warning(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that mismatched message IDs are logged but processing continues."""
        msg = json.dumps({"user_id": "user1"})
        # Return different message ID than expected
        mock_queue_manager.dequeue.return_value = ("msg_different", msg)
        mock_queue_manager.get_queue_length.return_value = 0

        result = await processor.process_message("user1", "msg1", msg)

        # Processing should still succeed
        assert result is not None
        # mark_complete should use the dequeued message ID
        mock_queue_manager.mark_complete.assert_called_once_with("user1", "msg_different")
