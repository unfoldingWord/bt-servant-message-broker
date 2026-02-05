"""FastAPI dependencies for authentication and rate limiting."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from bt_servant_message_broker.config import Settings, get_settings
from bt_servant_message_broker.services.queue_manager import QueueManager


def verify_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> str:
    """Verify the API key in the request header.

    Args:
        x_api_key: API key from X-API-Key header.
        settings: Application settings.

    Returns:
        The verified API key.

    Raises:
        HTTPException: If API key is missing or invalid.
    """
    if not settings.broker_api_key:
        # Auth disabled if no key configured
        return ""

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )

    if x_api_key != settings.broker_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return x_api_key


def get_queue_manager(request: Request) -> QueueManager | None:
    """Get the QueueManager from app state.

    Args:
        request: FastAPI request object.

    Returns:
        QueueManager instance or None if Redis unavailable.
    """
    return getattr(request.app.state, "queue_manager", None)


# Dependency for protected routes
RequireApiKey = Annotated[str, Depends(verify_api_key)]

# Dependency for queue operations
RequireQueueManager = Annotated[QueueManager | None, Depends(get_queue_manager)]
