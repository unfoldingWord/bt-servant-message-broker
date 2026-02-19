"""Pytest fixtures and configuration."""

import json
import os
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from bt_servant_message_broker.main import app
from bt_servant_message_broker.services.message_processor import MessageProcessor
from bt_servant_message_broker.services.queue_manager import QueueManager
from bt_servant_message_broker.services.worker_client import WorkerClient, WorkerResponse


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
    """Mock Redis client for unit tests.

    Includes support for Lua script registration used by QueueManager
    for atomic operations.
    """
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

    # Mock for Lua script registration
    # The script object is callable and returns whatever we configure
    def create_mock_script(script_content: str) -> AsyncMock:
        mock_script = AsyncMock()
        # Default return value is None (empty queue / already processing)
        mock_script.return_value = None
        return mock_script

    redis.register_script = MagicMock(side_effect=create_mock_script)

    return redis


@pytest.fixture
def queue_manager(mock_redis: AsyncMock) -> QueueManager:
    """QueueManager with mocked Redis."""
    return QueueManager(mock_redis)


@pytest.fixture
def mock_dequeue_script(mock_redis: AsyncMock) -> AsyncMock:
    """Get the mock dequeue script for configuring test behavior.

    Use this fixture to configure what the dequeue Lua script returns.

    Example:
        mock_dequeue_script.return_value = json.dumps({"id": "msg1", "data": "{}"})
    """
    # Create a fresh script mock that we can configure
    script = AsyncMock()
    script.return_value = None

    # Make register_script return our configurable mock for dequeue
    original_side_effect = mock_redis.register_script.side_effect

    def custom_register(script_content: str) -> AsyncMock:
        if "LPOP" in script_content:
            return script
        # For other scripts, use original behavior
        return original_side_effect(script_content)

    mock_redis.register_script = MagicMock(side_effect=custom_register)
    return script


@pytest.fixture
def mock_mark_complete_script(mock_redis: AsyncMock) -> AsyncMock:
    """Get the mock mark_complete script for configuring test behavior.

    Use this fixture to configure what the mark_complete Lua script returns.

    Example:
        mock_mark_complete_script.return_value = 1  # Success
        mock_mark_complete_script.return_value = 0  # Stale worker
    """
    script = AsyncMock()
    script.return_value = 1  # Default to success

    original_side_effect = mock_redis.register_script.side_effect

    def custom_register(script_content: str) -> AsyncMock:
        if "Compare-and-delete" in script_content or "expected_message_id" in script_content:
            return script
        return original_side_effect(script_content)

    mock_redis.register_script = MagicMock(side_effect=custom_register)
    return script


def make_queue_entry(message_id: str, data: str) -> str:
    """Helper to create a queue entry JSON string."""
    return json.dumps({"id": message_id, "data": data})


@pytest.fixture
def mock_worker_client() -> AsyncMock:
    """Create a mock WorkerClient for testing.

    Returns a mock that simulates successful worker responses.
    """
    mock = AsyncMock(spec=WorkerClient)
    mock.send_message = AsyncMock(
        return_value=WorkerResponse(
            responses=["Hello!"],
            response_language="en",
            voice_audio_base64=None,
        )
    )
    mock.health_check = AsyncMock(return_value=True)
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def mock_message_processor(
    queue_manager: QueueManager, mock_worker_client: AsyncMock
) -> MessageProcessor:
    """Create a MessageProcessor with mocked worker client."""
    return MessageProcessor(queue_manager, mock_worker_client)
