"""Message processing orchestration layer."""

import asyncio
import json
import logging

from bt_servant_message_broker.services.queue_manager import QueueManager
from bt_servant_message_broker.services.worker_client import WorkerClient, WorkerResponse

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Orchestrates message processing through queue and worker.

    Implements hybrid sync/async processing:
    - Attempts immediate processing only when message is first in queue
    - Returns None if user is busy or message is queued behind others
    - After completing a message, spawns background task to process next
    - Background tasks are non-blocking and don't add to request latency
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
        self, user_id: str, message_id: str, message_data: str, queue_position: int
    ) -> WorkerResponse | None:
        """Process a message if it's first in queue and user is not busy.

        Only attempts processing if queue_position is 1 (first in line).
        This prevents returning responses for the wrong message.

        Args:
            user_id: User identifier.
            message_id: Message identifier.
            message_data: JSON-serialized message data.
            queue_position: Position in queue after enqueue (1-indexed).

        Returns:
            WorkerResponse if processed successfully, None if queued.

        Raises:
            WorkerError: If worker communication fails.
            WorkerTimeoutError: If worker request times out.
        """
        # Only attempt processing if we're first in queue
        if queue_position > 1:
            logger.debug(
                "Message queued behind others",
                extra={
                    "user_id": user_id,
                    "message_id": message_id,
                    "queue_position": queue_position,
                },
            )
            return None

        # Try to dequeue (atomic check + pop)
        dequeued = await self._queue.dequeue(user_id)
        if dequeued is None:
            # User is already processing another message
            logger.debug(
                "User is busy, message stays queued",
                extra={"user_id": user_id, "message_id": message_id},
            )
            return None

        dequeued_id, dequeued_data = dequeued

        # Safety check: verify we got the expected message
        # If not, we have a race condition - process it but don't return to this client
        if dequeued_id != message_id:
            logger.error(
                "Message ID mismatch - dequeued different message than expected",
                extra={
                    "user_id": user_id,
                    "expected_message_id": message_id,
                    "dequeued_message_id": dequeued_id,
                },
            )
            # Process the dequeued message but don't return its response to this client
            try:
                parsed = json.loads(dequeued_data)
                await self._worker.send_message(parsed)
                logger.info(
                    "Processed mismatched message",
                    extra={"user_id": user_id, "message_id": dequeued_id},
                )
            except Exception as e:
                logger.error(
                    "Failed to process mismatched message",
                    extra={"user_id": user_id, "message_id": dequeued_id, "error": str(e)},
                )
            finally:
                await self._queue.mark_complete(user_id, dequeued_id)
                self._schedule_next_processing(user_id)
            # Return None so client gets "queued" status (their message is still in queue)
            return None

        # Process the message
        try:
            parsed = json.loads(dequeued_data)
            response = await self._worker.send_message(parsed)
            logger.info(
                "Processed message successfully",
                extra={"user_id": user_id, "message_id": dequeued_id},
            )
            return response
        finally:
            # Always mark complete to prevent queue stalling
            await self._queue.mark_complete(user_id, dequeued_id)
            self._schedule_next_processing(user_id)

    def _schedule_next_processing(self, user_id: str) -> None:
        """Schedule background task to process next message in queue.

        Uses asyncio.create_task to avoid blocking the current request.
        Each task processes one message and schedules the next if needed.
        """
        asyncio.create_task(self._process_next_message(user_id))

    async def _process_next_message(self, user_id: str) -> None:
        """Process the next message in queue (background task).

        This runs as a fire-and-forget background task. It processes one
        message and schedules another task for the next, avoiding recursion.
        """
        try:
            dequeued = await self._queue.dequeue(user_id)
            if dequeued is None:
                logger.debug(
                    "No more messages to process in background",
                    extra={"user_id": user_id},
                )
                return

            message_id, message_data = dequeued
            logger.info(
                "Processing queued message in background",
                extra={"user_id": user_id, "message_id": message_id},
            )

            try:
                parsed = json.loads(message_data)
                await self._worker.send_message(parsed)
                logger.info(
                    "Background message processed successfully",
                    extra={"user_id": user_id, "message_id": message_id},
                )
            except Exception as e:
                logger.error(
                    "Background message processing failed",
                    extra={"user_id": user_id, "message_id": message_id, "error": str(e)},
                )
            finally:
                await self._queue.mark_complete(user_id, message_id)
                # Schedule next message processing (non-recursive: new task)
                self._schedule_next_processing(user_id)

        except Exception as e:
            logger.error(
                "Background processing task failed",
                extra={"user_id": user_id, "error": str(e)},
            )
