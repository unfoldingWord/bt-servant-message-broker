"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from redis.asyncio import Redis

from bt_servant_message_broker.api.routes import router
from bt_servant_message_broker.config import get_settings
from bt_servant_message_broker.services.queue_manager import QueueManager
from bt_servant_message_broker.utils.logging import get_logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler for startup/shutdown."""
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger(__name__)
    logger.info("Starting bt-servant-message-broker")

    # Initialize Redis connection
    redis_client: Any = None
    queue_manager: QueueManager | None = None

    try:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        await redis_client.ping()
        queue_manager = QueueManager(redis_client)
        logger.info("Redis connected successfully")
    except Exception as e:
        logger.warning("Redis connection failed: %s", e)

    # Store in app.state for dependency access
    app.state.redis = redis_client
    app.state.queue_manager = queue_manager

    yield

    # Cleanup
    if redis_client:
        await redis_client.close()
    logger.info("Shutting down bt-servant-message-broker")


app = FastAPI(
    title="BT Servant Message Broker",
    description="Centralized message coordination service with per-user queueing",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "bt_servant_message_broker.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
