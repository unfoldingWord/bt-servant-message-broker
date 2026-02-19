# Implementation Plan: Phase 3 - Worker Integration

**Related Issue:** [#3 - Phase 3: Worker Integration](https://github.com/unfoldingWord/bt-servant-message-broker/issues/3)

## Overview

Implement the HTTP client for communicating with bt-servant-worker, handling async message processing with callback delivery. This connects the queue system (Phase 2) to the actual AI processing backend.

## Architecture

```
Client sends message (with callback_url)
    │
    ├──► Broker: enqueue → return {"status":"queued"} immediately
    │    Broker: trigger_processing(user_id)
    │
    │    [Background task]
    │    dequeue → send to worker → get AI response
    │    POST response to callback_url
    │    mark_complete → trigger next
    │
    ◄── Client's callback endpoint receives response
         (user_id in payload = WhatsApp phone number → sendToWhatsApp)
```

### Always-Async Design

Every message returns `{"status": "queued"}` immediately. All AI processing happens in background tasks. Responses are delivered via POST to the client's `callback_url`.

This ensures **every message gets a visible response**, even when messages queue behind each other. The previous hybrid sync/async approach silently dropped responses for queued messages.

## Key Design Decisions

1. **Always-async with callback**: Every message is queued and returns immediately. AI responses are delivered via POST to `callback_url`. No sync processing path.
2. **Fail fast**: No retries at broker layer - return errors to client via error callback.
3. **Always mark complete**: Even on errors, to prevent queue stalling.
4. **Background queue drain**: After completing a message, spawn a background task to process next in queue (non-blocking, avoids recursion depth issues).
5. **Error callbacks**: When worker processing fails, POST an error payload to `callback_url` so the client can inform the user.

## Callback Payloads

### Success Callback
```json
{
  "message_id": "uuid",
  "user_id": "15551234567",
  "status": "completed",
  "responses": ["Hello! How can I help?"],
  "response_language": "en",
  "voice_audio_base64": null
}
```

### Error Callback
```json
{
  "message_id": "uuid",
  "user_id": "15551234567",
  "status": "error",
  "error": "Worker error 500: Internal server error"
}
```

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

| Error | Action | Callback |
|-------|--------|----------|
| Worker timeout | Mark complete, schedule next | Error callback with timeout detail |
| Worker 4xx | Mark complete, schedule next | Error callback with error detail |
| Worker 5xx | Mark complete, schedule next | Error callback with error detail |
| Worker unreachable | Mark complete, schedule next | Error callback with connection error |
| No callback_url | Mark complete, schedule next | Warning logged, no delivery |
| Redis unavailable | 503 HTTP response | N/A (fails before enqueue) |
| No message processor | Queue only, no processing | N/A (no background task) |

## Verification Checklist

- [x] `ruff check .` passes
- [x] `ruff format --check .` passes
- [x] `mypy .` passes
- [x] `pyright` passes
- [x] `lint-imports` passes
- [x] `pytest --cov` passes with 65%+ coverage
- [x] E2E tests pass (see docs/test_plans/phase-3-e2e-tests.md)
