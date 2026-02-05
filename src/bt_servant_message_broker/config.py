"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Redis connection
    redis_url: str = "redis://localhost:6379"

    # Worker connection
    worker_base_url: str = "http://localhost:8787"
    worker_api_key: str = ""

    # Broker auth
    broker_api_key: str = ""

    # Logging
    log_level: str = "INFO"

    # Server
    host: str = "0.0.0.0"  # noqa: S104  # nosec B104 - intentional for containers
    port: int = 8000


def get_settings() -> Settings:
    """Get application settings (cached)."""
    return Settings()
