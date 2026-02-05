"""Tests for API dependencies."""

import pytest
from fastapi import HTTPException

from bt_servant_message_broker.api.dependencies import verify_api_key
from bt_servant_message_broker.config import Settings


class TestVerifyApiKey:
    """Tests for verify_api_key function."""

    def test_auth_disabled_when_no_key_configured(self) -> None:
        """Test that auth is disabled when broker_api_key is empty."""
        settings = Settings(broker_api_key="")
        result = verify_api_key(x_api_key=None, settings=settings)
        assert result == ""

    def test_auth_disabled_accepts_any_key(self) -> None:
        """Test that when auth is disabled, any key is accepted."""
        settings = Settings(broker_api_key="")
        result = verify_api_key(x_api_key="some-key", settings=settings)
        assert result == ""

    def test_missing_key_raises_401(self) -> None:
        """Test that missing API key raises 401."""
        settings = Settings(broker_api_key="secret-key")
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(x_api_key=None, settings=settings)
        assert exc_info.value.status_code == 401
        assert "Missing X-API-Key" in exc_info.value.detail

    def test_invalid_key_raises_403(self) -> None:
        """Test that invalid API key raises 403."""
        settings = Settings(broker_api_key="secret-key")
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(x_api_key="wrong-key", settings=settings)
        assert exc_info.value.status_code == 403
        assert "Invalid API key" in exc_info.value.detail

    def test_valid_key_returns_key(self) -> None:
        """Test that valid API key returns the key."""
        settings = Settings(broker_api_key="secret-key")
        result = verify_api_key(x_api_key="secret-key", settings=settings)
        assert result == "secret-key"
