"""SSE stream proxying from bt-servant-worker to clients."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from sse_starlette.event import ServerSentEvent

if TYPE_CHECKING:
    from bt_servant_message_broker.services.message_processor import MessageProcessor
    from bt_servant_message_broker.services.queue_manager import QueueManager

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0
_STREAM_HANDOFF_TIMEOUT = 120.0  # seconds to wait for queue handoff


@dataclass
class _PendingStream:
    """A registered stream awaiting handoff from the background processor."""

    future: asyncio.Future[dict[str, Any]]


class StreamProxy:
    """Proxies SSE streams from bt-servant-worker to web clients.

    Coordinates with MessageProcessor via a registry of asyncio.Futures
    keyed by message_id. When the background processor dequeues a message
    whose ID is registered here, it hands off the message data through the
    Future instead of calling the worker itself.
    """

    def __init__(
        self,
        worker_base_url: str,
        api_key: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._worker_base_url = worker_base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._registry: dict[str, _PendingStream] = {}
        self._registration_events: dict[str, asyncio.Event] = {}
        self._queue: QueueManager | None = None
        self._processor: MessageProcessor | None = None

    def configure(
        self,
        queue_manager: QueueManager,
        message_processor: MessageProcessor,
    ) -> None:
        """Set back-references needed by proxy_stream for cleanup.

        Called after MessageProcessor is created (avoids circular init).
        """
        self._queue = queue_manager
        self._processor = message_processor

    # -- Registry methods (coordinate with MessageProcessor) ----------------

    def register(self, message_id: str) -> asyncio.Future[dict[str, Any]]:
        """Register a message_id for SSE stream handoff.

        Returns an asyncio.Future that will be resolved by the background
        processor with the parsed message data when it dequeues this message.
        """
        loop = asyncio.get_running_loop()
        pending = _PendingStream(future=loop.create_future())
        self._registry[message_id] = pending
        logger.debug("Registered stream for message %s", message_id)
        # Notify any processor waiting for this registration
        event = self._registration_events.get(message_id)
        if event:
            event.set()
        return pending.future

    async def wait_for_registration(self, message_id: str, timeout: float) -> bool:
        """Wait for an SSE client to register a stream for the given message.

        Called by the background processor when it dequeues an SSE message
        (no callback_url) that hasn't been registered yet. This handles the
        drain-chain race where processing reaches a message before the client
        opens the SSE connection.

        Returns:
            True if registered within timeout, False if timed out.
        """
        if self.is_registered(message_id):
            return True
        event = asyncio.Event()
        self._registration_events[message_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self.is_registered(message_id)
        except TimeoutError:
            logger.warning(
                "Timed out waiting for SSE stream registration",
                extra={"message_id": message_id, "timeout": timeout},
            )
            return False
        finally:
            self._registration_events.pop(message_id, None)

    def unregister(self, message_id: str) -> None:
        """Remove a message_id from the registry without handoff."""
        pending = self._registry.pop(message_id, None)
        if pending and not pending.future.done():
            pending.future.cancel()
        logger.debug("Unregistered stream for message %s", message_id)

    def is_registered(self, message_id: str) -> bool:
        """Check if a message_id has a waiting SSE handler."""
        return message_id in self._registry

    def handoff(self, message_id: str, message_data: dict[str, Any]) -> None:
        """Resolve the Future for a registered message, handing off to SSE handler."""
        pending = self._registry.pop(message_id, None)
        if pending is None:
            logger.warning("Handoff called for unregistered message %s", message_id)
            return
        if pending.future.done():
            logger.warning("Handoff called but future already done for message %s", message_id)
            return
        pending.future.set_result(message_data)
        logger.info("Handed off message %s to SSE stream handler", message_id)

    # -- Worker streaming ---------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx async client (long timeout for streaming)."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            )
        return self._client

    def _build_worker_payload(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Map broker message fields to worker /api/v1/chat/stream payload."""
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

    async def stream_from_worker(
        self, message_data: dict[str, Any]
    ) -> AsyncIterator[ServerSentEvent]:
        """Open a streaming POST to the worker and yield parsed SSE events."""
        client = await self._get_client()
        payload = self._build_worker_payload(message_data)

        url = f"{self._worker_base_url}/api/v1/chat/stream"
        logger.info(
            "Opening SSE stream to worker",
            extra={
                "user_id": message_data.get("user_id"),
                "worker_url": self._worker_base_url,
            },
        )

        async with client.stream("POST", url, json=payload) as response:
            if response.status_code >= 400:
                body = await response.aread()
                logger.error(
                    "Worker returned error for stream request",
                    extra={
                        "status_code": response.status_code,
                        "body": body.decode(errors="replace")[:200],
                    },
                )
                yield ServerSentEvent(
                    data=f"Worker error: {response.status_code}",
                    event="error",
                )
                return

            async for event in _parse_sse_stream(response):
                yield event

    async def proxy_stream(self, user_id: str, message_id: str) -> AsyncIterator[dict[str, str]]:
        """Full SSE flow: register → wait for handoff → stream from worker → done.

        Yields dicts with ``event`` and ``data`` keys suitable for
        ``sse_starlette.EventSourceResponse``.
        """
        handoff_future = self.register(message_id)
        if self._processor and self._queue:
            has_work = await self._queue.get_queue_length(
                user_id
            ) > 0 or await self._queue.is_processing(user_id)
            if has_work:
                self._processor.trigger_processing(user_id)
        try:
            yield {"event": "queued", "data": json.dumps({"message_id": message_id})}

            try:
                handoff_data: dict[str, Any] = await asyncio.wait_for(
                    handoff_future, timeout=_STREAM_HANDOFF_TIMEOUT
                )
            except TimeoutError:
                yield {
                    "event": "error",
                    "data": json.dumps({"error": "Timed out waiting for processing slot"}),
                }
                return

            yield {"event": "processing", "data": ""}

            async for event in self.stream_from_worker(handoff_data):
                yield {
                    "event": event.event or "message",
                    "data": event.data or "",
                }

            yield {"event": "done", "data": json.dumps({"message_id": message_id})}

        except asyncio.CancelledError:
            logger.info(
                "Client disconnected from SSE stream",
                extra={"user_id": user_id, "message_id": message_id},
            )
        finally:
            self.unregister(message_id)
            if self._queue:
                await self._queue.mark_complete(user_id, message_id)
            if self._processor:
                self._processor.trigger_processing(user_id)

    async def close(self) -> None:
        """Close the httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


async def _parse_sse_stream(
    response: httpx.Response,
) -> AsyncIterator[ServerSentEvent]:
    """Parse raw SSE lines from an httpx streaming response."""
    event_type: str | None = None
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
        elif line == "":
            # Blank line = end of event
            if data_lines or event_type:
                yield ServerSentEvent(
                    data="\n".join(data_lines) if data_lines else "",
                    event=event_type or "message",
                )
                event_type = None
                data_lines = []
        # Ignore comment lines (starting with ':') and unknown prefixes

    # Flush any remaining event (stream ended without trailing blank line)
    if data_lines or event_type:
        yield ServerSentEvent(
            data="\n".join(data_lines) if data_lines else "",
            event=event_type or "message",
        )
