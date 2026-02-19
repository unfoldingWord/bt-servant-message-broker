"""Tests for StreamProxy service."""

import asyncio
import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bt_servant_message_broker.services.message_processor import MessageProcessor
from bt_servant_message_broker.services.stream_proxy import StreamProxy, _parse_sse_stream


def _async_iter_from_list(items: list[str]) -> object:
    """Create an async iterator factory from a list of strings."""

    async def _iter() -> AsyncGenerator[str, None]:
        for item in items:
            yield item

    return _iter


@pytest.fixture
def stream_proxy() -> StreamProxy:
    """Create a StreamProxy for testing."""
    return StreamProxy(
        worker_base_url="https://worker.example.com",
        api_key="test-api-key",
        timeout=30.0,
    )


class TestStreamRegistry:
    """Tests for the StreamProxy registry (register/unregister/handoff)."""

    @pytest.mark.asyncio
    async def test_register_returns_future(self, stream_proxy: StreamProxy) -> None:
        """Test that register returns an asyncio.Future."""
        future = stream_proxy.register("msg-1")
        assert isinstance(future, asyncio.Future)
        assert not future.done()

    @pytest.mark.asyncio
    async def test_is_registered_after_register(self, stream_proxy: StreamProxy) -> None:
        """Test that is_registered returns True after registering."""
        stream_proxy.register("msg-1")
        assert stream_proxy.is_registered("msg-1") is True

    def test_is_registered_returns_false_for_unknown(self, stream_proxy: StreamProxy) -> None:
        """Test that is_registered returns False for unknown message_id."""
        assert stream_proxy.is_registered("unknown") is False

    @pytest.mark.asyncio
    async def test_unregister_removes_entry(self, stream_proxy: StreamProxy) -> None:
        """Test that unregister removes the entry."""
        stream_proxy.register("msg-1")
        stream_proxy.unregister("msg-1")
        assert stream_proxy.is_registered("msg-1") is False

    @pytest.mark.asyncio
    async def test_unregister_cancels_pending_future(self, stream_proxy: StreamProxy) -> None:
        """Test that unregister cancels the pending future."""
        future = stream_proxy.register("msg-1")
        stream_proxy.unregister("msg-1")
        assert future.cancelled()

    def test_unregister_unknown_does_not_raise(self, stream_proxy: StreamProxy) -> None:
        """Test that unregistering an unknown message_id is harmless."""
        stream_proxy.unregister("unknown")  # Should not raise

    @pytest.mark.asyncio
    async def test_handoff_resolves_future(self, stream_proxy: StreamProxy) -> None:
        """Test that handoff resolves the Future with message data."""
        future = stream_proxy.register("msg-1")
        message_data = {"user_id": "user1", "message": "hello"}

        stream_proxy.handoff("msg-1", message_data)

        assert future.done()
        assert await future == message_data

    @pytest.mark.asyncio
    async def test_handoff_removes_from_registry(self, stream_proxy: StreamProxy) -> None:
        """Test that handoff removes the entry from registry."""
        stream_proxy.register("msg-1")
        stream_proxy.handoff("msg-1", {"user_id": "user1"})
        assert stream_proxy.is_registered("msg-1") is False

    def test_handoff_unknown_does_not_raise(self, stream_proxy: StreamProxy) -> None:
        """Test that handing off to unknown message_id is harmless."""
        stream_proxy.handoff("unknown", {"data": "test"})  # Should not raise


class TestBuildWorkerPayload:
    """Tests for worker payload mapping."""

    def test_maps_fields_correctly(self, stream_proxy: StreamProxy) -> None:
        """Test that broker fields are mapped to worker payload format."""
        message_data = {
            "user_id": "user1",
            "org_id": "org1",
            "message": "hello",
            "message_type": "text",
            "client_id": "web",
        }
        payload = stream_proxy._build_worker_payload(message_data)

        assert payload["user_id"] == "user1"
        assert payload["org"] == "org1"  # org_id -> org
        assert payload["message"] == "hello"
        assert payload["message_type"] == "text"
        assert payload["client_id"] == "web"
        assert payload["stream"] is True

    def test_includes_audio_fields_when_present(self, stream_proxy: StreamProxy) -> None:
        """Test that audio fields are included when present."""
        message_data = {
            "user_id": "user1",
            "org_id": "org1",
            "message": "",
            "message_type": "audio",
            "client_id": "web",
            "audio_base64": "base64data",
            "audio_format": "ogg",
        }
        payload = stream_proxy._build_worker_payload(message_data)

        assert payload["audio_base64"] == "base64data"
        assert payload["audio_format"] == "ogg"

    def test_omits_audio_fields_when_absent(self, stream_proxy: StreamProxy) -> None:
        """Test that audio fields are omitted when not present."""
        message_data = {
            "user_id": "user1",
            "org_id": "org1",
            "message": "hello",
            "message_type": "text",
            "client_id": "web",
        }
        payload = stream_proxy._build_worker_payload(message_data)

        assert "audio_base64" not in payload
        assert "audio_format" not in payload


class TestParseSSEStream:
    """Tests for SSE stream parsing."""

    @pytest.mark.asyncio
    async def test_parses_basic_event(self) -> None:
        """Test parsing a basic SSE event with event and data fields."""
        response = AsyncMock(spec=httpx.Response)
        response.aiter_lines = _async_iter_from_list(["event: token", "data: hello", ""])

        events = [e async for e in _parse_sse_stream(response)]
        assert len(events) == 1
        assert events[0].event == "token"
        assert events[0].data == "hello"

    @pytest.mark.asyncio
    async def test_parses_multiple_events(self) -> None:
        """Test parsing multiple SSE events."""
        response = AsyncMock(spec=httpx.Response)
        response.aiter_lines = _async_iter_from_list(
            [
                "event: token",
                "data: hello",
                "",
                "event: token",
                "data: world",
                "",
            ]
        )

        events = [e async for e in _parse_sse_stream(response)]
        assert len(events) == 2
        assert events[0].data == "hello"
        assert events[1].data == "world"

    @pytest.mark.asyncio
    async def test_parses_multiline_data(self) -> None:
        """Test parsing SSE event with multiple data lines."""
        response = AsyncMock(spec=httpx.Response)
        response.aiter_lines = _async_iter_from_list(
            [
                "event: message",
                "data: line1",
                "data: line2",
                "",
            ]
        )

        events = [e async for e in _parse_sse_stream(response)]
        assert len(events) == 1
        assert events[0].data == "line1\nline2"

    @pytest.mark.asyncio
    async def test_defaults_event_to_message(self) -> None:
        """Test that events without explicit type default to 'message'."""
        response = AsyncMock(spec=httpx.Response)
        response.aiter_lines = _async_iter_from_list(["data: hello", ""])

        events = [e async for e in _parse_sse_stream(response)]
        assert len(events) == 1
        assert events[0].event == "message"

    @pytest.mark.asyncio
    async def test_flushes_trailing_event(self) -> None:
        """Test that a trailing event without blank line is flushed."""
        response = AsyncMock(spec=httpx.Response)
        response.aiter_lines = _async_iter_from_list(["event: done", "data: final"])

        events = [e async for e in _parse_sse_stream(response)]
        assert len(events) == 1
        assert events[0].event == "done"
        assert events[0].data == "final"

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        """Test that an empty stream yields no events."""
        response = AsyncMock(spec=httpx.Response)
        response.aiter_lines = _async_iter_from_list([])

        events = [e async for e in _parse_sse_stream(response)]
        assert len(events) == 0


class TestStreamFromWorker:
    """Tests for stream_from_worker method."""

    @pytest.mark.asyncio
    async def test_yields_sse_events_from_worker(self, stream_proxy: StreamProxy) -> None:
        """Test that stream_from_worker yields parsed SSE events."""
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.aiter_lines = _async_iter_from_list(
            [
                "event: token",
                "data: Hello",
                "",
                "event: token",
                "data: World",
                "",
            ]
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_stream_cm = AsyncMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        stream_proxy._client = mock_client

        message_data = {"user_id": "user1", "org_id": "org1", "message": "hi"}
        events = [e async for e in stream_proxy.stream_from_worker(message_data)]

        assert len(events) == 2
        assert events[0].data == "Hello"
        assert events[1].data == "World"

    @pytest.mark.asyncio
    async def test_yields_error_on_worker_failure(self, stream_proxy: StreamProxy) -> None:
        """Test that worker HTTP errors yield an error event."""
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.aread = AsyncMock(return_value=b"Internal Server Error")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_stream_cm = AsyncMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        stream_proxy._client = mock_client

        message_data = {"user_id": "user1", "org_id": "org1", "message": "hi"}
        events = [e async for e in stream_proxy.stream_from_worker(message_data)]

        assert len(events) == 1
        assert events[0].event == "error"
        assert "500" in (events[0].data or "")


class TestStreamHandoff:
    """Integration tests: MessageProcessor hands off to StreamProxy."""

    @pytest.mark.asyncio
    async def test_handoff_skips_normal_processing(self) -> None:
        """Test that processor hands off and returns without calling worker."""
        mock_queue = AsyncMock()
        mock_worker = AsyncMock()
        proxy = StreamProxy(
            worker_base_url="https://worker.example.com",
            api_key="key",
        )

        processor = MessageProcessor(mock_queue, mock_worker, stream_proxy=proxy)

        # Register a stream handler
        future = proxy.register("msg-1")

        message_data = json.dumps({"user_id": "user1", "message": "hello"})
        mock_queue.dequeue = AsyncMock(return_value=("msg-1", message_data))

        with patch.object(processor, "_schedule_next_processing"):
            await processor._process_next_message("user1")

        # Worker should NOT have been called
        mock_worker.send_message.assert_not_called()
        # mark_complete should NOT have been called (SSE handler does it)
        mock_queue.mark_complete.assert_not_called()
        # Future should be resolved with parsed message data
        assert future.done()
        result = await future
        assert result["user_id"] == "user1"

    @pytest.mark.asyncio
    async def test_normal_processing_when_not_registered(self) -> None:
        """Test that processor follows normal path when no stream registered."""
        mock_queue = AsyncMock()
        mock_worker = AsyncMock()
        mock_worker.send_message = AsyncMock(
            return_value=MagicMock(
                responses=["Hi"],
                response_language="en",
                voice_audio_base64=None,
            )
        )
        proxy = StreamProxy(
            worker_base_url="https://worker.example.com",
            api_key="key",
        )

        processor = MessageProcessor(mock_queue, mock_worker, stream_proxy=proxy)

        message_data = json.dumps({"user_id": "user1", "message": "hello"})
        mock_queue.dequeue = AsyncMock(return_value=("msg-1", message_data))

        with patch.object(processor, "_schedule_next_processing"):
            await processor._process_next_message("user1")

        # Worker SHOULD have been called (normal path)
        mock_worker.send_message.assert_called_once()
        # mark_complete SHOULD have been called
        mock_queue.mark_complete.assert_called_once_with("user1", "msg-1")

    @pytest.mark.asyncio
    async def test_normal_processing_when_no_stream_proxy(self) -> None:
        """Test that processor works normally when stream_proxy is None."""
        mock_queue = AsyncMock()
        mock_worker = AsyncMock()
        mock_worker.send_message = AsyncMock(
            return_value=MagicMock(
                responses=["Hi"],
                response_language="en",
                voice_audio_base64=None,
            )
        )

        processor = MessageProcessor(mock_queue, mock_worker, stream_proxy=None)

        message_data = json.dumps({"user_id": "user1", "message": "hello"})
        mock_queue.dequeue = AsyncMock(return_value=("msg-1", message_data))

        with patch.object(processor, "_schedule_next_processing"):
            await processor._process_next_message("user1")

        mock_worker.send_message.assert_called_once()
        mock_queue.mark_complete.assert_called_once_with("user1", "msg-1")


class TestProxyStreamTriggersProcessing:
    """Tests that proxy_stream triggers processing after registration."""

    @pytest.mark.asyncio
    async def test_proxy_stream_triggers_processing_after_register(self) -> None:
        """Test that proxy_stream calls trigger_processing after registering."""
        mock_queue = AsyncMock()
        mock_processor = MagicMock(spec=MessageProcessor)
        proxy = StreamProxy(
            worker_base_url="https://worker.example.com",
            api_key="key",
        )
        proxy.configure(mock_queue, mock_processor)

        # Collect the first yielded event (queued), then verify trigger was called.
        async for event in proxy.proxy_stream("user1", "msg-1"):
            assert event["event"] == "queued"
            mock_processor.trigger_processing.assert_called_once_with("user1")
            break  # Exit after first event; generator cleanup runs via finally

    @pytest.mark.asyncio
    async def test_proxy_stream_works_without_processor(self) -> None:
        """Test that proxy_stream works when processor is not configured."""
        proxy = StreamProxy(
            worker_base_url="https://worker.example.com",
            api_key="key",
        )
        # Don't call configure — _processor is None

        async for event in proxy.proxy_stream("user1", "msg-1"):
            assert event["event"] == "queued"
            break  # Should not crash even without processor


class TestStreamProxyClose:
    """Tests for StreamProxy cleanup."""

    @pytest.mark.asyncio
    async def test_close_with_no_client(self, stream_proxy: StreamProxy) -> None:
        """Test that close works when no client was created."""
        await stream_proxy.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_close_closes_client(self, stream_proxy: StreamProxy) -> None:
        """Test that close properly closes the httpx client."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        stream_proxy._client = mock_client

        await stream_proxy.close()

        mock_client.aclose.assert_called_once()
        assert stream_proxy._client is None
