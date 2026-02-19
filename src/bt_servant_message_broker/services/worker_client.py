"""HTTP client for communicating with bt-servant-worker."""

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


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

    @staticmethod
    def _build_payload(message_data: dict[str, Any]) -> dict[str, Any]:
        """Build the worker API payload from message data.

        Maps org_id -> org and includes optional audio fields.
        """
        payload: dict[str, Any] = {
            "client_id": message_data.get("client_id"),
            "user_id": message_data.get("user_id"),
            "message": message_data.get("message"),
            "message_type": message_data.get("message_type", "text"),
            "org": message_data.get("org_id"),
        }
        if message_data.get("audio_base64"):
            payload["audio_base64"] = message_data["audio_base64"]
        if message_data.get("audio_format"):
            payload["audio_format"] = message_data["audio_format"]
        return payload

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
        payload = self._build_payload(message_data)

        user_id = payload.get("user_id")
        org = payload.get("org")
        message_type = payload.get("message_type")

        logger.info(
            "Sending request to worker",
            extra={
                "user_id": user_id,
                "org": org,
                "message_type": message_type,
                "worker_url": self._base_url,
            },
        )

        start_time = time.monotonic()

        try:
            response = await client.post(
                f"{self._base_url}/api/v1/chat",
                json=payload,
            )
        except httpx.TimeoutException as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Worker request timed out",
                extra={
                    "user_id": user_id,
                    "timeout_seconds": self._timeout,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise WorkerTimeoutError(self._timeout) from e
        except httpx.ConnectError as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Worker unreachable",
                extra={
                    "user_id": user_id,
                    "worker_url": self._base_url,
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise WorkerError(503, f"Worker unreachable: {e}") from e

        duration_ms = (time.monotonic() - start_time) * 1000

        if response.status_code >= 400:
            logger.error(
                "Worker returned error",
                extra={
                    "user_id": user_id,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            # Pass through 4xx errors, wrap 5xx as 502
            if response.status_code >= 500:
                raise WorkerError(502, f"Worker error: {response.text}")
            raise WorkerError(response.status_code, response.text)

        try:
            data = response.json()
            worker_response = WorkerResponse(**data)
            logger.info(
                "Worker request successful",
                extra={
                    "user_id": user_id,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                    "response_language": worker_response.response_language,
                    "response_count": len(worker_response.responses),
                },
            )
            return worker_response
        except Exception as e:
            logger.error(
                "Invalid worker response",
                extra={
                    "user_id": user_id,
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise WorkerError(502, f"Invalid worker response: {e}") from e

    async def stream_message(self, message_data: dict[str, Any]) -> AsyncIterator[str]:
        """Stream a message response from the worker via SSE.

        Opens a streaming POST to the worker and yields raw response lines.

        Args:
            message_data: Message payload to send (same format as send_message).

        Yields:
            Individual SSE lines from the worker response.

        Raises:
            WorkerTimeoutError: If the request times out.
            WorkerError: For HTTP errors or connection failures.
        """
        client = await self._get_client()
        payload = self._build_payload(message_data)
        user_id = payload.get("user_id")

        logger.info(
            "Opening streaming connection to worker",
            extra={"user_id": user_id, "worker_url": self._base_url},
        )

        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/v1/chat",
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    text = body.decode("utf-8", errors="replace")
                    if response.status_code >= 500:
                        raise WorkerError(502, f"Worker error: {text}")
                    raise WorkerError(response.status_code, text)
                async for line in response.aiter_lines():
                    yield line
        except httpx.TimeoutException as e:
            raise WorkerTimeoutError(self._timeout) from e
        except httpx.ConnectError as e:
            raise WorkerError(503, f"Worker unreachable: {e}") from e

        logger.info(
            "Worker stream completed",
            extra={"user_id": user_id},
        )

    async def health_check(self) -> bool:
        """Check if the worker is healthy.

        Returns:
            True if worker is healthy, False otherwise.
        """
        client = await self._get_client()

        try:
            response = await client.get(f"{self._base_url}/health")
            is_healthy = response.status_code == 200
            logger.debug(
                "Worker health check",
                extra={
                    "worker_url": self._base_url,
                    "status_code": response.status_code,
                    "is_healthy": is_healthy,
                },
            )
            return is_healthy
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning(
                "Worker health check failed",
                extra={"worker_url": self._base_url, "error": str(e)},
            )
            return False

    async def close(self) -> None:
        """Close the httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
