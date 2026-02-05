"""API route definitions for the message broker."""

from fastapi import APIRouter

from bt_servant_message_broker.api.dependencies import RequireApiKey
from bt_servant_message_broker.models import (
    HealthResponse,
    MessageRequest,
    QueuedResponse,
    QueueStatusResponse,
)

router = APIRouter()


@router.post("/api/v1/message", response_model=QueuedResponse)
async def submit_message(
    request: MessageRequest,
    _api_key: RequireApiKey,
) -> QueuedResponse:
    """Submit a message for processing.

    The message is queued and processed in FIFO order per user.
    """
    # TODO: Implement in Phase 2
    # 1. Generate message_id
    # 2. Enqueue message
    # 3. Return queue position
    return QueuedResponse(
        status="queued",
        queue_position=1,
        message_id="placeholder",
    )


@router.get("/api/v1/queue/{user_id}", response_model=QueueStatusResponse)
async def get_queue_status(
    user_id: str,
    _api_key: RequireApiKey,
) -> QueueStatusResponse:
    """Get the queue status for a user."""
    # TODO: Implement in Phase 2
    return QueueStatusResponse(
        user_id=user_id,
        queue_length=0,
        is_processing=False,
        current_message_id=None,
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint with queue statistics."""
    # TODO: Add Redis health check in Phase 2
    return HealthResponse(
        status="healthy",
        redis_connected=False,
        active_queues=0,
        messages_processing=0,
    )
