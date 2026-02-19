"""Message processing orchestration layer."""

import asyncio
import json
import logging

import httpx

from bt_servant_message_broker.services.queue_manager import QueueManager
from bt_servant_message_broker.services.worker_client import WorkerClient, WorkerResponse

logger = logging.getLogger(__name__)


class MessageProcessor:
    """Orchestrates message processing through queue and worker.

    All processing is asynchronous via background tasks:
    - Route calls trigger_processing() after enqueue
    - Background task dequeues and sends to worker
    - Response delivered to callback_url if provided
    - After completion, schedules next message processing
    """

    def __init__(self, queue_manager: QueueManager, worker_client: WorkerClient) -> None:
        """Initialize the message processor.

        Args:
            queue_manager: QueueManager for queue operations.
            worker_client: WorkerClient for worker communication.
        """
        self._queue = queue_manager
        self._worker = worker_client

    def trigger_processing(self, user_id: str) -> None:
        """Kick off background processing for a user's queue.

        Safe to call multiple times - if the user is already processing,
        the background task will find the processing lock set and exit.

        Args:
            user_id: User identifier.
        """
        self._schedule_next_processing(user_id)

    def _schedule_next_processing(self, user_id: str) -> None:
        """Schedule background task to process next message in queue.

        Uses asyncio.create_task to avoid blocking the current request.
        Each task processes one message and schedules the next if needed.
        """
        asyncio.create_task(self._process_next_message(user_id))

    async def _process_next_message(self, user_id: str) -> None:
        """Process the next message in queue and deliver via callback.

        This runs as a fire-and-forget background task. It processes one
        message, delivers the response to the callback_url if provided,
        and schedules another task for the next message.
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
                "Processing message in background",
                extra={"user_id": user_id, "message_id": message_id},
            )

            callback_url: str | None = None
            msg_user_id = user_id

            try:
                parsed = json.loads(message_data)
                msg_user_id = parsed.get("user_id", user_id)
                callback_url = parsed.get("callback_url")

                response = await self._worker.send_message(parsed)
                logger.info(
                    "Message processed successfully",
                    extra={"user_id": user_id, "message_id": message_id},
                )

                # Deliver response via callback if URL was provided
                if callback_url:
                    await self._deliver_callback(callback_url, message_id, msg_user_id, response)
                else:
                    logger.warning(
                        "No callback_url - response cannot be delivered to client",
                        extra={"user_id": user_id, "message_id": message_id},
                    )
            except Exception as e:
                logger.error(
                    "Message processing failed",
                    extra={
                        "user_id": user_id,
                        "message_id": message_id,
                        "error": str(e),
                    },
                )
                # Deliver error via callback if URL was provided
                if callback_url:
                    await self._deliver_error_callback(
                        callback_url, message_id, msg_user_id, str(e)
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

    async def _deliver_callback(
        self,
        callback_url: str,
        message_id: str,
        user_id: str,
        response: WorkerResponse,
    ) -> None:
        """POST worker response to the client's callback URL.

        Args:
            callback_url: URL to deliver the response to.
            message_id: Message ID for correlation.
            user_id: User ID for routing (e.g. WhatsApp phone number).
            response: Worker response to deliver.
        """
        payload = {
            "message_id": message_id,
            "user_id": user_id,
            "status": "completed",
            "responses": response.responses,
            "response_language": response.response_language,
            "voice_audio_base64": response.voice_audio_base64,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                result = await client.post(callback_url, json=payload)

            if result.status_code >= 400:
                logger.error(
                    "Callback delivery got error response",
                    extra={
                        "message_id": message_id,
                        "callback_url": callback_url,
                        "status_code": result.status_code,
                    },
                )
            else:
                logger.info(
                    "Callback delivered successfully",
                    extra={
                        "message_id": message_id,
                        "callback_url": callback_url,
                        "status_code": result.status_code,
                    },
                )
        except Exception as e:
            logger.error(
                "Callback delivery failed",
                extra={
                    "message_id": message_id,
                    "callback_url": callback_url,
                    "error": str(e),
                },
            )

    async def _deliver_error_callback(
        self,
        callback_url: str,
        message_id: str,
        user_id: str,
        error_detail: str,
    ) -> None:
        """POST error details to the client's callback URL.

        Args:
            callback_url: URL to deliver the error to.
            message_id: Message ID for correlation.
            user_id: User ID for routing.
            error_detail: Description of what went wrong.
        """
        payload = {
            "message_id": message_id,
            "user_id": user_id,
            "status": "error",
            "error": error_detail,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                result = await client.post(callback_url, json=payload)

            if result.status_code >= 400:
                logger.error(
                    "Error callback delivery got error response",
                    extra={
                        "message_id": message_id,
                        "callback_url": callback_url,
                        "status_code": result.status_code,
                    },
                )
            else:
                logger.info(
                    "Error callback delivered",
                    extra={
                        "message_id": message_id,
                        "callback_url": callback_url,
                    },
                )
        except Exception as e:
            logger.error(
                "Error callback delivery failed",
                extra={
                    "message_id": message_id,
                    "callback_url": callback_url,
                    "error": str(e),
                },
            )
