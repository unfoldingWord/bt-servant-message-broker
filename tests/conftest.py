"""Pytest fixtures and configuration."""

import os
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from bt_servant_message_broker.main import app
from bt_servant_message_broker.services.queue_manager import QueueManager


@pytest.fixture(autouse=True)
def clean_env() -> Iterator[None]:
    """Ensure deterministic test environment by clearing auth-related env vars.

    This prevents tests from failing if a developer has BROKER_API_KEY set
    in their local environment.
    """
    # Store original values
    original_broker_key = os.environ.pop("BROKER_API_KEY", None)

    yield

    # Restore original values
    if original_broker_key is not None:
        os.environ["BROKER_API_KEY"] = original_broker_key


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis client for unit tests."""
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.rpush = AsyncMock(return_value=1)
    redis.lpop = AsyncMock(return_value=None)
    redis.llen = AsyncMock(return_value=0)
    redis.setex = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.exists = AsyncMock(return_value=0)
    redis.delete = AsyncMock()
    redis.hset = AsyncMock()
    redis.scan = AsyncMock(return_value=(0, []))
    return redis


@pytest.fixture
def queue_manager(mock_redis: AsyncMock) -> QueueManager:
    """QueueManager with mocked Redis."""
    return QueueManager(mock_redis)
