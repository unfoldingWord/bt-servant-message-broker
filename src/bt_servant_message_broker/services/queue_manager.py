"""Redis queue operations for per-user message ordering."""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Lua script for atomic dequeue operation
# Checks processing flag, pops message, sets processing flag atomically
# Returns: message entry JSON string, or nil if queue empty or already processing
DEQUEUE_SCRIPT = """
local queue_key = KEYS[1]
local processing_key = KEYS[2]
local ttl = ARGV[1]

-- Check if already processing (strict FIFO: one message at a time per user)
if redis.call('EXISTS', processing_key) == 1 then
    return nil
end

-- Pop the next message
local entry = redis.call('LPOP', queue_key)
if not entry then
    return nil
end

-- Parse to get message ID for processing flag
local parsed = cjson.decode(entry)
local message_id = parsed['id']

-- Set processing flag atomically
redis.call('SETEX', processing_key, ttl, message_id)

return entry
"""

# Lua script for atomic mark_complete with compare-and-delete
# Only clears processing flag if current message matches provided message_id
# Returns: 1 if cleared, 0 if not (stale worker or already cleared)
MARK_COMPLETE_SCRIPT = """
local processing_key = KEYS[1]
local message_key = KEYS[2]
local expected_message_id = ARGV[1]

-- Compare-and-delete: only clear if message ID matches
local current_id = redis.call('GET', processing_key)
if current_id == expected_message_id then
    redis.call('DEL', processing_key)
    redis.call('DEL', message_key)
    return 1
end

return 0
"""


class QueueManager:
    """Manages per-user message queues in Redis.

    Ensures FIFO ordering per user while allowing parallel processing across users.
    Uses Lua scripts for atomic operations to prevent race conditions.

    Redis Schema:
        user:{user_id}:queue - LIST for FIFO message queue
        user:{user_id}:processing - STRING with TTL for current processing message
        message:{msg_id} - HASH for message metadata
    """

    DEFAULT_PROCESSING_TTL = 300  # 5 minutes default

    def __init__(self, redis_client: Any, processing_ttl: int | None = None) -> None:
        """Initialize the queue manager.

        Args:
            redis_client: Async Redis client instance.
            processing_ttl: TTL in seconds for the processing lock. Should be longer
                than the worker timeout to prevent lock expiry during processing.
                Defaults to 300 seconds (5 minutes).
        """
        self._redis: Any = redis_client
        self._processing_ttl = processing_ttl or self.DEFAULT_PROCESSING_TTL
        self._dequeue_script: Any = None
        self._mark_complete_script: Any = None

    async def _get_dequeue_script(self) -> Any:
        """Get or register the dequeue Lua script."""
        if self._dequeue_script is None:
            self._dequeue_script = self._redis.register_script(DEQUEUE_SCRIPT)
        return self._dequeue_script

    async def _get_mark_complete_script(self) -> Any:
        """Get or register the mark_complete Lua script."""
        if self._mark_complete_script is None:
            self._mark_complete_script = self._redis.register_script(MARK_COMPLETE_SCRIPT)
        return self._mark_complete_script

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

        # Extract metadata fields from message_data per PRD schema
        queued_at = datetime.now(timezone.utc).isoformat()
        metadata: dict[str, str] = {
            "user_id": user_id,
            "queued_at": queued_at,
        }

        # Parse message_data to extract client_id and callback_url if present
        try:
            parsed_data = json.loads(message_data)
            if "client_id" in parsed_data:
                metadata["client_id"] = str(parsed_data["client_id"])
            if "callback_url" in parsed_data and parsed_data["callback_url"]:
                metadata["callback_url"] = str(parsed_data["callback_url"])
        except (json.JSONDecodeError, TypeError):
            pass  # If message_data isn't valid JSON, skip optional fields

        # Store message metadata for debugging/monitoring
        await self._redis.hset(message_key, mapping=metadata)

        logger.info(
            "Enqueued message",
            extra={
                "user_id": user_id,
                "message_id": message_id,
                "queue_position": position,
                "client_id": metadata.get("client_id"),
            },
        )

        return position

    async def dequeue(self, user_id: str) -> tuple[str, str] | None:
        """Remove and return the next message from the user's queue.

        Uses atomic Lua script to prevent race conditions:
        - Checks if user already has a message being processed (strict FIFO)
        - Pops message and sets processing flag atomically

        Args:
            user_id: User identifier.

        Returns:
            Tuple of (message_id, message_data) or None if queue is empty
            or user already has a message being processed.
        """
        queue_key = self._queue_key(user_id)
        processing_key = self._processing_key(user_id)

        # Execute atomic dequeue script
        script = await self._get_dequeue_script()
        entry: str | None = await script(
            keys=[queue_key, processing_key],
            args=[self._processing_ttl],
        )

        if entry is None:
            logger.debug(
                "Dequeue returned None (user busy or queue empty)",
                extra={"user_id": user_id},
            )
            return None

        # Parse the queue entry
        parsed = json.loads(entry)
        message_id: str = parsed["id"]
        message_data: str = parsed["data"]

        # Update message metadata with processing start time
        message_key = self._message_key(message_id)
        started_at = datetime.now(timezone.utc).isoformat()
        await self._redis.hset(message_key, "started_at", started_at)

        # Get remaining queue length for logging
        remaining = await self.get_queue_length(user_id)

        logger.info(
            "Dequeued message for processing",
            extra={
                "user_id": user_id,
                "message_id": message_id,
                "remaining_in_queue": remaining,
            },
        )

        return (message_id, message_data)

    async def mark_complete(self, user_id: str, message_id: str) -> bool:
        """Mark a message as complete and clear the processing flag.

        Uses atomic compare-and-delete to prevent stale workers from
        clearing the processing flag for a different message.

        Args:
            user_id: User identifier.
            message_id: Message identifier.

        Returns:
            True if the processing flag was cleared, False if the message_id
            didn't match (stale worker) or was already cleared.
        """
        processing_key = self._processing_key(user_id)
        message_key = self._message_key(message_id)

        # Execute atomic compare-and-delete script
        script = await self._get_mark_complete_script()
        result: int = await script(
            keys=[processing_key, message_key],
            args=[message_id],
        )

        success = result == 1
        if success:
            logger.info(
                "Marked message complete",
                extra={"user_id": user_id, "message_id": message_id},
            )
        else:
            logger.warning(
                "Failed to mark message complete (stale or already cleared)",
                extra={"user_id": user_id, "message_id": message_id},
            )

        return success

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
