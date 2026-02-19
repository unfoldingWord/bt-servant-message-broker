"""Tests for MessageProcessor."""

import json
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bt_servant_message_broker.services.message_processor import (
    MessageProcessor,
    _validate_callback_host,
)
from bt_servant_message_broker.services.worker_client import (
    WorkerError,
    WorkerResponse,
    WorkerTimeoutError,
)


@pytest.fixture
def mock_queue_manager() -> AsyncMock:
    """Create a mock QueueManager."""
    mock = AsyncMock()
    mock.dequeue = AsyncMock(return_value=None)
    mock.mark_complete = AsyncMock(return_value=True)
    mock.get_queue_length = AsyncMock(return_value=0)
    return mock


@pytest.fixture
def mock_worker_client() -> AsyncMock:
    """Create a mock WorkerClient."""
    mock = AsyncMock()
    mock.send_message = AsyncMock(
        return_value=WorkerResponse(
            responses=["Hello!"],
            response_language="en",
            voice_audio_base64=None,
        )
    )
    mock.health_check = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def processor(mock_queue_manager: AsyncMock, mock_worker_client: AsyncMock) -> MessageProcessor:
    """Create a MessageProcessor with mocked dependencies."""
    return MessageProcessor(mock_queue_manager, mock_worker_client)


class TestTriggerProcessing:
    """Tests for trigger_processing public API."""

    def test_trigger_processing_calls_schedule(
        self,
        processor: MessageProcessor,
    ) -> None:
        """Test that trigger_processing delegates to _schedule_next_processing."""
        with patch.object(processor, "_schedule_next_processing") as mock_schedule:
            processor.trigger_processing("user1")
            mock_schedule.assert_called_once_with("user1")

    def test_schedule_creates_asyncio_task(
        self,
        processor: MessageProcessor,
    ) -> None:
        """Test that _schedule_next_processing creates an asyncio task."""
        with (
            patch("bt_servant_message_broker.services.message_processor.asyncio") as mock_asyncio,
            patch.object(processor, "_process_next_message", MagicMock(return_value=None)),
        ):
            mock_asyncio.create_task = MagicMock()
            processor._schedule_next_processing("user1")
            mock_asyncio.create_task.assert_called_once()


class TestBackgroundProcessing:
    """Tests for background message processing."""

    @pytest.mark.asyncio
    async def test_empty_queue_returns_early(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that background processing exits when queue is empty."""
        mock_queue_manager.dequeue.return_value = None

        await processor._process_next_message("user1")

        mock_queue_manager.dequeue.assert_called_once_with("user1")
        mock_worker_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_next_message(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that background processing dequeues and processes a message."""
        message_data = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg2", message_data)

        with patch.object(processor, "_schedule_next_processing"):
            await processor._process_next_message("user1")

        mock_queue_manager.dequeue.assert_called_with("user1")
        mock_worker_client.send_message.assert_called_once()
        mock_queue_manager.mark_complete.assert_called_once_with("user1", "msg2")

    @pytest.mark.asyncio
    async def test_handles_worker_errors_gracefully(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that background processing handles worker errors without crashing."""
        message_data = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg2", message_data)
        mock_worker_client.send_message.side_effect = WorkerError(500, "Server error")

        with patch.object(processor, "_schedule_next_processing"):
            # Should not raise
            await processor._process_next_message("user1")

        mock_queue_manager.mark_complete.assert_called_once_with("user1", "msg2")

    @pytest.mark.asyncio
    async def test_schedules_next_after_completion(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that background processing schedules next message after completion."""
        message_data = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg1", message_data)

        with patch.object(processor, "_schedule_next_processing") as mock_schedule:
            await processor._process_next_message("user1")

        mock_schedule.assert_called_once_with("user1")

    @pytest.mark.asyncio
    async def test_schedules_next_even_on_error(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that next message is scheduled even when worker errors occur."""
        message_data = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg1", message_data)
        mock_worker_client.send_message.side_effect = WorkerError(500, "Server error")

        with patch.object(processor, "_schedule_next_processing") as mock_schedule:
            await processor._process_next_message("user1")

        mock_schedule.assert_called_once_with("user1")


class TestCallbackDelivery:
    """Tests for callback delivery to client."""

    @pytest.mark.asyncio
    async def test_delivers_callback_with_user_id(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that callback delivery includes user_id from message data."""
        message_data = json.dumps(
            {
                "user_id": "15551234567",
                "callback_url": "https://example.com/callback",
            }
        )
        mock_queue_manager.dequeue.return_value = ("msg2", message_data)

        with (
            patch.object(processor, "_schedule_next_processing"),
            patch.object(processor, "_deliver_callback", new_callable=AsyncMock) as mock_callback,
        ):
            await processor._process_next_message("15551234567")

            mock_callback.assert_called_once_with(
                "https://example.com/callback",
                "msg2",
                "15551234567",
                mock_worker_client.send_message.return_value,
            )

    @pytest.mark.asyncio
    async def test_no_callback_when_url_missing(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that no callback is attempted when callback_url is missing."""
        message_data = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg2", message_data)

        with (
            patch.object(processor, "_schedule_next_processing"),
            patch.object(processor, "_deliver_callback", new_callable=AsyncMock) as mock_callback,
        ):
            await processor._process_next_message("user1")

            mock_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_payload_structure(
        self,
        processor: MessageProcessor,
    ) -> None:
        """Test that callback POST payload has correct structure."""
        response = WorkerResponse(
            responses=["Hello!"],
            response_language="en",
            voice_audio_base64=None,
        )

        mock_response = AsyncMock()
        mock_response.status_code = 200

        with (
            patch(
                "bt_servant_message_broker.services.message_processor._validate_callback_host",
                new_callable=AsyncMock,
            ),
            patch(
                "bt_servant_message_broker.services.message_processor.httpx.AsyncClient"
            ) as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await processor._deliver_callback(
                "https://example.com/callback", "msg1", "15551234567", response
            )

            mock_client.post.assert_called_once_with(
                "https://example.com/callback",
                json={
                    "message_id": "msg1",
                    "user_id": "15551234567",
                    "status": "completed",
                    "responses": ["Hello!"],
                    "response_language": "en",
                    "voice_audio_base64": None,
                },
            )

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_crash(
        self,
        processor: MessageProcessor,
    ) -> None:
        """Test that callback delivery failure doesn't crash the processor."""
        response = WorkerResponse(
            responses=["Hello!"],
            response_language="en",
            voice_audio_base64=None,
        )

        with (
            patch(
                "bt_servant_message_broker.services.message_processor._validate_callback_host",
                new_callable=AsyncMock,
            ),
            patch(
                "bt_servant_message_broker.services.message_processor.httpx.AsyncClient"
            ) as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            # Should not raise
            await processor._deliver_callback(
                "https://example.com/callback", "msg1", "user1", response
            )


class TestErrorCallbackDelivery:
    """Tests for error callback delivery."""

    @pytest.mark.asyncio
    async def test_error_callback_on_worker_failure(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that error callback is delivered when worker fails."""
        message_data = json.dumps(
            {
                "user_id": "user1",
                "callback_url": "https://example.com/callback",
            }
        )
        mock_queue_manager.dequeue.return_value = ("msg1", message_data)
        mock_worker_client.send_message.side_effect = WorkerError(500, "Server error")

        with (
            patch.object(processor, "_schedule_next_processing"),
            patch.object(
                processor, "_deliver_error_callback", new_callable=AsyncMock
            ) as mock_error_cb,
        ):
            await processor._process_next_message("user1")

            mock_error_cb.assert_called_once_with(
                "https://example.com/callback",
                "msg1",
                "user1",
                "Worker error 500: Server error",
            )

    @pytest.mark.asyncio
    async def test_error_callback_on_worker_timeout(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that error callback is delivered when worker times out."""
        message_data = json.dumps(
            {
                "user_id": "user1",
                "callback_url": "https://example.com/callback",
            }
        )
        mock_queue_manager.dequeue.return_value = ("msg1", message_data)
        mock_worker_client.send_message.side_effect = WorkerTimeoutError(60.0)

        with (
            patch.object(processor, "_schedule_next_processing"),
            patch.object(
                processor, "_deliver_error_callback", new_callable=AsyncMock
            ) as mock_error_cb,
        ):
            await processor._process_next_message("user1")

            mock_error_cb.assert_called_once_with(
                "https://example.com/callback",
                "msg1",
                "user1",
                "Worker error 504: Worker request timed out after 60.0s",
            )

    @pytest.mark.asyncio
    async def test_no_error_callback_without_url(
        self,
        processor: MessageProcessor,
        mock_queue_manager: AsyncMock,
        mock_worker_client: AsyncMock,
    ) -> None:
        """Test that no error callback is attempted when callback_url is missing."""
        message_data = json.dumps({"user_id": "user1"})
        mock_queue_manager.dequeue.return_value = ("msg1", message_data)
        mock_worker_client.send_message.side_effect = WorkerError(500, "Server error")

        with (
            patch.object(processor, "_schedule_next_processing"),
            patch.object(
                processor, "_deliver_error_callback", new_callable=AsyncMock
            ) as mock_error_cb,
        ):
            await processor._process_next_message("user1")

            mock_error_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_callback_payload_structure(
        self,
        processor: MessageProcessor,
    ) -> None:
        """Test that error callback POST payload has correct structure."""
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with (
            patch(
                "bt_servant_message_broker.services.message_processor._validate_callback_host",
                new_callable=AsyncMock,
            ),
            patch(
                "bt_servant_message_broker.services.message_processor.httpx.AsyncClient"
            ) as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await processor._deliver_error_callback(
                "https://example.com/callback", "msg1", "user1", "Something went wrong"
            )

            mock_client.post.assert_called_once_with(
                "https://example.com/callback",
                json={
                    "message_id": "msg1",
                    "user_id": "user1",
                    "status": "error",
                    "error": "Something went wrong",
                },
            )

    @pytest.mark.asyncio
    async def test_error_callback_failure_does_not_crash(
        self,
        processor: MessageProcessor,
    ) -> None:
        """Test that error callback delivery failure doesn't crash the processor."""
        with (
            patch(
                "bt_servant_message_broker.services.message_processor._validate_callback_host",
                new_callable=AsyncMock,
            ),
            patch(
                "bt_servant_message_broker.services.message_processor.httpx.AsyncClient"
            ) as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            # Should not raise
            await processor._deliver_error_callback(
                "https://example.com/callback", "msg1", "user1", "Some error"
            )


class TestSsrfDnsValidation:
    """Tests for DNS-level SSRF prevention."""

    @pytest.mark.asyncio
    async def test_callback_blocked_when_hostname_resolves_to_private_ip(
        self,
        processor: MessageProcessor,
    ) -> None:
        """Test that callback is blocked when hostname resolves to private IP."""
        response = WorkerResponse(
            responses=["Hello!"],
            response_language="en",
            voice_audio_base64=None,
        )

        # Mock DNS resolution to return a private IP
        with (
            patch(
                "bt_servant_message_broker.services.message_processor._validate_callback_host",
                new_callable=AsyncMock,
                side_effect=ValueError("callback_url hostname resolves to blocked IP: 10.0.0.1"),
            ),
            patch(
                "bt_servant_message_broker.services.message_processor.httpx.AsyncClient"
            ) as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            # Should not crash, should not POST
            await processor._deliver_callback(
                "https://evil.com/callback", "msg1", "user1", response
            )

            mock_client.__aenter__.assert_not_called()

    @pytest.mark.asyncio
    async def test_validate_callback_host_blocks_private_ip(self) -> None:
        """Test that _validate_callback_host blocks hostnames resolving to private IPs."""
        # Mock getaddrinfo to return a private IP
        mock_loop = AsyncMock()
        mock_loop.getaddrinfo = AsyncMock(return_value=[(2, 1, 6, "", ("10.0.0.1", 0))])

        with patch("asyncio.get_running_loop", return_value=mock_loop):
            with pytest.raises(ValueError, match="blocked IP"):
                await _validate_callback_host("https://evil.com/callback")

    @pytest.mark.asyncio
    async def test_validate_callback_host_blocks_link_local(self) -> None:
        """Test that _validate_callback_host blocks link-local resolved IPs."""
        mock_loop = AsyncMock()
        mock_loop.getaddrinfo = AsyncMock(return_value=[(2, 1, 6, "", ("169.254.169.254", 0))])

        with patch("asyncio.get_running_loop", return_value=mock_loop):
            with pytest.raises(ValueError, match="blocked IP"):
                await _validate_callback_host("https://metadata.internal/latest")

    @pytest.mark.asyncio
    async def test_validate_callback_host_allows_public_ip(self) -> None:
        """Test that _validate_callback_host allows hostnames resolving to public IPs."""
        mock_loop = AsyncMock()
        mock_loop.getaddrinfo = AsyncMock(return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])

        with patch("asyncio.get_running_loop", return_value=mock_loop):
            # Should not raise
            await _validate_callback_host("https://example.com/callback")

    @pytest.mark.asyncio
    async def test_validate_callback_host_dns_failure_passes_through(self) -> None:
        """Test that DNS resolution failure is not treated as SSRF (let httpx handle it)."""
        mock_loop = AsyncMock()
        mock_loop.getaddrinfo = AsyncMock(side_effect=socket.gaierror("DNS failed"))

        with patch("asyncio.get_running_loop", return_value=mock_loop):
            # Should not raise - DNS failure is not SSRF
            await _validate_callback_host("https://nonexistent.example.com/callback")
