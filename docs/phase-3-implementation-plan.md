# Implementation Plan: Phase 3 - Worker Integration

**Related Issue:** [#3 - Phase 3: Worker Integration](https://github.com/unfoldingWord/bt-servant-message-broker/issues/3)

## Overview

Implement the HTTP client for communicating with bt-servant-worker, handling sync request/response flow. This connects the queue system (Phase 2) to the actual AI processing backend.

## Architecture

```
Client → POST /api/v1/message → Broker
                                  ↓
                            1. Enqueue message
                            2. Try dequeue (atomic)
                                  ↓
              ┌─ Dequeue succeeds ─┴─ Dequeue fails (busy) ─┐
              ↓                                              ↓
         Send to Worker                              Return "queued"
              ↓
         Mark Complete
              ↓
         Return Response ──────────────────────────────────────┐
              ↓                                                │
    [Background Task] ← Schedule next processing (non-blocking)│
              ↓                                                │
         Try dequeue → Process → Mark Complete → Schedule next │
                                                               │
    Note: Background tasks process queued messages without     │
    blocking the original request. Each task processes one     │
    message and spawns another task for the next (if any).     │
```

## Key Design Decisions

1. **Hybrid sync/async**: Attempt immediate processing; return "queued" only if user already has message processing
2. **Fail fast**: No retries at broker layer - return errors to client who can retry
3. **Always mark complete**: Even on errors, to prevent queue stalling
4. **Background queue drain**: After completing a message, spawn a background task to process next in queue (non-blocking, avoids request latency and recursion depth issues)

## bt-servant-worker API

```
POST /api/v1/chat
Authorization: Bearer {WORKER_API_KEY}
Content-Type: application/json

Request:
{
  "client_id": "web",
  "user_id": "user123",
  "message": "Hello",
  "message_type": "text",
  "org": "myorg"  // mapped from org_id
}

Response (200):
{
  "responses": ["Hello! How can I help?"],
  "response_language": "en",
  "voice_audio_base64": null
}
```

## Files Created/Modified

### New Files
- `src/bt_servant_message_broker/services/message_processor.py` - Orchestration layer
- `tests/unit/test_worker_client.py` - WorkerClient unit tests
- `tests/unit/test_message_processor.py` - MessageProcessor unit tests
- `docs/phase-3-implementation-plan.md` - This file
- `docs/test_plans/phase-3-e2e-tests.md` - E2E test plan

### Modified Files
- `src/bt_servant_message_broker/config.py` - Added worker_timeout setting
- `src/bt_servant_message_broker/services/worker_client.py` - Complete implementation with httpx
- `src/bt_servant_message_broker/models/messages.py` - Added MessageResponse, updated HealthResponse
- `src/bt_servant_message_broker/models/__init__.py` - Export MessageResponse
- `src/bt_servant_message_broker/api/dependencies.py` - Added worker and processor dependencies
- `src/bt_servant_message_broker/main.py` - Initialize WorkerClient and MessageProcessor in lifespan
- `src/bt_servant_message_broker/api/routes.py` - Wire up processing, update health endpoint
- `tests/conftest.py` - Added mock fixtures
- `tests/unit/test_api.py` - Updated for new response model

## Error Handling Matrix

| Error | HTTP Status | Action |
|-------|-------------|--------|
| Worker timeout | 504 | Mark complete, return error |
| Worker 4xx | Pass through | Mark complete, return error |
| Worker 5xx | 502 | Mark complete, return error |
| Worker unreachable | 503 | Mark complete, return error |
| Redis unavailable | 503 | Fail before enqueue |
| No message processor | 200 (queued) | Queue only, no processing |

## Verification Checklist

- [x] `ruff check .` passes
- [x] `ruff format --check .` passes
- [x] `mypy .` passes
- [x] `pyright` passes
- [x] `lint-imports` passes
- [x] `pytest --cov` passes with 65%+ coverage
- [ ] E2E tests pass (see docs/test_plans/phase-3-e2e-tests.md)
