"""Redis queue operations for per-user message ordering."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any


class QueueManager:
    """Manages per-user message queues in Redis.

    Ensures FIFO ordering per user while allowing parallel processing across users.

    Redis Schema:
        user:{user_id}:queue - LIST for FIFO message queue
        user:{user_id}:processing - STRING with TTL for current processing message
        message:{msg_id} - HASH for message metadata
    """

    PROCESSING_TTL = 300  # 5 minutes - prevents stale locks if worker crashes

    def __init__(self, redis_client: Any) -> None:
        """Initialize the queue manager.

        Args:
            redis_client: Async Redis client instance.
        """
        self._redis: Any = redis_client

    @staticmethod
    def generate_message_id() -> str:
        """Generate a unique message ID.

        Returns:
            UUID string for message identification.
        """
        return str(uuid.uuid4())

    def _queue_key(self, user_id: str) -> str:
        """Get the Redis key for a user's queue."""
        return f"user:{user_id}:queue"

    def _processing_key(self, user_id: str) -> str:
        """Get the Redis key for a user's processing flag."""
        return f"user:{user_id}:processing"

    def _message_key(self, message_id: str) -> str:
        """Get the Redis key for message metadata."""
        return f"message:{message_id}"

    async def enqueue(self, user_id: str, message_id: str, message_data: str) -> int:
        """Add a message to the user's queue.

        Args:
            user_id: User identifier.
            message_id: Unique message identifier.
            message_data: JSON-serialized message data.

        Returns:
            Queue position (1-indexed).
        """
        queue_key = self._queue_key(user_id)
        message_key = self._message_key(message_id)

        # Store the message with its ID in the queue
        queue_entry = json.dumps({"id": message_id, "data": message_data})
        position: int = await self._redis.rpush(queue_key, queue_entry)

        # Store message metadata for debugging/monitoring
        queued_at = datetime.now(timezone.utc).isoformat()
        await self._redis.hset(
            message_key,
            mapping={
                "user_id": user_id,
                "queued_at": queued_at,
            },
        )

        return position

    async def dequeue(self, user_id: str) -> tuple[str, str] | None:
        """Remove and return the next message from the user's queue.

        Sets the processing flag with TTL to prevent stale locks.

        Args:
            user_id: User identifier.

        Returns:
            Tuple of (message_id, message_data) or None if queue is empty.
        """
        queue_key = self._queue_key(user_id)
        processing_key = self._processing_key(user_id)

        # Get the next message from the queue
        entry: str | None = await self._redis.lpop(queue_key)
        if entry is None:
            return None

        # Parse the queue entry
        parsed = json.loads(entry)
        message_id: str = parsed["id"]
        message_data: str = parsed["data"]

        # Set processing flag with TTL
        await self._redis.setex(processing_key, self.PROCESSING_TTL, message_id)

        # Update message metadata with processing start time
        message_key = self._message_key(message_id)
        started_at = datetime.now(timezone.utc).isoformat()
        await self._redis.hset(message_key, "started_at", started_at)

        return (message_id, message_data)

    async def mark_complete(self, user_id: str, message_id: str) -> None:
        """Mark a message as complete and clear the processing flag.

        Args:
            user_id: User identifier.
            message_id: Message identifier.
        """
        processing_key = self._processing_key(user_id)
        message_key = self._message_key(message_id)

        # Clear processing flag
        await self._redis.delete(processing_key)

        # Clean up message metadata
        await self._redis.delete(message_key)

    async def get_queue_length(self, user_id: str) -> int:
        """Get the number of messages in a user's queue.

        Args:
            user_id: User identifier.

        Returns:
            Number of messages in queue.
        """
        queue_key = self._queue_key(user_id)
        length: int = await self._redis.llen(queue_key)
        return length

    async def is_processing(self, user_id: str) -> bool:
        """Check if a user currently has a message being processed.

        Args:
            user_id: User identifier.

        Returns:
            True if processing, False otherwise.
        """
        processing_key = self._processing_key(user_id)
        exists: int = await self._redis.exists(processing_key)
        return bool(exists)

    async def get_current_message_id(self, user_id: str) -> str | None:
        """Get the ID of the message currently being processed.

        Args:
            user_id: User identifier.

        Returns:
            Message ID if processing, None otherwise.
        """
        processing_key = self._processing_key(user_id)
        message_id: str | None = await self._redis.get(processing_key)
        return message_id

    async def get_active_queue_count(self) -> int:
        """Get the number of users with non-empty queues.

        Uses SCAN to handle large key spaces efficiently.

        Returns:
            Number of active queues.
        """
        count = 0
        cursor = 0

        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor,
                match="user:*:queue",
                count=100,
            )
            for key in keys:
                length: int = await self._redis.llen(key)
                if length > 0:
                    count += 1

            if cursor == 0:
                break

        return count

    async def get_processing_count(self) -> int:
        """Get the number of messages currently being processed.

        Uses SCAN to handle large key spaces efficiently.

        Returns:
            Number of messages in processing state.
        """
        count = 0
        cursor = 0

        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor,
                match="user:*:processing",
                count=100,
            )
            count += len(keys)

            if cursor == 0:
                break

        return count

    async def ping(self) -> bool:
        """Check Redis connectivity.

        Returns:
            True if Redis is reachable, False otherwise.
        """
        try:
            result: bool = await self._redis.ping()
            return result
        except Exception:
            return False
