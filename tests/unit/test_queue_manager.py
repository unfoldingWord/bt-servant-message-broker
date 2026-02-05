"""Tests for QueueManager."""

import json
from unittest.mock import AsyncMock

from bt_servant_message_broker.services.queue_manager import QueueManager


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


class TestDequeue:
    """Tests for dequeue method."""

    async def test_empty_queue_returns_none(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that dequeue returns None for empty queue."""
        mock_redis.lpop.return_value = None
        result = await queue_manager.dequeue("user1")
        assert result is None

    async def test_returns_message_tuple(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that dequeue returns (message_id, message_data) tuple."""
        mock_redis.lpop.return_value = json.dumps({"id": "msg1", "data": '{"test":1}'})
        result = await queue_manager.dequeue("user1")
        assert result is not None
        assert result[0] == "msg1"
        assert result[1] == '{"test":1}'

    async def test_sets_processing_flag(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that dequeue sets processing flag with TTL."""
        mock_redis.lpop.return_value = json.dumps({"id": "msg1", "data": "{}"})
        await queue_manager.dequeue("user1")
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "user:user1:processing"
        assert call_args[0][1] == QueueManager.PROCESSING_TTL
        assert call_args[0][2] == "msg1"

    async def test_updates_message_metadata(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that dequeue updates message metadata with started_at."""
        mock_redis.lpop.return_value = json.dumps({"id": "msg1", "data": "{}"})
        await queue_manager.dequeue("user1")
        # hset should be called to update started_at
        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == "message:msg1"
        assert call_args[0][1] == "started_at"


class TestMarkComplete:
    """Tests for mark_complete method."""

    async def test_deletes_processing_flag(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that mark_complete deletes processing flag."""
        await queue_manager.mark_complete("user1", "msg1")
        calls = mock_redis.delete.call_args_list
        keys_deleted = [call[0][0] for call in calls]
        assert "user:user1:processing" in keys_deleted

    async def test_deletes_message_metadata(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that mark_complete cleans up message metadata."""
        await queue_manager.mark_complete("user1", "msg1")
        calls = mock_redis.delete.call_args_list
        keys_deleted = [call[0][0] for call in calls]
        assert "message:msg1" in keys_deleted


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


class TestFifoOrdering:
    """Tests for FIFO ordering behavior."""

    async def test_messages_dequeued_in_order(
        self, queue_manager: QueueManager, mock_redis: AsyncMock
    ) -> None:
        """Test that messages are dequeued in FIFO order."""
        # Simulate three messages in queue
        messages = [
            json.dumps({"id": "msg1", "data": '{"order":1}'}),
            json.dumps({"id": "msg2", "data": '{"order":2}'}),
            json.dumps({"id": "msg3", "data": '{"order":3}'}),
        ]
        mock_redis.lpop.side_effect = messages

        # Dequeue should return in order
        result1 = await queue_manager.dequeue("user1")
        result2 = await queue_manager.dequeue("user1")
        result3 = await queue_manager.dequeue("user1")

        assert result1 is not None and result1[0] == "msg1"
        assert result2 is not None and result2[0] == "msg2"
        assert result3 is not None and result3[0] == "msg3"
