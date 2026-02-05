"""HTTP client for communicating with bt-servant-worker."""

from typing import Any


class WorkerClient:
    """Client for making requests to bt-servant-worker.

    Handles request/response flow and error handling for worker communication.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        """Initialize the worker client.

        Args:
            base_url: Base URL of the bt-servant-worker service.
            api_key: API key for authenticating with the worker.
        """
        self._base_url = base_url
        self._api_key = api_key
        # TODO: Initialize httpx client in Phase 3

    async def send_message(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Send a message to the worker for processing.

        Args:
            message_data: Message payload to send.

        Returns:
            Response from the worker.
        """
        # TODO: Implement in Phase 3
        raise NotImplementedError

    async def health_check(self) -> bool:
        """Check if the worker is healthy.

        Returns:
            True if worker is healthy, False otherwise.
        """
        # TODO: Implement in Phase 3
        raise NotImplementedError
