# E2E Test Plan: Phase 3 - Worker Integration

## Prerequisites

1. Phase 3 code deployed to Fly.io
2. CI passing (all linters, type checkers, unit tests)
3. `WORKER_BASE_URL` and `WORKER_API_KEY` secrets set in Fly.io
4. bt-servant-worker accessible from broker

## Environment Setup

```bash
export BASE_URL="https://bt-servant-message-broker.fly.dev"
export API_KEY="<your-broker-api-key>"
```

## Test Cases

### Test 1: Health Check with Worker Status

**Purpose:** Verify health endpoint shows worker connectivity.

```bash
curl -s "$BASE_URL/health" | jq .
```

**Expected:**
```json
{
  "status": "healthy",
  "redis_connected": true,
  "active_queues": 0,
  "messages_processing": 0,
  "worker_connected": true
}
```

**Pass criteria:** `worker_connected: true`

---

### Test 2: Submit Message - Immediate Processing

**Purpose:** Verify message is processed immediately when user has no queue.

```bash
curl -s -X POST "$BASE_URL/api/v1/message" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "e2e-test-user-1",
    "org_id": "testorg",
    "message": "What is Genesis about?",
    "client_id": "web"
  }' | jq .
```

**Expected:**
```json
{
  "status": "completed",
  "message_id": "<uuid>",
  "responses": ["Genesis is the first book..."],
  "response_language": "en",
  "voice_audio_base64": null
}
```

**Pass criteria:** `status: "completed"` with non-empty `responses` array

---

### Test 3: Queue Status After Processing

**Purpose:** Verify queue is empty after processing completes.

```bash
curl -s "$BASE_URL/api/v1/queue/e2e-test-user-1" \
  -H "X-API-Key: $API_KEY" | jq .
```

**Expected:**
```json
{
  "user_id": "e2e-test-user-1",
  "queue_length": 0,
  "is_processing": false,
  "current_message_id": null
}
```

**Pass criteria:** `queue_length: 0`, `is_processing: false`

---

### Test 4: Multiple Users - Parallel Processing

**Purpose:** Verify different users can process simultaneously.

```bash
# User A
curl -s -X POST "$BASE_URL/api/v1/message" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"e2e-user-A","org_id":"test","message":"Hello A","client_id":"web"}' &

# User B (simultaneously)
curl -s -X POST "$BASE_URL/api/v1/message" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"e2e-user-B","org_id":"test","message":"Hello B","client_id":"web"}' &

wait
```

**Pass criteria:** Both requests return `status: "completed"`

---

### Test 5: Worker Timeout Handling

**Purpose:** Verify timeout errors are handled gracefully.

*Note: This test requires a message that causes worker timeout, or temporarily setting a very low timeout.*

**Expected:** HTTP 504 with appropriate error message

---

### Test 6: Authentication Enforcement

**Purpose:** Verify API key is required for message submission.

```bash
# No API key
curl -s -X POST "$BASE_URL/api/v1/message" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"hacker","org_id":"bad","message":"test","client_id":"web"}' | jq .
```

**Expected:**
```json
{
  "detail": "Missing X-API-Key header"
}
```

**Pass criteria:** Request rejected with 401/403

---

### Test 7: Health Degraded Without Worker

**Purpose:** Verify health shows degraded when worker unavailable.

*Note: Requires temporarily setting invalid WORKER_BASE_URL or stopping worker.*

**Expected:**
```json
{
  "status": "degraded",
  "redis_connected": true,
  "worker_connected": false
}
```

---

## Cleanup

After testing, clear test data:

```bash
# Check for lingering queues
curl -s "$BASE_URL/health" | jq '.active_queues'
```

## Test Summary Template

| Test | Status | Notes |
|------|--------|-------|
| 1. Health with worker | |  |
| 2. Immediate processing | |  |
| 3. Queue empty after | |  |
| 4. Parallel users | |  |
| 5. Timeout handling | |  |
| 6. Auth enforcement | |  |
| 7. Degraded health | |  |

**Overall Result:** PASS / FAIL

**Tester:** _______________
**Date:** _______________
