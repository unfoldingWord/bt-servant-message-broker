"""API route definitions for the message broker."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException

from bt_servant_message_broker.api.dependencies import (
    RequireApiKey,
    RequireMessageProcessor,
    RequireQueueManager,
    RequireWorkerClient,
)
from bt_servant_message_broker.models import (
    HealthResponse,
    MessageRequest,
    MessageResponse,
    QueueStatusResponse,
)
from bt_servant_message_broker.services.queue_manager import QueueManager

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

    # Trigger background processing (response delivered via callback_url)
    if message_processor:
        message_processor.trigger_processing(request.user_id)

    logger.info(
        "Message queued for background processing",
        extra={
            "user_id": request.user_id,
            "message_id": message_id,
            "queue_position": position,
            "has_callback_url": request.callback_url is not None,
        },
    )
    return MessageResponse(
        status="queued",
        message_id=message_id,
        queue_position=position,
    )


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
