"""Tests for configuration module."""

from bt_servant_message_broker.config import Settings, get_settings


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        settings = Settings()
        assert settings.redis_url == "redis://localhost:6379"
        assert settings.worker_base_url == "http://localhost:8787"
        assert settings.worker_api_key == ""
        assert settings.broker_api_key == ""
        assert settings.log_level == "INFO"
        assert settings.host == "0.0.0.0"
        assert settings.port == 8000

    def test_get_settings(self) -> None:
        """Test get_settings returns Settings instance."""
        settings = get_settings()
        assert isinstance(settings, Settings)
