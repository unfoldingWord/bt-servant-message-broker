"""Pytest fixtures and configuration."""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from bt_servant_message_broker.main import app


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
