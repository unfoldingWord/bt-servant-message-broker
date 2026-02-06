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
from bt_servant_message_broker.services.worker_client import WorkerError, WorkerTimeoutError

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

    The message is queued and processed in FIFO order per user.
    If the user has no messages currently processing, the message is
    processed immediately and the response is returned synchronously.
    Otherwise, the message is queued and the response indicates queued status.
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

    # Try to process immediately if message processor is available
    if message_processor:
        try:
            response = await message_processor.process_message(
                request.user_id, message_id, message_data, position
            )
            if response:
                logger.info(
                    "Message processed immediately",
                    extra={
                        "user_id": request.user_id,
                        "message_id": message_id,
                        "status": "completed",
                    },
                )
                return MessageResponse(
                    status="completed",
                    message_id=message_id,
                    responses=response.responses,
                    response_language=response.response_language,
                    voice_audio_base64=response.voice_audio_base64,
                )
        except WorkerTimeoutError as e:
            logger.error(
                "Worker timeout during message processing",
                extra={"user_id": request.user_id, "message_id": message_id},
            )
            raise HTTPException(status_code=504, detail="Worker request timed out") from e
        except WorkerError as e:
            logger.error(
                "Worker error during message processing",
                extra={
                    "user_id": request.user_id,
                    "message_id": message_id,
                    "worker_status_code": e.status_code,
                },
            )
            # Pass through 4xx, wrap 5xx as 502
            status_code = 502 if e.status_code >= 500 else e.status_code
            raise HTTPException(status_code=status_code, detail=e.detail) from e

    # Message stays queued (user busy or no processor configured)
    logger.info(
        "Message queued (user busy or no processor)",
        extra={
            "user_id": request.user_id,
            "message_id": message_id,
            "queue_position": position,
            "status": "queued",
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
