"""Tests for logging module."""

import logging

from bt_servant_message_broker.utils.logging import get_logger, setup_logging


class TestLogging:
    """Tests for logging configuration."""

    def test_setup_logging_default_level(self) -> None:
        """Test setup_logging with default level."""
        setup_logging()
        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO

    def test_setup_logging_custom_level(self) -> None:
        """Test setup_logging with custom level."""
        setup_logging("DEBUG")
        root_logger = logging.getLogger()
        assert root_logger.level == logging.DEBUG

    def test_setup_logging_case_insensitive(self) -> None:
        """Test setup_logging handles case insensitive levels."""
        setup_logging("warning")
        root_logger = logging.getLogger()
        assert root_logger.level == logging.WARNING

    def test_get_logger_returns_logger(self) -> None:
        """Test get_logger returns a logger instance."""
        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_get_logger_same_name_returns_same_logger(self) -> None:
        """Test get_logger with same name returns same instance."""
        logger1 = get_logger("test.same")
        logger2 = get_logger("test.same")
        assert logger1 is logger2
