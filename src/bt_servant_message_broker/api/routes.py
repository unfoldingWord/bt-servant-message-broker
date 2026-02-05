"""API route definitions for the message broker."""

from fastapi import APIRouter, HTTPException

from bt_servant_message_broker.api.dependencies import (
    RequireApiKey,
    RequireQueueManager,
)
from bt_servant_message_broker.models import (
    HealthResponse,
    MessageRequest,
    QueuedResponse,
    QueueStatusResponse,
)
from bt_servant_message_broker.services.queue_manager import QueueManager

router = APIRouter()


@router.post("/api/v1/message", response_model=QueuedResponse)
async def submit_message(
    request: MessageRequest,
    _api_key: RequireApiKey,
    queue_manager: RequireQueueManager,
) -> QueuedResponse:
    """Submit a message for processing.

    The message is queued and processed in FIFO order per user.
    """
    if queue_manager is None:
        raise HTTPException(status_code=503, detail="Queue service unavailable")

    message_id = QueueManager.generate_message_id()
    message_data = request.model_dump_json()
    position = await queue_manager.enqueue(request.user_id, message_id, message_data)

    return QueuedResponse(
        status="queued",
        queue_position=position,
        message_id=message_id,
    )


@router.get("/api/v1/queue/{user_id}", response_model=QueueStatusResponse)
async def get_queue_status(
    user_id: str,
    _api_key: RequireApiKey,
    queue_manager: RequireQueueManager,
) -> QueueStatusResponse:
    """Get the queue status for a user."""
    if queue_manager is None:
        raise HTTPException(status_code=503, detail="Queue service unavailable")

    return QueueStatusResponse(
        user_id=user_id,
        queue_length=await queue_manager.get_queue_length(user_id),
        is_processing=await queue_manager.is_processing(user_id),
        current_message_id=await queue_manager.get_current_message_id(user_id),
    )


@router.get("/health", response_model=HealthResponse)
async def health_check(queue_manager: RequireQueueManager) -> HealthResponse:
    """Health check endpoint with queue statistics."""
    if queue_manager is None:
        return HealthResponse(
            status="degraded",
            redis_connected=False,
            active_queues=0,
            messages_processing=0,
        )

    try:
        redis_ok = await queue_manager.ping()
        active = await queue_manager.get_active_queue_count()
        processing = await queue_manager.get_processing_count()

        return HealthResponse(
            status="healthy" if redis_ok else "degraded",
            redis_connected=redis_ok,
            active_queues=active,
            messages_processing=processing,
        )
    except Exception:
        return HealthResponse(
            status="unhealthy",
            redis_connected=False,
            active_queues=0,
            messages_processing=0,
        )
