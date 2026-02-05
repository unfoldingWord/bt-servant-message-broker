"""HTTP client for communicating with bt-servant-worker."""

from typing import Any

import httpx
from pydantic import BaseModel


class WorkerResponse(BaseModel):
    """Response from bt-servant-worker /api/v1/chat endpoint."""

    responses: list[str]
    response_language: str
    voice_audio_base64: str | None = None


class WorkerError(Exception):
    """Error from worker communication."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Worker error {status_code}: {detail}")


class WorkerTimeoutError(WorkerError):
    """Worker request timed out."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(504, f"Worker request timed out after {timeout_seconds}s")


class WorkerClient:
    """Client for making requests to bt-servant-worker.

    Handles request/response flow and error handling for worker communication.
    """

    DEFAULT_TIMEOUT = 60.0

    def __init__(self, base_url: str, api_key: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Initialize the worker client.

        Args:
            base_url: Base URL of the bt-servant-worker service.
            api_key: API key for authenticating with the worker.
            timeout: Request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx async client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def send_message(self, message_data: dict[str, Any]) -> WorkerResponse:
        """Send a message to the worker for processing.

        Args:
            message_data: Message payload to send. Should include user_id, org_id,
                message, message_type, and client_id.

        Returns:
            WorkerResponse with responses, response_language, and optional voice_audio_base64.

        Raises:
            WorkerTimeoutError: If the request times out.
            WorkerError: For other HTTP errors.
        """
        client = await self._get_client()

        # Map org_id -> org for worker API
        payload = {
            "client_id": message_data.get("client_id"),
            "user_id": message_data.get("user_id"),
            "message": message_data.get("message"),
            "message_type": message_data.get("message_type", "text"),
            "org": message_data.get("org_id"),
        }

        # Include optional fields if present
        if message_data.get("audio_base64"):
            payload["audio_base64"] = message_data["audio_base64"]
        if message_data.get("audio_format"):
            payload["audio_format"] = message_data["audio_format"]

        try:
            response = await client.post(
                f"{self._base_url}/api/v1/chat",
                json=payload,
            )
        except httpx.TimeoutException as e:
            raise WorkerTimeoutError(self._timeout) from e
        except httpx.ConnectError as e:
            raise WorkerError(503, f"Worker unreachable: {e}") from e

        if response.status_code >= 400:
            # Pass through 4xx errors, wrap 5xx as 502
            if response.status_code >= 500:
                raise WorkerError(502, f"Worker error: {response.text}")
            raise WorkerError(response.status_code, response.text)

        try:
            data = response.json()
            return WorkerResponse(**data)
        except Exception as e:
            raise WorkerError(502, f"Invalid worker response: {e}") from e

    async def health_check(self) -> bool:
        """Check if the worker is healthy.

        Returns:
            True if worker is healthy, False otherwise.
        """
        client = await self._get_client()

        try:
            response = await client.get(f"{self._base_url}/health")
            return response.status_code == 200
        except (httpx.TimeoutException, httpx.ConnectError):
            return False

    async def close(self) -> None:
        """Close the httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
