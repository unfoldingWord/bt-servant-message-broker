"""Integration tests for queue operations with real Redis.

These tests require a running Redis instance and are skipped in CI
unless explicitly enabled. Run with: pytest -m integration

To run locally:
1. Start Redis: docker run -d -p 6379:6379 redis:alpine
2. Run tests: pytest -m integration tests/integration/
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis.asyncio import Redis

from bt_servant_message_broker.services.queue_manager import QueueManager

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture
async def real_redis() -> AsyncIterator[Any]:
    """Real Redis client for integration tests."""
    redis: Any = Redis.from_url("redis://localhost:6379", decode_responses=True)
    try:
        await redis.ping()
    except Exception:
        pytest.skip("Redis not available")
    yield redis
    await redis.flushdb()
    await redis.close()


@pytest.fixture
def real_queue_manager(real_redis: Any) -> QueueManager:
    """QueueManager with real Redis."""
    return QueueManager(real_redis)


class TestQueueLifecycle:
    """Integration tests for full queue lifecycle."""

    async def test_enqueue_single_message(self, real_queue_manager: QueueManager) -> None:
        """Test enqueueing a single message."""
        position = await real_queue_manager.enqueue("user1", "msg1", '{"message":"hello"}')
        assert position == 1
        assert await real_queue_manager.get_queue_length("user1") == 1

    async def test_enqueue_multiple_messages(self, real_queue_manager: QueueManager) -> None:
        """Test enqueueing multiple messages returns correct positions."""
        pos1 = await real_queue_manager.enqueue("user1", "msg1", '{"order":1}')
        pos2 = await real_queue_manager.enqueue("user1", "msg2", '{"order":2}')
        pos3 = await real_queue_manager.enqueue("user1", "msg3", '{"order":3}')

        assert pos1 == 1
        assert pos2 == 2
        assert pos3 == 3
        assert await real_queue_manager.get_queue_length("user1") == 3

    async def test_dequeue_fifo_order(self, real_queue_manager: QueueManager) -> None:
        """Test that messages are dequeued in FIFO order."""
        await real_queue_manager.enqueue("user1", "msg1", '{"order":1}')
        await real_queue_manager.enqueue("user1", "msg2", '{"order":2}')
        await real_queue_manager.enqueue("user1", "msg3", '{"order":3}')

        result1 = await real_queue_manager.dequeue("user1")
        assert result1 is not None
        assert result1[0] == "msg1"
        assert '{"order":1}' in result1[1]

        # Complete first before getting next
        await real_queue_manager.mark_complete("user1", "msg1")

        result2 = await real_queue_manager.dequeue("user1")
        assert result2 is not None
        assert result2[0] == "msg2"

    async def test_dequeue_empty_queue(self, real_queue_manager: QueueManager) -> None:
        """Test that dequeue returns None for empty queue."""
        result = await real_queue_manager.dequeue("nonexistent_user")
        assert result is None

    async def test_processing_flag_set_on_dequeue(self, real_queue_manager: QueueManager) -> None:
        """Test that processing flag is set when dequeuing."""
        await real_queue_manager.enqueue("user1", "msg1", '{"data":"test"}')

        assert await real_queue_manager.is_processing("user1") is False

        await real_queue_manager.dequeue("user1")

        assert await real_queue_manager.is_processing("user1") is True
        assert await real_queue_manager.get_current_message_id("user1") == "msg1"

    async def test_mark_complete_clears_processing(self, real_queue_manager: QueueManager) -> None:
        """Test that mark_complete clears the processing flag."""
        await real_queue_manager.enqueue("user1", "msg1", '{"data":"test"}')
        await real_queue_manager.dequeue("user1")

        assert await real_queue_manager.is_processing("user1") is True

        await real_queue_manager.mark_complete("user1", "msg1")

        assert await real_queue_manager.is_processing("user1") is False
        assert await real_queue_manager.get_current_message_id("user1") is None


class TestMultipleUsers:
    """Integration tests for multiple user queues."""

    async def test_queues_isolated_per_user(self, real_queue_manager: QueueManager) -> None:
        """Test that queues are isolated per user."""
        await real_queue_manager.enqueue("user1", "msg1a", '{"user":1}')
        await real_queue_manager.enqueue("user1", "msg1b", '{"user":1}')
        await real_queue_manager.enqueue("user2", "msg2a", '{"user":2}')

        assert await real_queue_manager.get_queue_length("user1") == 2
        assert await real_queue_manager.get_queue_length("user2") == 1

    async def test_processing_isolated_per_user(self, real_queue_manager: QueueManager) -> None:
        """Test that processing flags are isolated per user."""
        await real_queue_manager.enqueue("user1", "msg1", "{}")
        await real_queue_manager.enqueue("user2", "msg2", "{}")

        await real_queue_manager.dequeue("user1")

        assert await real_queue_manager.is_processing("user1") is True
        assert await real_queue_manager.is_processing("user2") is False


class TestStatistics:
    """Integration tests for queue statistics."""

    async def test_active_queue_count(self, real_queue_manager: QueueManager) -> None:
        """Test counting active queues."""
        # Initially no active queues
        assert await real_queue_manager.get_active_queue_count() == 0

        # Add messages for two users
        await real_queue_manager.enqueue("user1", "msg1", "{}")
        await real_queue_manager.enqueue("user2", "msg2", "{}")

        assert await real_queue_manager.get_active_queue_count() == 2

        # Dequeue one user's message
        await real_queue_manager.dequeue("user1")

        assert await real_queue_manager.get_active_queue_count() == 1

    async def test_processing_count(self, real_queue_manager: QueueManager) -> None:
        """Test counting messages being processed."""
        await real_queue_manager.enqueue("user1", "msg1", "{}")
        await real_queue_manager.enqueue("user2", "msg2", "{}")

        assert await real_queue_manager.get_processing_count() == 0

        await real_queue_manager.dequeue("user1")
        assert await real_queue_manager.get_processing_count() == 1

        await real_queue_manager.dequeue("user2")
        assert await real_queue_manager.get_processing_count() == 2

        await real_queue_manager.mark_complete("user1", "msg1")
        assert await real_queue_manager.get_processing_count() == 1


class TestPing:
    """Integration tests for Redis connectivity check."""

    async def test_ping_succeeds(self, real_queue_manager: QueueManager) -> None:
        """Test that ping returns True when Redis is connected."""
        assert await real_queue_manager.ping() is True
