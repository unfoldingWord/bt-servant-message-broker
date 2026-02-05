"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from bt_servant_message_broker.api.routes import router
from bt_servant_message_broker.config import get_settings
from bt_servant_message_broker.utils.logging import get_logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler for startup/shutdown."""
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger(__name__)
    logger.info("Starting bt-servant-message-broker")

    # TODO: Initialize Redis connection in Phase 2
    # TODO: Initialize worker client in Phase 3

    yield

    # Cleanup
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
