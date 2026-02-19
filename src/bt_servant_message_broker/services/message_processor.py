"""Message processing orchestration layer."""

import asyncio
import ipaddress
import json
import logging
import socket
import ssl
from urllib.parse import urlparse

import httpx

from bt_servant_message_broker.services.queue_manager import QueueManager
from bt_servant_message_broker.services.stream_proxy import StreamProxy
from bt_servant_message_broker.services.worker_client import WorkerClient, WorkerResponse

logger = logging.getLogger(__name__)

_CALLBACK_TIMEOUT = 10.0
_STREAM_REGISTRATION_TIMEOUT = 30.0  # seconds to wait for SSE client to connect


def _sanitize_url_for_log(url: str) -> str:
    """Return scheme://host for safe logging (strip path/query/fragment)."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}"


async def _validate_callback_host(callback_url: str) -> str | None:
    """Resolve callback hostname and block private/internal IPs (SSRF prevention).

    Resolves the hostname once and returns a validated IP so the caller can
    connect directly to it, closing the TOCTOU window where DNS could rebind
    between validation and the HTTP request.

    Returns:
        A validated public IP string to connect to, or None if DNS failed
        (let httpx surface the connection error naturally).

    Raises:
        ValueError: If any resolved IP is in a blocked range.
    """
    hostname = urlparse(callback_url).hostname
    if not hostname:
        raise ValueError("callback_url has no hostname")

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None  # DNS resolution failed - let httpx surface the connection error

    validated_ip: str | None = None
    for info in infos:
        ip_str = info[4][0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_unspecified
        ):
            raise ValueError(f"callback_url hostname resolves to blocked IP: {ip_str}")
        if validated_ip is None:
            validated_ip = ip_str
    return validated_ip


async def _post_pinned(
    validated_ip: str,
    callback_url: str,
    payload: dict[str, object],
) -> int:
    """POST JSON to callback URL, connecting directly to a pre-validated IP.

    Uses asyncio.open_connection with ``server_hostname`` so TLS validates
    against the original hostname while the TCP connection goes to the
    already-validated IP address.  This closes the DNS-rebinding TOCTOU
    window that would exist if we let httpx re-resolve DNS on its own.

    Returns:
        HTTP status code from the server.
    """
    parsed = urlparse(callback_url)
    hostname = parsed.hostname or ""
    port = parsed.port or 443
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"

    ssl_ctx = ssl.create_default_context()
    body = json.dumps(payload).encode()
    request_bytes = (
        f"POST {target} HTTP/1.1\r\n"
        f"Host: {hostname}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode() + body

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            validated_ip,
            port,
            ssl=ssl_ctx,
            server_hostname=hostname,
        ),
        timeout=_CALLBACK_TIMEOUT,
    )

    try:
        writer.write(request_bytes)
        await asyncio.wait_for(writer.drain(), timeout=_CALLBACK_TIMEOUT)

        status_line = await asyncio.wait_for(
            reader.readline(),
            timeout=_CALLBACK_TIMEOUT,
        )
        parts = status_line.split(b" ", 2)
        return int(parts[1]) if len(parts) >= 2 else 0
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            logger.debug("writer.wait_closed() raised during cleanup", exc_info=True)


class MessageProcessor:
    """Orchestrates message processing through queue and worker.

    All processing is asynchronous via background tasks:
    - Route calls trigger_processing() after enqueue
    - Background task dequeues and sends to worker
    - Response delivered to callback_url if provided
    - After completion, schedules next message processing
    """

    def __init__(
        self,
        queue_manager: QueueManager,
        worker_client: WorkerClient,
        stream_proxy: StreamProxy | None = None,
    ) -> None:
        """Initialize the message processor.

        Args:
            queue_manager: QueueManager for queue operations.
            worker_client: WorkerClient for worker communication.
            stream_proxy: Optional StreamProxy for SSE stream handoff.
        """
        self._queue = queue_manager
        self._worker = worker_client
        self._stream_proxy = stream_proxy

    def trigger_processing(self, user_id: str) -> None:
        """Kick off background processing for a user's queue.

        Safe to call multiple times - if the user is already processing,
        the background task will find the processing lock set and exit.

        Args:
            user_id: User identifier.
        """
        self._schedule_next_processing(user_id)

    def _schedule_next_processing(self, user_id: str) -> None:
        """Schedule background task to process next message in queue.

        Uses asyncio.create_task to avoid blocking the current request.
        Each task processes one message and schedules the next if needed.
        """
        asyncio.create_task(self._process_next_message(user_id))

    async def _process_next_message(self, user_id: str) -> None:
        """Process the next message in queue and deliver via callback.

        This runs as a fire-and-forget background task. It processes one
        message, delivers the response to the callback_url if provided,
        and schedules another task for the next message.
        """
        try:
            dequeued = await self._queue.dequeue(user_id)
            if dequeued is None:
                logger.debug(
                    "No more messages to process in background",
                    extra={"user_id": user_id},
                )
                return

            message_id, message_data = dequeued
            logger.info(
                "Processing message in background",
                extra={"user_id": user_id, "message_id": message_id},
            )

            # Hand off to SSE stream handler if one is waiting for this message
            if self._stream_proxy and self._stream_proxy.is_registered(message_id):
                parsed = json.loads(message_data)
                self._stream_proxy.handoff(message_id, parsed)
                return  # SSE handler takes over mark_complete + trigger_next

            # For SSE messages (no callback_url), wait for the client to open
            # the stream before processing. This handles the drain-chain race
            # where a prior message completes and the processor dequeues this
            # SSE message before the client connects to GET /api/v1/stream.
            pre_parsed = json.loads(message_data)
            if not pre_parsed.get("callback_url") and self._stream_proxy:
                registered = await self._stream_proxy.wait_for_registration(
                    message_id, timeout=_STREAM_REGISTRATION_TIMEOUT
                )
                if registered:
                    self._stream_proxy.handoff(message_id, pre_parsed)
                    return  # SSE handler takes over mark_complete + trigger_next
                logger.warning(
                    "SSE message has no stream registration — discarding",
                    extra={"user_id": user_id, "message_id": message_id},
                )
                await self._queue.mark_complete(user_id, message_id)
                self._schedule_next_processing(user_id)
                return

            callback_url: str | None = None
            msg_user_id = user_id

            try:
                parsed = json.loads(message_data)
                msg_user_id = parsed.get("user_id", user_id)
                callback_url = parsed.get("callback_url")

                response = await self._worker.send_message(parsed)
                logger.info(
                    "Message processed successfully",
                    extra={"user_id": user_id, "message_id": message_id},
                )

                # Deliver response via callback if URL was provided
                if callback_url:
                    await self._deliver_callback(callback_url, message_id, msg_user_id, response)
                else:
                    logger.warning(
                        "No callback_url - response cannot be delivered to client",
                        extra={"user_id": user_id, "message_id": message_id},
                    )
            except Exception as e:
                logger.error(
                    "Message processing failed",
                    extra={
                        "user_id": user_id,
                        "message_id": message_id,
                        "error": str(e),
                    },
                )
                # Deliver error via callback if URL was provided
                if callback_url:
                    await self._deliver_error_callback(
                        callback_url, message_id, msg_user_id, str(e)
                    )
            finally:
                await self._queue.mark_complete(user_id, message_id)
                # Schedule next message processing (non-recursive: new task)
                self._schedule_next_processing(user_id)

        except Exception as e:
            logger.error(
                "Background processing task failed",
                extra={"user_id": user_id, "error": str(e)},
            )

    async def _deliver_callback(
        self,
        callback_url: str,
        message_id: str,
        user_id: str,
        response: WorkerResponse,
    ) -> None:
        """POST worker response to the client's callback URL.

        Args:
            callback_url: URL to deliver the response to.
            message_id: Message ID for correlation.
            user_id: User ID for routing (e.g. WhatsApp phone number).
            response: Worker response to deliver.
        """
        try:
            validated_ip = await _validate_callback_host(callback_url)
        except ValueError as e:
            logger.error(
                "Callback blocked by SSRF protection",
                extra={"message_id": message_id, "error": str(e)},
            )
            return

        payload: dict[str, object] = {
            "message_id": message_id,
            "user_id": user_id,
            "status": "completed",
            "responses": response.responses,
            "response_language": response.response_language,
            "voice_audio_base64": response.voice_audio_base64,
        }

        try:
            status_code = await self._post_callback_payload(
                callback_url,
                payload,
                validated_ip,
            )

            if status_code >= 400:
                logger.error(
                    "Callback delivery got error response",
                    extra={
                        "message_id": message_id,
                        "callback_url": _sanitize_url_for_log(callback_url),
                        "status_code": status_code,
                    },
                )
            else:
                logger.info(
                    "Callback delivered successfully",
                    extra={
                        "message_id": message_id,
                        "callback_url": _sanitize_url_for_log(callback_url),
                        "status_code": status_code,
                    },
                )
        except Exception as e:
            logger.error(
                "Callback delivery failed",
                extra={
                    "message_id": message_id,
                    "callback_url": _sanitize_url_for_log(callback_url),
                    "error": str(e),
                },
            )

    async def _deliver_error_callback(
        self,
        callback_url: str,
        message_id: str,
        user_id: str,
        error_detail: str,
    ) -> None:
        """POST error details to the client's callback URL.

        Args:
            callback_url: URL to deliver the error to.
            message_id: Message ID for correlation.
            user_id: User ID for routing.
            error_detail: Description of what went wrong.
        """
        try:
            validated_ip = await _validate_callback_host(callback_url)
        except ValueError as e:
            logger.error(
                "Error callback blocked by SSRF protection",
                extra={"message_id": message_id, "error": str(e)},
            )
            return

        payload: dict[str, object] = {
            "message_id": message_id,
            "user_id": user_id,
            "status": "error",
            "error": error_detail,
        }

        try:
            status_code = await self._post_callback_payload(
                callback_url,
                payload,
                validated_ip,
            )

            if status_code >= 400:
                logger.error(
                    "Error callback delivery got error response",
                    extra={
                        "message_id": message_id,
                        "callback_url": _sanitize_url_for_log(callback_url),
                        "status_code": status_code,
                    },
                )
            else:
                logger.info(
                    "Error callback delivered",
                    extra={
                        "message_id": message_id,
                        "callback_url": _sanitize_url_for_log(callback_url),
                    },
                )
        except Exception as e:
            logger.error(
                "Error callback delivery failed",
                extra={
                    "message_id": message_id,
                    "callback_url": _sanitize_url_for_log(callback_url),
                    "error": str(e),
                },
            )

    async def _post_callback_payload(
        self,
        callback_url: str,
        payload: dict[str, object],
        validated_ip: str | None,
    ) -> int:
        """POST JSON payload to callback URL with IP-pinning when possible.

        When *validated_ip* is available, connects directly via
        ``asyncio.open_connection`` to the pre-validated IP (with TLS
        hostname verification preserved via ``server_hostname``).

        Falls back to httpx when DNS resolution failed earlier (validated_ip
        is None) so that httpx can surface the connection error naturally.

        Returns:
            HTTP status code from the server.
        """
        if validated_ip:
            return await _post_pinned(validated_ip, callback_url, payload)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_CALLBACK_TIMEOUT),
        ) as client:
            result = await client.post(callback_url, json=payload)
        return result.status_code
