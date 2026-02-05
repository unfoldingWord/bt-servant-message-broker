"""Pytest fixtures and configuration."""

import pytest
from fastapi.testclient import TestClient

from bt_servant_message_broker.main import app


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)
