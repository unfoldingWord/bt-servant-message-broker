# Implementation Plan: Phase 2 - Core Queue Logic

**Related Issue:** [#2 - Phase 2: Core Queue Logic](https://github.com/unfoldingWord/bt-servant-message-broker/issues/2)
**PR:** [#8 - feat: Phase 2 - Core Queue Logic](https://github.com/unfoldingWord/bt-servant-message-broker/pull/8)

## Overview

Implement the Redis-based per-user message queue system with FIFO ordering guarantees. This phase transforms the stub implementations into working queue operations.

## Redis Schema

```
# Per-user queue (Redis LIST - FIFO via RPUSH/LPOP)
user:{user_id}:queue → ["msg_json_1", "msg_json_2", ...]

# Currently processing flag (Redis STRING with TTL: 300s)
user:{user_id}:processing → "msg_id"

# Message metadata (Redis HASH)
message:{msg_id} → {
  "user_id": "...",
  "queued_at": "...",
  "started_at": "...",
}
```

## Files to Modify

### 1. `src/bt_servant_message_broker/services/queue_manager.py`

**Complete rewrite** - implement all methods:

```python
import json
import uuid
from datetime import datetime, timezone
from typing import Any


class QueueManager:
    PROCESSING_TTL = 300  # 5 minutes

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    @staticmethod
    def generate_message_id() -> str:
        return str(uuid.uuid4())

    async def enqueue(self, user_id: str, message_id: str, message_data: str) -> int:
        # RPUSH to user:{user_id}:queue
        # Store metadata in message:{msg_id} hash
        # Return queue length as position

    async def dequeue(self, user_id: str) -> tuple[str, str] | None:
        # LPOP from user:{user_id}:queue
        # Set user:{user_id}:processing with TTL
        # Update message metadata with started_at
        # Return (message_id, message_data) or None

    async def mark_complete(self, user_id: str, message_id: str) -> None:
        # DELETE user:{user_id}:processing
        # Clean up message:{msg_id}

    async def get_queue_length(self, user_id: str) -> int:
        # LLEN user:{user_id}:queue

    async def is_processing(self, user_id: str) -> bool:
        # EXISTS user:{user_id}:processing

    async def get_current_message_id(self, user_id: str) -> str | None:
        # GET user:{user_id}:processing

    async def get_active_queue_count(self) -> int:
        # SCAN for user:*:queue keys, count non-empty

    async def get_processing_count(self) -> int:
        # SCAN for user:*:processing keys

    async def ping(self) -> bool:
        # Redis PING to check connectivity
```

**Key design decisions:**
- Accept Redis client via constructor (dependency injection)
- Use RPUSH/LPOP for FIFO ordering
- SETEX for processing flag with TTL (prevents stale locks)
- SCAN for counting (handles large key spaces)

### 2. `src/bt_servant_message_broker/main.py`

**Modify lifespan** to initialize Redis:

```python
from redis.asyncio import Redis
from bt_servant_message_broker.services.queue_manager import QueueManager

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger(__name__)

    # Initialize Redis
    redis_client: Any = None
    queue_manager: QueueManager | None = None

    try:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        await redis_client.ping()
        queue_manager = QueueManager(redis_client)
        logger.info("Redis connected successfully")
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")

    # Store in app.state for dependency access
    app.state.redis = redis_client
    app.state.queue_manager = queue_manager

    yield

    # Cleanup
    if redis_client:
        await redis_client.close()
    logger.info("Shutting down bt-servant-message-broker")
```

### 3. `src/bt_servant_message_broker/api/dependencies.py`

**Add QueueManager dependency:**

```python
from fastapi import Request

def get_queue_manager(request: Request) -> QueueManager | None:
    return getattr(request.app.state, "queue_manager", None)

RequireQueueManager = Annotated[QueueManager | None, Depends(get_queue_manager)]
```

### 4. `src/bt_servant_message_broker/api/routes.py`

**Update all endpoints** to use QueueManager:

```python
@router.post("/api/v1/message", response_model=QueuedResponse)
async def submit_message(
    request: MessageRequest,
    _api_key: RequireApiKey,
    queue_manager: RequireQueueManager,
) -> QueuedResponse:
    if queue_manager is None:
        raise HTTPException(503, "Queue service unavailable")

    message_id = QueueManager.generate_message_id()
    message_data = request.model_dump_json()
    position = await queue_manager.enqueue(request.user_id, message_id, message_data)

    return QueuedResponse(
        status="queued",
        queue_position=position,
        message_id=message_id,
    )

@router.get("/api/v1/queue/{user_id}", response_model=QueueStatusResponse)
async def get_queue_status(
    user_id: str,
    _api_key: RequireApiKey,
    queue_manager: RequireQueueManager,
) -> QueueStatusResponse:
    if queue_manager is None:
        raise HTTPException(503, "Queue service unavailable")

    return QueueStatusResponse(
        user_id=user_id,
        queue_length=await queue_manager.get_queue_length(user_id),
        is_processing=await queue_manager.is_processing(user_id),
        current_message_id=await queue_manager.get_current_message_id(user_id),
    )

@router.get("/health", response_model=HealthResponse)
async def health_check(queue_manager: RequireQueueManager) -> HealthResponse:
    if queue_manager is None:
        return HealthResponse(
            status="degraded",
            redis_connected=False,
            active_queues=0,
            messages_processing=0,
        )

    try:
        redis_ok = await queue_manager.ping()
        active = await queue_manager.get_active_queue_count()
        processing = await queue_manager.get_processing_count()

        return HealthResponse(
            status="healthy" if redis_ok else "degraded",
            redis_connected=redis_ok,
            active_queues=active,
            messages_processing=processing,
        )
    except Exception:
        return HealthResponse(
            status="unhealthy",
            redis_connected=False,
            active_queues=0,
            messages_processing=0,
        )
```

### 5. `tests/conftest.py`

**Add Redis mock fixtures:**

```python
import pytest
from unittest.mock import AsyncMock

@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis client for unit tests."""
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.rpush = AsyncMock(return_value=1)
    redis.lpop = AsyncMock(return_value=None)
    redis.llen = AsyncMock(return_value=0)
    redis.setex = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.exists = AsyncMock(return_value=0)
    redis.delete = AsyncMock()
    redis.hset = AsyncMock()
    redis.scan = AsyncMock(return_value=(0, []))
    return redis

@pytest.fixture
def queue_manager(mock_redis: AsyncMock) -> QueueManager:
    """QueueManager with mocked Redis."""
    return QueueManager(mock_redis)
```

### 6. `tests/unit/test_queue_manager.py` (NEW)

**Comprehensive unit tests:**

```python
class TestQueueManager:
    async def test_enqueue_returns_position(self, queue_manager, mock_redis):
        mock_redis.rpush.return_value = 3
        position = await queue_manager.enqueue("user1", "msg1", '{"data":"test"}')
        assert position == 3
        mock_redis.rpush.assert_called_once()

    async def test_dequeue_empty_queue(self, queue_manager, mock_redis):
        mock_redis.lpop.return_value = None
        result = await queue_manager.dequeue("user1")
        assert result is None

    async def test_dequeue_returns_message(self, queue_manager, mock_redis):
        mock_redis.lpop.return_value = '{"id":"msg1","data":"test"}'
        result = await queue_manager.dequeue("user1")
        assert result is not None

    async def test_fifo_ordering(self, queue_manager, mock_redis):
        # Test that messages come out in order

    async def test_processing_flag_set_on_dequeue(self, queue_manager, mock_redis):
        # Verify SETEX called with TTL

    async def test_is_processing_true_when_flag_exists(self, queue_manager, mock_redis):
        mock_redis.exists.return_value = 1
        assert await queue_manager.is_processing("user1") is True

    async def test_ping_returns_true(self, queue_manager, mock_redis):
        assert await queue_manager.ping() is True
```

### 7. `tests/integration/test_queue_integration.py` (NEW)

**Integration tests with real Redis (optional, CI-gated):**

```python
import pytest
from redis.asyncio import Redis

@pytest.fixture
async def real_redis():
    """Real Redis for integration tests."""
    redis = Redis.from_url("redis://localhost:6379", decode_responses=True)
    yield redis
    await redis.flushdb()  # Clean up
    await redis.close()

@pytest.mark.integration
class TestQueueIntegration:
    async def test_full_queue_lifecycle(self, real_redis):
        qm = QueueManager(real_redis)

        # Enqueue multiple messages
        pos1 = await qm.enqueue("user1", "msg1", '{"order":1}')
        pos2 = await qm.enqueue("user1", "msg2", '{"order":2}')

        assert pos1 == 1
        assert pos2 == 2
        assert await qm.get_queue_length("user1") == 2

        # Dequeue in FIFO order
        msg1 = await qm.dequeue("user1")
        assert "order\":1" in msg1[1]
        assert await qm.is_processing("user1") is True

        await qm.mark_complete("user1", msg1[0])
        assert await qm.is_processing("user1") is False
```

## Implementation Order

1. **QueueManager implementation** - core Redis operations
2. **main.py lifespan** - Redis client initialization
3. **dependencies.py** - add QueueManager dependency
4. **routes.py** - wire up endpoints to QueueManager
5. **Unit tests** - test_queue_manager.py with mocks
6. **Integration tests** - optional, requires local Redis
7. **Update existing tests** - handle new dependencies

## Verification Checklist

- [x] `ruff check .` passes
- [x] `ruff format --check .` passes
- [x] `mypy .` passes
- [x] `pyright` passes
- [x] `lint-imports` passes
- [x] `pytest --cov` passes with 65%+ coverage (achieved: 83%)
- [x] Manual testing:
  ```bash
  # Start local Redis
  docker run -d -p 6379:6379 redis:alpine

  # Set Redis URL
  export REDIS_URL=redis://localhost:6379

  # Start app
  uvicorn bt_servant_message_broker.main:app --reload

  # Test endpoints
  curl http://localhost:8000/health
  # Should show redis_connected: true

  curl -X POST http://localhost:8000/api/v1/message \
    -H "Content-Type: application/json" \
    -d '{"user_id":"test","org_id":"org","message":"hello","client_id":"web"}'
  # Should return queue_position and message_id

  curl http://localhost:8000/api/v1/queue/test
  # Should show queue_length: 1
  ```

## Key Files Summary

| File | Action | Status |
|------|--------|--------|
| `src/bt_servant_message_broker/services/queue_manager.py` | Rewrite | Done |
| `src/bt_servant_message_broker/main.py` | Modify lifespan | Done |
| `src/bt_servant_message_broker/api/dependencies.py` | Add dependency | Done |
| `src/bt_servant_message_broker/api/routes.py` | Update endpoints | Done |
| `tests/conftest.py` | Add Redis fixtures | Done |
| `tests/unit/test_queue_manager.py` | New file | Done |
| `tests/integration/test_queue_integration.py` | New file | Done |
| `tests/unit/test_api.py` | Update for mocking | Done |
| `pyproject.toml` | Add integration marker | Done |
| `.pre-commit-config.yaml` | Exclude integration tests | Done |
| `.github/workflows/ci.yml` | Exclude integration tests | Done |

## Notes

- Redis client uses `decode_responses=True` for string handling
- Processing TTL of 300s prevents stale locks if worker crashes
- SCAN used for counting to handle large key spaces efficiently
- Health endpoint gracefully degrades if Redis unavailable
- Message metadata stored in hash for future debugging/monitoring
- Integration tests are skipped in CI (require running Redis)
