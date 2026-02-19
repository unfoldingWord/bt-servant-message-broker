"""Tests for API routes."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from bt_servant_message_broker.api.dependencies import (
    get_message_processor,
    get_queue_manager,
    get_worker_client,
)
from bt_servant_message_broker.main import app
from bt_servant_message_broker.services.message_processor import MessageProcessor
from bt_servant_message_broker.services.queue_manager import QueueManager
from bt_servant_message_broker.services.worker_client import (
    WorkerClient,
    WorkerError,
    WorkerResponse,
    WorkerTimeoutError,
)


def create_mock_queue_manager() -> QueueManager:
    """Create a mock QueueManager for testing."""
    mock_redis = AsyncMock()
    mock_redis.rpush = AsyncMock(return_value=1)
    mock_redis.lpop = AsyncMock(return_value=None)
    mock_redis.llen = AsyncMock(return_value=0)
    mock_redis.setex = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.exists = AsyncMock(return_value=0)
    mock_redis.delete = AsyncMock()
    mock_redis.hset = AsyncMock()
    mock_redis.scan = AsyncMock(return_value=(0, []))
    mock_redis.ping = AsyncMock(return_value=True)
    return QueueManager(mock_redis)


def create_mock_worker_client(healthy: bool = True) -> AsyncMock:
    """Create a mock WorkerClient for testing."""
    mock = AsyncMock(spec=WorkerClient)
    mock.send_message = AsyncMock(
        return_value=WorkerResponse(
            responses=["Hello!"],
            response_language="en",
            voice_audio_base64=None,
        )
    )
    mock.health_check = AsyncMock(return_value=healthy)
    return mock


class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_returns_200(self) -> None:
        """Test that health endpoint returns 200."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_response_structure(self) -> None:
        """Test that health response has correct structure."""
        client = TestClient(app)
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert "redis_connected" in data
        assert "active_queues" in data
        assert "messages_processing" in data
        assert "worker_connected" in data

    def test_health_returns_degraded_without_redis(self) -> None:
        """Test that health returns degraded when Redis is not connected."""
        client = TestClient(app)
        response = client.get("/health")
        data = response.json()
        # Without Redis connection, status should be degraded
        assert data["status"] == "degraded"
        assert data["redis_connected"] is False

    def test_health_returns_healthy_with_redis_and_worker(self) -> None:
        """Test that health returns healthy when Redis and worker are connected."""
        mock_qm = create_mock_queue_manager()
        mock_wc = create_mock_worker_client(healthy=True)
        app.dependency_overrides[get_queue_manager] = lambda: mock_qm
        app.dependency_overrides[get_worker_client] = lambda: mock_wc
        try:
            client = TestClient(app)
            response = client.get("/health")
            data = response.json()
            assert data["status"] == "healthy"
            assert data["redis_connected"] is True
            assert data["worker_connected"] is True
        finally:
            app.dependency_overrides.clear()

    def test_health_returns_degraded_with_redis_no_worker(self) -> None:
        """Test that health returns degraded when Redis OK but worker down."""
        mock_qm = create_mock_queue_manager()
        mock_wc = create_mock_worker_client(healthy=False)
        app.dependency_overrides[get_queue_manager] = lambda: mock_qm
        app.dependency_overrides[get_worker_client] = lambda: mock_wc
        try:
            client = TestClient(app)
            response = client.get("/health")
            data = response.json()
            assert data["status"] == "degraded"
            assert data["redis_connected"] is True
            assert data["worker_connected"] is False
        finally:
            app.dependency_overrides.clear()


class TestMessageEndpoint:
    """Tests for the POST /api/v1/message endpoint."""

    def test_submit_message_returns_queued_without_processor(self) -> None:
        """Test that message is queued when no processor is available."""
        mock_qm = create_mock_queue_manager()
        app.dependency_overrides[get_queue_manager] = lambda: mock_qm
        app.dependency_overrides[get_message_processor] = lambda: None
        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/message",
                json={
                    "user_id": "user123",
                    "org_id": "org456",
                    "message": "Hello",
                    "client_id": "web",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "queued"
            assert "queue_position" in data
            assert "message_id" in data
        finally:
            app.dependency_overrides.clear()

    def test_submit_message_returns_completed_when_processed(self) -> None:
        """Test that message returns completed when processed immediately."""
        mock_qm = create_mock_queue_manager()
        mock_wc = create_mock_worker_client()
        mock_processor = AsyncMock(spec=MessageProcessor)
        mock_processor.process_message = AsyncMock(
            return_value=WorkerResponse(
                responses=["Hello there!"],
                response_language="en",
                voice_audio_base64=None,
            )
        )

        app.dependency_overrides[get_queue_manager] = lambda: mock_qm
        app.dependency_overrides[get_worker_client] = lambda: mock_wc
        app.dependency_overrides[get_message_processor] = lambda: mock_processor
        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/message",
                json={
                    "user_id": "user123",
                    "org_id": "org456",
                    "message": "Hello",
                    "client_id": "web",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "completed"
            assert data["responses"] == ["Hello there!"]
            assert data["response_language"] == "en"
        finally:
            app.dependency_overrides.clear()

    def test_submit_message_returns_queued_when_user_busy(self) -> None:
        """Test that message returns queued when user is busy."""
        mock_qm = create_mock_queue_manager()
        mock_wc = create_mock_worker_client()
        mock_processor = AsyncMock(spec=MessageProcessor)
        mock_processor.process_message = AsyncMock(return_value=None)  # User busy

        app.dependency_overrides[get_queue_manager] = lambda: mock_qm
        app.dependency_overrides[get_worker_client] = lambda: mock_wc
        app.dependency_overrides[get_message_processor] = lambda: mock_processor
        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/message",
                json={
                    "user_id": "user123",
                    "org_id": "org456",
                    "message": "Hello",
                    "client_id": "web",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "queued"
            assert data["queue_position"] == 1
        finally:
            app.dependency_overrides.clear()

    def test_submit_message_timeout_returns_504(self) -> None:
        """Test that worker timeout returns 504."""
        mock_qm = create_mock_queue_manager()
        mock_processor = AsyncMock(spec=MessageProcessor)
        mock_processor.process_message = AsyncMock(side_effect=WorkerTimeoutError(60.0))

        app.dependency_overrides[get_queue_manager] = lambda: mock_qm
        app.dependency_overrides[get_message_processor] = lambda: mock_processor
        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/message",
                json={
                    "user_id": "user123",
                    "org_id": "org456",
                    "message": "Hello",
                    "client_id": "web",
                },
            )
            assert response.status_code == 504
            assert "timed out" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    def test_submit_message_worker_error_returns_502(self) -> None:
        """Test that worker 5xx error returns 502."""
        mock_qm = create_mock_queue_manager()
        mock_processor = AsyncMock(spec=MessageProcessor)
        mock_processor.process_message = AsyncMock(
            side_effect=WorkerError(500, "Internal server error")
        )

        app.dependency_overrides[get_queue_manager] = lambda: mock_qm
        app.dependency_overrides[get_message_processor] = lambda: mock_processor
        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/message",
                json={
                    "user_id": "user123",
                    "org_id": "org456",
                    "message": "Hello",
                    "client_id": "web",
                },
            )
            assert response.status_code == 502
        finally:
            app.dependency_overrides.clear()

    def test_submit_message_worker_4xx_passthrough(self) -> None:
        """Test that worker 4xx error is passed through."""
        mock_qm = create_mock_queue_manager()
        mock_processor = AsyncMock(spec=MessageProcessor)
        mock_processor.process_message = AsyncMock(side_effect=WorkerError(400, "Bad request"))

        app.dependency_overrides[get_queue_manager] = lambda: mock_qm
        app.dependency_overrides[get_message_processor] = lambda: mock_processor
        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/message",
                json={
                    "user_id": "user123",
                    "org_id": "org456",
                    "message": "Hello",
                    "client_id": "web",
                },
            )
            assert response.status_code == 400
        finally:
            app.dependency_overrides.clear()

    def test_submit_message_missing_fields(self) -> None:
        """Test that missing required fields returns 422."""
        client = TestClient(app)
        response = client.post(
            "/api/v1/message",
            json={"user_id": "user123"},
        )
        assert response.status_code == 422

    def test_submit_message_invalid_client_id(self) -> None:
        """Test that invalid client_id returns 422."""
        client = TestClient(app)
        response = client.post(
            "/api/v1/message",
            json={
                "user_id": "user123",
                "org_id": "org456",
                "message": "Hello",
                "client_id": "invalid",
            },
        )
        assert response.status_code == 422

    def test_submit_message_returns_503_without_redis(self) -> None:
        """Test that submit returns 503 when Redis is unavailable."""
        client = TestClient(app)
        response = client.post(
            "/api/v1/message",
            json={
                "user_id": "user123",
                "org_id": "org456",
                "message": "Hello",
                "client_id": "web",
            },
        )
        assert response.status_code == 503
        data = response.json()
        assert data["detail"] == "Queue service unavailable"


class TestQueueStatusEndpoint:
    """Tests for the GET /api/v1/queue/{user_id} endpoint."""

    def test_get_queue_status(self) -> None:
        """Test getting queue status for a user."""
        mock_qm = create_mock_queue_manager()
        app.dependency_overrides[get_queue_manager] = lambda: mock_qm
        try:
            client = TestClient(app)
            response = client.get("/api/v1/queue/user123")
            assert response.status_code == 200
            data = response.json()
            assert data["user_id"] == "user123"
            assert "queue_length" in data
            assert "is_processing" in data
        finally:
            app.dependency_overrides.clear()

    def test_get_queue_status_returns_503_without_redis(self) -> None:
        """Test that queue status returns 503 when Redis is unavailable."""
        client = TestClient(app)
        response = client.get("/api/v1/queue/user123")
        assert response.status_code == 503
        data = response.json()
        assert data["detail"] == "Queue service unavailable"
