"""Tests for WorkerClient."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bt_servant_message_broker.services.worker_client import (
    WorkerClient,
    WorkerError,
    WorkerResponse,
    WorkerTimeoutError,
)


class TestWorkerResponse:
    """Tests for WorkerResponse model."""

    def test_response_with_all_fields(self) -> None:
        """Test creating response with all fields."""
        response = WorkerResponse(
            responses=["Hello!"],
            response_language="en",
            voice_audio_base64="base64data",
        )
        assert response.responses == ["Hello!"]
        assert response.response_language == "en"
        assert response.voice_audio_base64 == "base64data"

    def test_response_without_voice_audio(self) -> None:
        """Test creating response without optional voice_audio."""
        response = WorkerResponse(
            responses=["Hello!"],
            response_language="en",
        )
        assert response.voice_audio_base64 is None


class TestWorkerError:
    """Tests for WorkerError exceptions."""

    def test_worker_error(self) -> None:
        """Test WorkerError exception."""
        error = WorkerError(500, "Internal error")
        assert error.status_code == 500
        assert error.detail == "Internal error"
        assert "500" in str(error)

    def test_worker_timeout_error(self) -> None:
        """Test WorkerTimeoutError exception."""
        error = WorkerTimeoutError(30.0)
        assert error.status_code == 504
        assert error.timeout_seconds == 30.0
        assert "30.0s" in error.detail


class TestWorkerClient:
    """Tests for WorkerClient."""

    @pytest.fixture
    def client(self) -> WorkerClient:
        """Create a WorkerClient instance."""
        return WorkerClient(
            base_url="http://worker.test",
            api_key="test-key",
            timeout=10.0,
        )

    def test_init(self, client: WorkerClient) -> None:
        """Test client initialization."""
        assert client._base_url == "http://worker.test"
        assert client._api_key == "test-key"
        assert client._timeout == 10.0
        assert client._client is None

    def test_init_strips_trailing_slash(self) -> None:
        """Test that trailing slash is stripped from base_url."""
        client = WorkerClient("http://worker.test/", "key")
        assert client._base_url == "http://worker.test"

    @pytest.mark.asyncio
    async def test_send_message_success(self, client: WorkerClient) -> None:
        """Test successful message sending."""
        mock_response = httpx.Response(
            200,
            json={
                "responses": ["Hello!"],
                "response_language": "en",
                "voice_audio_base64": None,
            },
        )

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            response = await client.send_message(
                {
                    "user_id": "user123",
                    "org_id": "org456",
                    "message": "Hello",
                    "message_type": "text",
                    "client_id": "web",
                }
            )

            assert response.responses == ["Hello!"]
            assert response.response_language == "en"

            # Verify the payload was transformed correctly
            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["org"] == "org456"  # org_id -> org
            assert payload["user_id"] == "user123"

    @pytest.mark.asyncio
    async def test_send_message_with_audio(self, client: WorkerClient) -> None:
        """Test message sending with audio data."""
        mock_response = httpx.Response(
            200,
            json={
                "responses": ["I heard you!"],
                "response_language": "en",
            },
        )

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            await client.send_message(
                {
                    "user_id": "user123",
                    "org_id": "org456",
                    "message": "",
                    "message_type": "audio",
                    "client_id": "web",
                    "audio_base64": "base64data",
                    "audio_format": "ogg",
                }
            )

            payload = mock_post.call_args.kwargs["json"]
            assert payload["audio_base64"] == "base64data"
            assert payload["audio_format"] == "ogg"

    @pytest.mark.asyncio
    async def test_send_message_timeout(self, client: WorkerClient) -> None:
        """Test timeout handling."""
        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.TimeoutException("Request timed out")

            with pytest.raises(WorkerTimeoutError) as exc_info:
                await client.send_message({"user_id": "test"})

            assert exc_info.value.timeout_seconds == 10.0

    @pytest.mark.asyncio
    async def test_send_message_connect_error(self, client: WorkerClient) -> None:
        """Test connection error handling."""
        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.ConnectError("Connection refused")

            with pytest.raises(WorkerError) as exc_info:
                await client.send_message({"user_id": "test"})

            assert exc_info.value.status_code == 503
            assert "unreachable" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_send_message_4xx_error(self, client: WorkerClient) -> None:
        """Test 4xx error handling (passed through)."""
        mock_response = httpx.Response(400, text="Bad request")

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with pytest.raises(WorkerError) as exc_info:
                await client.send_message({"user_id": "test"})

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_send_message_5xx_error(self, client: WorkerClient) -> None:
        """Test 5xx error handling (wrapped as 502)."""
        mock_response = httpx.Response(500, text="Internal server error")

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with pytest.raises(WorkerError) as exc_info:
                await client.send_message({"user_id": "test"})

            assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_send_message_invalid_json_response(self, client: WorkerClient) -> None:
        """Test handling of invalid JSON response."""
        mock_response = httpx.Response(200, text="not json")

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with pytest.raises(WorkerError) as exc_info:
                await client.send_message({"user_id": "test"})

            assert exc_info.value.status_code == 502
            assert "invalid" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_health_check_success(self, client: WorkerClient) -> None:
        """Test successful health check."""
        mock_response = httpx.Response(200)

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await client.health_check()

            assert result is True
            mock_get.assert_called_once_with("http://worker.test/health")

    @pytest.mark.asyncio
    async def test_health_check_failure(self, client: WorkerClient) -> None:
        """Test health check with non-200 response."""
        mock_response = httpx.Response(503)

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await client.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_timeout(self, client: WorkerClient) -> None:
        """Test health check with timeout."""
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.TimeoutException("Timeout")

            result = await client.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_connect_error(self, client: WorkerClient) -> None:
        """Test health check with connection error."""
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.ConnectError("Connection refused")

            result = await client.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_close(self, client: WorkerClient) -> None:
        """Test client closure."""
        # Create a mock httpx client
        mock_httpx_client = AsyncMock(spec=httpx.AsyncClient)
        client._client = mock_httpx_client

        await client.close()

        mock_httpx_client.aclose.assert_called_once()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_when_not_initialized(self, client: WorkerClient) -> None:
        """Test closing a client that was never initialized."""
        # Should not raise an error
        await client.close()
        assert client._client is None
