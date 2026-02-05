"""Message processing orchestration layer."""

import json
import logging

from bt_servant_message_broker.services.queue_manager import QueueManager
from bt_servant_message_broker.services.worker_client import WorkerClient, WorkerResponse

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Orchestrates message processing through queue and worker.

    Implements hybrid sync/async processing:
    - Attempts immediate processing when user has no pending messages
    - Returns None if user is busy (message stays queued for later)
    - Recursively processes next message after completing one
    """

    def __init__(self, queue_manager: QueueManager, worker_client: WorkerClient) -> None:
        """Initialize the message processor.

        Args:
            queue_manager: QueueManager for queue operations.
            worker_client: WorkerClient for worker communication.
        """
        self._queue = queue_manager
        self._worker = worker_client

    async def process_message(
        self, user_id: str, message_id: str, message_data: str
    ) -> WorkerResponse | None:
        """Process a message if user is not currently processing.

        Attempts to dequeue and process the message immediately. If the user
        already has a message being processed, returns None (message stays queued).

        Args:
            user_id: User identifier.
            message_id: Message identifier.
            message_data: JSON-serialized message data.

        Returns:
            WorkerResponse if processed successfully, None if user is busy.

        Raises:
            WorkerError: If worker communication fails.
            WorkerTimeoutError: If worker request times out.
        """
        # Try to dequeue (atomic check + pop)
        dequeued = await self._queue.dequeue(user_id)
        if dequeued is None:
            # User is already processing another message
            logger.debug("User %s is busy, message %s stays queued", user_id, message_id)
            return None

        dequeued_id, dequeued_data = dequeued

        # Verify we got the expected message (should always match for new messages)
        if dequeued_id != message_id:
            logger.warning(
                "Dequeued message %s doesn't match expected %s for user %s",
                dequeued_id,
                message_id,
                user_id,
            )

        try:
            parsed = json.loads(dequeued_data)
            response = await self._worker.send_message(parsed)
            logger.info("Processed message %s for user %s", dequeued_id, user_id)
            return response
        finally:
            # Always mark complete to prevent queue stalling
            await self._queue.mark_complete(user_id, dequeued_id)
            # Process next message in queue if any
            await self._process_next(user_id)

    async def _process_next(self, user_id: str) -> None:
        """Process the next message in the user's queue if available.

        This is called after completing a message to drain the queue.
        Errors are logged but not raised to prevent cascading failures.

        Args:
            user_id: User identifier.
        """
        # Check if there are more messages
        queue_length = await self._queue.get_queue_length(user_id)
        if queue_length == 0:
            return

        # Try to dequeue next message
        dequeued = await self._queue.dequeue(user_id)
        if dequeued is None:
            # Another worker got it or queue is empty
            return

        dequeued_id, dequeued_data = dequeued

        try:
            parsed = json.loads(dequeued_data)
            await self._worker.send_message(parsed)
            logger.info("Processed queued message %s for user %s", dequeued_id, user_id)
        except Exception as e:
            # Log but don't raise - we don't want to fail the original request
            logger.error(
                "Failed to process queued message %s for user %s: %s",
                dequeued_id,
                user_id,
                e,
            )
        finally:
            await self._queue.mark_complete(user_id, dequeued_id)
            # Recurse to process remaining messages
            await self._process_next(user_id)
