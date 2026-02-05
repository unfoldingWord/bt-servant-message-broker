"""Tests for API routes."""

from fastapi.testclient import TestClient

from bt_servant_message_broker.main import app


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


class TestMessageEndpoint:
    """Tests for the POST /api/v1/message endpoint."""

    def test_submit_message_returns_queued(self) -> None:
        """Test that submitting a message returns queued status."""
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


class TestQueueStatusEndpoint:
    """Tests for the GET /api/v1/queue/{user_id} endpoint."""

    def test_get_queue_status(self) -> None:
        """Test getting queue status for a user."""
        client = TestClient(app)
        response = client.get("/api/v1/queue/user123")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "user123"
        assert "queue_length" in data
        assert "is_processing" in data
