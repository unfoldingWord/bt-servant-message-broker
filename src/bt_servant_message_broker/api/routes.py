"""API route definitions for the message broker."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from bt_servant_message_broker.api.dependencies import (
    RequireApiKey,
    RequireMessageProcessor,
    RequireQueueManager,
    RequireStreamProxy,
    RequireWorkerClient,
)
from bt_servant_message_broker.models import (
    HealthResponse,
    MessageRequest,
    MessageResponse,
    QueueStatusResponse,
    StreamRequest,
)
from bt_servant_message_broker.services.queue_manager import QueueManager
from bt_servant_message_broker.services.stream_proxy import StreamProxy

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/v1/message", response_model=MessageResponse)
async def submit_message(
    request: MessageRequest,
    _api_key: RequireApiKey,
    queue_manager: RequireQueueManager,
    message_processor: RequireMessageProcessor,
) -> MessageResponse:
    """Submit a message for processing.

    The message is enqueued and always returns "queued" immediately.
    Background processing delivers the AI response to the client's
    callback_url via POST when complete.
    """
    logger.info(
        "Received message submission",
        extra={
            "user_id": request.user_id,
            "org_id": request.org_id,
            "client_id": request.client_id.value,
            "message_type": request.message_type.value,
        },
    )

    if queue_manager is None:
        logger.error("Queue service unavailable")
        raise HTTPException(status_code=503, detail="Queue service unavailable")

    message_id = QueueManager.generate_message_id()
    message_data = request.model_dump_json()
    position = await queue_manager.enqueue(request.user_id, message_id, message_data)

    # Only trigger background processing for first-in-queue;
    # the background drain handles subsequent messages after each completion.
    if message_processor and position == 1:
        message_processor.trigger_processing(request.user_id)

    logger.info(
        "Message queued for background processing",
        extra={
            "user_id": request.user_id,
            "message_id": message_id,
            "queue_position": position,
        },
    )
    return MessageResponse(
        status="queued",
        message_id=message_id,
        queue_position=position,
    )


_STREAM_HANDOFF_TIMEOUT = 120.0  # seconds to wait for queue handoff


@router.post("/api/v1/stream")
async def stream_message(
    request: StreamRequest,
    _api_key: RequireApiKey,
    queue_manager: RequireQueueManager,
    stream_proxy: RequireStreamProxy,
    message_processor: RequireMessageProcessor,
) -> EventSourceResponse:
    """Submit a message and stream the AI response via SSE.

    Enqueues the message, waits for its turn in the per-user FIFO queue,
    then proxies the SSE stream from the worker back to the client.
    """
    if queue_manager is None:
        logger.error("Queue service unavailable for stream request")
        raise HTTPException(status_code=503, detail="Queue service unavailable")

    if stream_proxy is None:
        logger.error("Stream proxy unavailable")
        raise HTTPException(status_code=503, detail="Stream service unavailable")

    return EventSourceResponse(
        _stream_generator(request, queue_manager, stream_proxy, message_processor),
    )


async def _stream_generator(
    request: StreamRequest,
    queue_manager: QueueManager,
    stream_proxy: StreamProxy,
    message_processor: object | None,
) -> AsyncIterator[ServerSentEvent]:
    """Async generator that drives the SSE stream lifecycle."""
    message_id = QueueManager.generate_message_id()
    message_data = request.model_dump_json()
    user_id = request.user_id
    handoff_future: asyncio.Future[dict[str, object]] | None = None

    try:
        # 1. Enqueue message
        position = await queue_manager.enqueue(user_id, message_id, message_data)
        logger.info(
            "Stream message queued",
            extra={
                "user_id": user_id,
                "message_id": message_id,
                "queue_position": position,
            },
        )
        yield ServerSentEvent(
            data=json.dumps({"message_id": message_id, "queue_position": position}),
            event="queued",
        )

        # 2. Register for handoff and trigger processing
        handoff_future = stream_proxy.register(message_id)
        if message_processor and hasattr(message_processor, "trigger_processing"):
            message_processor.trigger_processing(user_id)  # type: ignore[union-attr]

        # 3. Wait for the background processor to hand off this message
        try:
            handoff_data: dict[str, object] = await asyncio.wait_for(
                handoff_future, timeout=_STREAM_HANDOFF_TIMEOUT
            )
        except TimeoutError:
            logger.error(
                "Stream handoff timed out",
                extra={"user_id": user_id, "message_id": message_id},
            )
            yield ServerSentEvent(data="Handoff timeout", event="error")
            return

        # 4. Handoff received — now stream from worker
        yield ServerSentEvent(data="", event="processing")

        async for event in stream_proxy.stream_from_worker(handoff_data):
            yield event

        # 5. Done
        yield ServerSentEvent(data="", event="done")
        logger.info(
            "Stream completed successfully",
            extra={"user_id": user_id, "message_id": message_id},
        )

    except asyncio.CancelledError:
        logger.info(
            "Client disconnected from stream",
            extra={"user_id": user_id, "message_id": message_id},
        )
    finally:
        # Always clean up: mark complete and trigger next
        stream_proxy.unregister(message_id)
        await queue_manager.mark_complete(user_id, message_id)
        if message_processor and hasattr(message_processor, "trigger_processing"):
            message_processor.trigger_processing(user_id)  # type: ignore[union-attr]


@router.get("/api/v1/queue/{user_id}", response_model=QueueStatusResponse)
async def get_queue_status(
    user_id: str,
    _api_key: RequireApiKey,
    queue_manager: RequireQueueManager,
) -> QueueStatusResponse:
    """Get the queue status for a user."""
    logger.debug("Queue status request", extra={"user_id": user_id})

    if queue_manager is None:
        logger.error("Queue service unavailable")
        raise HTTPException(status_code=503, detail="Queue service unavailable")

    queue_length = await queue_manager.get_queue_length(user_id)
    is_processing = await queue_manager.is_processing(user_id)
    current_message_id = await queue_manager.get_current_message_id(user_id)

    logger.debug(
        "Queue status response",
        extra={
            "user_id": user_id,
            "queue_length": queue_length,
            "is_processing": is_processing,
            "current_message_id": current_message_id,
        },
    )

    return QueueStatusResponse(
        user_id=user_id,
        queue_length=queue_length,
        is_processing=is_processing,
        current_message_id=current_message_id,
    )


@router.get("/health", response_model=HealthResponse)
async def health_check(
    queue_manager: RequireQueueManager,
    worker_client: RequireWorkerClient,
) -> HealthResponse:
    """Health check endpoint with queue statistics."""
    # Check worker health
    worker_connected = False
    if worker_client:
        worker_connected = await worker_client.health_check()

    if queue_manager is None:
        return HealthResponse(
            status="degraded",
            redis_connected=False,
            active_queues=0,
            messages_processing=0,
            worker_connected=worker_connected,
        )

    try:
        redis_ok = await queue_manager.ping()
        active = await queue_manager.get_active_queue_count()
        processing = await queue_manager.get_processing_count()

        # Determine overall status
        status: Literal["healthy", "degraded", "unhealthy"]
        if redis_ok and worker_connected:
            status = "healthy"
        elif redis_ok:
            status = "degraded"  # Redis OK but worker not connected
        else:
            status = "unhealthy"

        return HealthResponse(
            status=status,
            redis_connected=redis_ok,
            active_queues=active,
            messages_processing=processing,
            worker_connected=worker_connected,
        )
    except Exception:
        return HealthResponse(
            status="unhealthy",
            redis_connected=False,
            active_queues=0,
            messages_processing=0,
            worker_connected=worker_connected,
        )
