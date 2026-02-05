"""SSE stream proxying from bt-servant-worker to clients."""

from collections.abc import AsyncIterator


class StreamProxy:
    """Proxies SSE streams from bt-servant-worker to clients.

    Maintains message ordering while streaming responses.
    """

    def __init__(self, worker_base_url: str, api_key: str) -> None:
        """Initialize the stream proxy.

        Args:
            worker_base_url: Base URL of the bt-servant-worker service.
            api_key: API key for authenticating with the worker.
        """
        self._worker_base_url = worker_base_url
        self._api_key = api_key

    async def proxy_stream(self, user_id: str, org_id: str, message_id: str) -> AsyncIterator[str]:
        """Proxy an SSE stream from the worker.

        Args:
            user_id: User identifier.
            org_id: Organization identifier.
            message_id: Message being streamed.

        Yields:
            SSE event strings.
        """
        # TODO: Implement in Phase 4
        raise NotImplementedError
        yield ""  # noqa: B901
