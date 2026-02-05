"""Tests for QueueManager."""

import json
from unittest.mock import AsyncMock

from bt_servant_message_broker.services.queue_manager import QueueManager
from tests.conftest import make_queue_entry


class TestGenerateMessageId:
    """Tests for generate_message_id static method."""

    def test_returns_string(self) -> None:
        """Test that generate_message_id returns a string."""
        message_id = QueueManager.generate_message_id()
        assert isinstance(message_id, str)

    def test_returns_uuid_format(self) -> None:
        """Test that message ID is in UUID format."""
        message_id = QueueManager.generate_message_id()
        # UUID4 format: 8-4-4-4-12 hex characters
        parts = message_id.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12

    def test_returns_unique_ids(self) -> None:
        """Test that consecutive calls return unique IDs."""
        ids = [QueueManager.generate_message_id() for _ in range(100)]
        assert len(set(ids)) == 100


class TestEnqueue:
    """Tests for enqueue method."""

    async def test_returns_position(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that enqueue returns queue position."""
        mock_redis.rpush.return_value = 3
        position = await queue_manager.enqueue("user1", "msg1", '{"data":"test"}')
        assert position == 3

    async def test_calls_rpush_with_correct_key(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that enqueue uses correct Redis key."""
        await queue_manager.enqueue("user1", "msg1", '{"data":"test"}')
        call_args = mock_redis.rpush.call_args
        assert call_args[0][0] == "user:user1:queue"

    async def test_stores_message_with_id(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that enqueue stores message data with ID."""
        await queue_manager.enqueue("user1", "msg1", '{"data":"test"}')
        call_args = mock_redis.rpush.call_args
        entry = json.loads(call_args[0][1])
        assert entry["id"] == "msg1"
        assert entry["data"] == '{"data":"test"}'

    async def test_stores_metadata_hash(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that enqueue stores message metadata in Redis hash."""
        await queue_manager.enqueue("user1", "msg1", '{"data":"test"}')
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == "message:msg1"
        assert "user_id" in call_args[1]["mapping"]
        assert "queued_at" in call_args[1]["mapping"]

    async def test_stores_client_id_in_metadata(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that enqueue extracts client_id from message_data."""
        message_data = '{"client_id": "web", "message": "hello"}'
        await queue_manager.enqueue("user1", "msg1", message_data)
        call_args = mock_redis.hset.call_args
        assert call_args[1]["mapping"]["client_id"] == "web"

    async def test_stores_callback_url_in_metadata(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that enqueue extracts callback_url from message_data."""
        message_data = '{"callback_url": "https://example.com/callback"}'
        await queue_manager.enqueue("user1", "msg1", message_data)
        call_args = mock_redis.hset.call_args
        assert call_args[1]["mapping"]["callback_url"] == "https://example.com/callback"

    async def test_skips_null_callback_url(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that enqueue skips null callback_url."""
        message_data = '{"callback_url": null}'
        await queue_manager.enqueue("user1", "msg1", message_data)
        call_args = mock_redis.hset.call_args
        assert "callback_url" not in call_args[1]["mapping"]


class TestDequeue:
    """Tests for dequeue method."""

    async def test_empty_queue_returns_none(
        self, queue_manager: QueueManager, mock_dequeue_script: AsyncMock
    ) -> None:
        """Test that dequeue returns None for empty queue."""
        mock_dequeue_script.return_value = None
        result = await queue_manager.dequeue("user1")
        assert result is None

    async def test_returns_message_tuple(
        self,
        queue_manager: QueueManager,
        mock_redis: AsyncMock,
        mock_dequeue_script: AsyncMock,
    ) -> None:
        """Test that dequeue returns (message_id, message_data) tuple."""
        mock_dequeue_script.return_value = make_queue_entry("msg1", '{"test":1}')
        result = await queue_manager.dequeue("user1")
        assert result is not None
        assert result[0] == "msg1"
        assert result[1] == '{"test":1}'

    async def test_already_processing_returns_none(
        self, queue_manager: QueueManager, mock_dequeue_script: AsyncMock
    ) -> None:
        """Test that dequeue returns None when user already has a message processing."""
        # Lua script returns None when processing flag exists
        mock_dequeue_script.return_value = None
        result = await queue_manager.dequeue("user1")
        assert result is None

    async def test_updates_message_metadata_with_started_at(
        self,
        queue_manager: QueueManager,
        mock_redis: AsyncMock,
        mock_dequeue_script: AsyncMock,
    ) -> None:
        """Test that dequeue updates message metadata with started_at."""
        mock_dequeue_script.return_value = make_queue_entry("msg1", "{}")
        await queue_manager.dequeue("user1")
        # hset should be called to update started_at
        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == "message:msg1"
        assert call_args[0][1] == "started_at"

    async def test_script_called_with_correct_keys(
        self,
        queue_manager: QueueManager,
        mock_dequeue_script: AsyncMock,
    ) -> None:
        """Test that dequeue calls script with correct Redis keys."""
        mock_dequeue_script.return_value = None
        await queue_manager.dequeue("user1")
        mock_dequeue_script.assert_called_once()
        call_kwargs = mock_dequeue_script.call_args[1]
        assert call_kwargs["keys"] == ["user:user1:queue", "user:user1:processing"]
        assert call_kwargs["args"] == [QueueManager.PROCESSING_TTL]


class TestMarkComplete:
    """Tests for mark_complete method."""

    async def test_returns_true_on_success(
        self, queue_manager: QueueManager, mock_mark_complete_script: AsyncMock
    ) -> None:
        """Test that mark_complete returns True when message ID matches."""
        mock_mark_complete_script.return_value = 1
        result = await queue_manager.mark_complete("user1", "msg1")
        assert result is True

    async def test_returns_false_on_stale_worker(
        self, queue_manager: QueueManager, mock_mark_complete_script: AsyncMock
    ) -> None:
        """Test that mark_complete returns False when message ID doesn't match."""
        mock_mark_complete_script.return_value = 0
        result = await queue_manager.mark_complete("user1", "msg1")
        assert result is False

    async def test_script_called_with_correct_keys(
        self, queue_manager: QueueManager, mock_mark_complete_script: AsyncMock
    ) -> None:
        """Test that mark_complete calls script with correct Redis keys."""
        await queue_manager.mark_complete("user1", "msg1")
        mock_mark_complete_script.assert_called_once()
        call_kwargs = mock_mark_complete_script.call_args[1]
        assert call_kwargs["keys"] == ["user:user1:processing", "message:msg1"]
        assert call_kwargs["args"] == ["msg1"]


class TestGetQueueLength:
    """Tests for get_queue_length method."""

    async def test_returns_length(self, queue_manager: QueueManager, mock_redis: AsyncMock) -> None:
        """Test that get_queue_length returns queue length."""
        mock_redis.llen.return_value = 5
        length = await queue_manager.get_queue_length("user1")
        assert length == 5

    async def test_uses_correct_key(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that get_queue_length uses correct Redis key."""
        await queue_manager.get_queue_length("user1")
        mock_redis.llen.assert_called_once_with("user:user1:queue")


class TestIsProcessing:
    """Tests for is_processing method."""

    async def test_returns_true_when_flag_exists(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that is_processing returns True when flag exists."""
        mock_redis.exists.return_value = 1
        result = await queue_manager.is_processing("user1")
        assert result is True

    async def test_returns_false_when_flag_missing(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that is_processing returns False when flag missing."""
        mock_redis.exists.return_value = 0
        result = await queue_manager.is_processing("user1")
        assert result is False

    async def test_uses_correct_key(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that is_processing uses correct Redis key."""
        await queue_manager.is_processing("user1")
        mock_redis.exists.assert_called_once_with("user:user1:processing")


class TestGetCurrentMessageId:
    """Tests for get_current_message_id method."""

    async def test_returns_message_id(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that get_current_message_id returns the message ID."""
        mock_redis.get.return_value = "msg123"
        result = await queue_manager.get_current_message_id("user1")
        assert result == "msg123"

    async def test_returns_none_when_not_processing(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that get_current_message_id returns None when not processing."""
        mock_redis.get.return_value = None
        result = await queue_manager.get_current_message_id("user1")
        assert result is None


class TestGetActiveQueueCount:
    """Tests for get_active_queue_count method."""

    async def test_returns_zero_for_no_queues(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that get_active_queue_count returns 0 when no queues exist."""
        mock_redis.scan.return_value = (0, [])
        count = await queue_manager.get_active_queue_count()
        assert count == 0

    async def test_counts_non_empty_queues(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that get_active_queue_count only counts non-empty queues."""
        mock_redis.scan.return_value = (0, ["user:a:queue", "user:b:queue"])
        # First queue has messages, second is empty
        mock_redis.llen.side_effect = [3, 0]
        count = await queue_manager.get_active_queue_count()
        assert count == 1


class TestGetProcessingCount:
    """Tests for get_processing_count method."""

    async def test_returns_zero_for_no_processing(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that get_processing_count returns 0 when nothing processing."""
        mock_redis.scan.return_value = (0, [])
        count = await queue_manager.get_processing_count()
        assert count == 0

    async def test_counts_processing_keys(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that get_processing_count counts all processing keys."""
        mock_redis.scan.return_value = (0, ["user:a:processing", "user:b:processing"])
        count = await queue_manager.get_processing_count()
        assert count == 2


class TestPing:
    """Tests for ping method."""

    async def test_returns_true_when_connected(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that ping returns True when Redis responds."""
        mock_redis.ping.return_value = True
        result = await queue_manager.ping()
        assert result is True

    async def test_returns_false_on_exception(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that ping returns False on exception."""
        mock_redis.ping.side_effect = Exception("Connection failed")
        result = await queue_manager.ping()
        assert result is False


class TestAtomicOperations:
    """Tests for atomic operation guarantees."""

    async def test_dequeue_is_atomic(
        self, queue_manager: QueueManager, mock_dequeue_script: AsyncMock
    ) -> None:
        """Test that dequeue uses atomic Lua script."""
        mock_dequeue_script.return_value = make_queue_entry("msg1", "{}")
        await queue_manager.dequeue("user1")
        # Script should be called exactly once (atomic operation)
        mock_dequeue_script.assert_called_once()

    async def test_mark_complete_is_atomic(
        self, queue_manager: QueueManager, mock_mark_complete_script: AsyncMock
    ) -> None:
        """Test that mark_complete uses atomic Lua script."""
        mock_mark_complete_script.return_value = 1
        await queue_manager.mark_complete("user1", "msg1")
        # Script should be called exactly once (atomic operation)
        mock_mark_complete_script.assert_called_once()

    async def test_concurrent_dequeue_blocked_by_processing_flag(
        self, queue_manager: QueueManager, mock_dequeue_script: AsyncMock
    ) -> None:
        """Test that concurrent dequeue is blocked when already processing.

        The Lua script checks EXISTS on processing flag before LPOP,
        so if another worker is processing, dequeue returns None.
        """
        # First dequeue succeeds
        mock_dequeue_script.return_value = make_queue_entry("msg1", "{}")
        result1 = await queue_manager.dequeue("user1")
        assert result1 is not None

        # Second dequeue returns None (processing flag exists in Lua script)
        mock_dequeue_script.return_value = None
        result2 = await queue_manager.dequeue("user1")
        assert result2 is None
