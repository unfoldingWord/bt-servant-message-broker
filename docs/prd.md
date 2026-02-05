# Plan: bt-servant-message-broker

## Overview

Create **bt-servant-message-broker**, a centralized message coordination service that sits between client applications (WhatsApp gateway, web client, future clients) and the AI compute engine (bt-servant-worker). It provides per-user message queueing, ordering guarantees, and session management.

## Problem Statement

The BT Servant system has multiple clients (bt-servant-whatsapp-gateway, bt-servant-web-client) that communicate directly with bt-servant-worker. This creates race conditions:

1. **Cloudflare Workers with `waitUntil()`** spawn independent background tasks with no coordination
2. **Multiple retry loops race** to acquire the worker's lock, with no ordering guarantee
3. **Multi-tab/multi-client scenarios** bypass UI protections (disabled send button)

Cloudflare Queues don't guarantee FIFO ordering. Cloudflare Containers lack persistent disk. A persistent server process is needed.

## Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Platform | **Fly.io** | Always-on, persistent disk, simpler than CF Containers |
| Language | **Python + FastAPI** | Asyncio handles I/O-bound work well, familiar patterns |
| Persistence | **Redis (Upstash)** | Right tool for queues, atomic list ops, managed on Fly.io |
| Auth | **API key validation** | Defense in depth, broker validates client requests |
| Streaming | **Broker proxies** | All traffic through broker, consistent ordering |
| Repo | `unfoldingWord/bt-servant-message-broker` | Already created at `../bt-servant-message-broker` |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Protocol-Specific Layer                          │
│                    (Cloudflare Workers / Vercel)                    │
│                                                                     │
│  bt-servant-whatsapp-gateway     bt-servant-web-client    (future)  │
│  - Meta webhook handling         - HTTP/SSE handling       Telegram │
│  - Signature validation          - NextAuth                Discord  │
│  - WhatsApp message parsing      - Web UI                  etc.     │
│  - Meta API for responses        - Browser protocols                │
│                                                                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼ (protocol-agnostic messages)

┌──────────────────────────────────────────────────────────────────────┐
│                bt-servant-message-broker (Fly.io)                    │
│                Python + FastAPI + Redis                              │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                Per-User Message Queues (Redis)                  │ │
│  │                                                                 │ │
│  │   user:A:queue → [msg1] → processing                            │ │
│  │   user:B:queue → [msg1, msg2, msg3] → msg1 processing           │ │
│  │   user:C:queue → [] → idle                                      │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  - Per-user FIFO ordering                                            │
│  - API key validation                                                │
│  - SSE stream proxying                                               │
│  - Response routing                                                  │
│                                                                      │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
                           ▼

┌──────────────────────────────────────────────────────────────────────┐
│              bt-servant-worker (Cloudflare Workers + DOs)            │
│                                                                      │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐     │
│  │ DO User A  │  │ DO User B  │  │ DO User C  │  │ DO User D  │     │
│  │ - Claude   │  │ - Claude   │  │ - Claude   │  │ - Claude   │     │
│  │ - MCP      │  │ - MCP      │  │ - MCP      │  │ - MCP      │     │
│  │ - History  │  │ - History  │  │ - History  │  │ - History  │     │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘     │
│                                                                      │
│  - AI orchestration (parallel across users)                          │
│  - MCP tool execution                                                │
│  - Chat history storage                                              │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

## Component Changes

### bt-servant-message-broker (NEW)

**Responsibilities:**
- Receive messages from all clients via unified API
- Maintain per-user message queues in Redis
- Ensure strict FIFO processing per user
- Forward messages to bt-servant-worker one at a time per user
- Proxy SSE streams from worker to clients
- Route responses back to originating client

### bt-servant-whatsapp-gateway (MODIFY)

**Changes:**
- Remove 429 retry logic
- Change `ENGINE_BASE_URL` to point to broker instead of worker
- Simplify to pure protocol translation

### bt-servant-web-client (MODIFY)

**Changes:**
- API routes call broker instead of worker
- Remove any retry logic
- Streaming endpoint proxied through broker

### bt-servant-worker (MINIMAL CHANGES)

**Changes:**
- Remove or simplify 429 lock mechanism (broker guarantees serialization)
- Keep for safety but should never trigger in normal operation

## API Design

### POST /api/v1/message

Submit a message for processing.

```json
{
  "user_id": "string",
  "org_id": "string",
  "message": "string",
  "message_type": "text | audio",
  "audio_base64": "string (optional)",
  "audio_format": "string (optional)",
  "client_id": "whatsapp | web | telegram",
  "client_message_id": "string (optional)",
  "callback_url": "string (optional, for async delivery)"
}
```

**Response (sync):** Full ChatResponse from worker
**Response (async with callback_url):** `{"status": "queued", "queue_position": N}`

### GET /api/v1/stream

SSE stream endpoint - proxies to worker's /stream endpoint.

### GET /api/v1/queue/{user_id}

Queue status for debugging/monitoring.

### GET /health

Health check with queue statistics.

## Project Structure

```
bt-servant-message-broker/
├── src/
│   ├── main.py                 # FastAPI app entry
│   ├── config.py               # Settings (pydantic-settings)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py           # API endpoints
│   │   └── dependencies.py     # Auth, rate limiting
│   ├── services/
│   │   ├── __init__.py
│   │   ├── queue_manager.py    # Redis queue operations
│   │   ├── worker_client.py    # HTTP client for bt-servant-worker
│   │   └── stream_proxy.py     # SSE proxying
│   ├── models/
│   │   ├── __init__.py
│   │   └── messages.py         # Pydantic models
│   └── utils/
│       ├── __init__.py
│       └── logging.py          # Structured logging
├── tests/
│   ├── unit/
│   │   └── test_queue_manager.py
│   └── integration/
│       └── test_api.py
├── docs/
│   └── prd.md                  # Full PRD (copy from this plan)
├── pyproject.toml
├── Dockerfile
├── fly.toml
├── .pre-commit-config.yaml     # Copy patterns from bt-servant-engine
├── .importlinter               # Architecture fitness functions
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
└── README.md
```

## Tooling & Quality Standards

**Reference: `../bt-servant-engine`**

Copy the following patterns and configurations from bt-servant-engine:

1. **Pre-commit hooks** (`.pre-commit-config.yaml`):
   - ruff (linting + formatting)
   - mypy (type checking)
   - pyright (strict type checking)
   - import-linter (architecture fitness functions)
   - conventional commits

2. **Architecture fitness functions** (`.importlinter`):
   - Define layer boundaries (api → services → models)
   - Prevent circular dependencies
   - Enforce separation of concerns

3. **CI checks** (`.github/workflows/ci.yml`):
   - ruff check + format
   - mypy
   - pyright
   - lint-imports
   - pytest with coverage threshold

4. **pyproject.toml structure**:
   - Same dependency management style
   - Same ruff configuration
   - Same mypy configuration
   - Same pytest configuration

## Redis Schema

```
# Per-user queue (list)
user:{user_id}:queue → ["msg_json_1", "msg_json_2", ...]

# Currently processing flag (string with expiry)
user:{user_id}:processing → "msg_id" (TTL: 300s)

# Message metadata (hash)
message:{msg_id} → {
  "user_id": "...",
  "client_id": "...",
  "callback_url": "...",
  "queued_at": "...",
  "started_at": "...",
}
```

## Implementation Phases

### Phase 1: Project Setup
1. Initialize Python project with pyproject.toml
2. Set up FastAPI skeleton
3. Configure Redis connection (Upstash)
4. Create Dockerfile and fly.toml
5. Set up CI/CD workflows

### Phase 2: Core Queue Logic
1. Implement `QueueManager` class with Redis operations
2. Implement per-user FIFO logic
3. Add queue status endpoints
4. Write unit tests

### Phase 3: Worker Integration
1. Implement `WorkerClient` for bt-servant-worker communication
2. Handle sync request/response flow
3. Handle errors and timeouts
4. Write integration tests

### Phase 4: SSE Proxying
1. Implement SSE stream proxying
2. Maintain ordering with streaming requests
3. Test with bt-servant-web-client

### Phase 5: Create GitHub Issues for Client Migration
1. Create detailed GitHub issue in `unfoldingWord/bt-servant-whatsapp-gateway`
2. Create detailed GitHub issue in `unfoldingWord/bt-servant-web-client`
3. Create optional GitHub issue in `unfoldingWord/bt-servant-worker`
4. Issues should be thorough enough for repo bots/maintainers to execute independently

### Phase 6: Production Deployment
1. Deploy to Fly.io
2. Configure Upstash Redis
3. Set up monitoring/alerting
4. Update DNS/routing

## GitHub Issues for Dependent Repos

**IMPORTANT:** Do NOT directly modify bt-servant-whatsapp-gateway or bt-servant-web-client. Instead, create detailed GitHub issues in those repos for the bots/maintainers responsible for those codebases to execute on.

### Issue for bt-servant-whatsapp-gateway

**Title:** Integrate with bt-servant-message-broker for message ordering

**Body should include:**
1. **Context**: Link to this PRD, explain the ordering problem being solved
2. **Changes required**:
   - Update `src/services/engine-client.ts`:
     - Change `ENGINE_BASE_URL` to `BROKER_BASE_URL`
     - Remove 429 retry logic (broker handles ordering)
     - Simplify to single request/response (no retries needed)
   - Update `src/config/types.ts`:
     - Add `BROKER_BASE_URL` env var
     - Add `BROKER_API_KEY` env var
     - Remove `ENGINE_BASE_URL` and `ENGINE_API_KEY` (no longer needed)
   - Update `wrangler.toml`:
     - Update environment variables
   - Update `src/services/message-handler.ts`:
     - Adjust to new broker response format if needed
3. **Testing requirements**:
   - E2E test: send two messages rapidly, verify ordered processing
   - E2E test: verify responses arrive in correct order
4. **Environment variables to set**:
   - `BROKER_BASE_URL`: URL of deployed broker
   - `BROKER_API_KEY`: API key for authenticating to broker

### Issue for bt-servant-web-client

**Title:** Integrate with bt-servant-message-broker for message ordering

**Body should include:**
1. **Context**: Link to this PRD, explain the ordering problem (multi-tab scenario)
2. **Changes required**:
   - Update `src/lib/engine-client.ts`:
     - Change `ENGINE_BASE_URL` to `BROKER_BASE_URL`
     - Update endpoint paths to match broker API
     - Remove any retry logic
   - Update environment variables:
     - `BROKER_BASE_URL` instead of `ENGINE_BASE_URL`
     - `BROKER_API_KEY` instead of `ENGINE_API_KEY`
   - Update streaming endpoint:
     - Point to broker's `/api/v1/stream` endpoint
     - Broker will proxy the SSE stream
3. **Testing requirements**:
   - E2E test: open two tabs, send messages from both, verify ordering
   - E2E test: verify SSE streaming still works through broker
4. **Environment variables to set**:
   - `BROKER_BASE_URL`: URL of deployed broker
   - `BROKER_API_KEY`: API key for authenticating to broker

### Issue for bt-servant-worker (Optional)

**Title:** Simplify 429 lock mechanism (broker now handles serialization)

**Body should include:**
1. **Context**: bt-servant-message-broker now guarantees per-user serialization
2. **Changes required** (optional, for safety can keep as-is):
   - `src/durable-objects/user-session.ts`:
     - Consider removing or simplifying the 429 lock mechanism
     - If kept, it serves as defense-in-depth (should never trigger)
3. **Testing requirements**:
   - Verify worker still functions correctly with broker in front
   - Verify 429 is never returned in normal operation

## Environment Variables

### bt-servant-message-broker
```
REDIS_URL=redis://...
WORKER_BASE_URL=https://bt-servant-worker...
BROKER_API_KEY=...  # For client auth
WORKER_API_KEY=...  # For calling worker
LOG_LEVEL=INFO
```

## Verification Plan

### In bt-servant-message-broker (this repo)
1. **Unit tests**: Queue operations, message ordering, Redis interactions
2. **Integration tests**: Full flow with mocked bt-servant-worker
3. **Load test**: Multiple concurrent users, verify per-user ordering maintained
4. **Health check**: `/health` endpoint returns queue statistics

### E2E Tests (after client repos implement their issues)
5. **E2E test - WhatsApp**: Send messages, verify ordered responses
6. **E2E test - Web**: Send messages from multiple tabs, verify ordering
7. **E2E test - Cross-client**: Send from WhatsApp, then web, verify FIFO

*Note: E2E tests 5-7 require the client repos to complete their integration issues first.*

## Open Questions (Resolved)

| Question | Resolution |
|----------|------------|
| Platform | Fly.io |
| Language | Python + FastAPI |
| Persistence | Redis (Upstash) |
| Streaming | Broker proxies |
| Auth | API key validation |
| Naming | bt-servant-message-broker |

## Deliverables

1. **bt-servant-message-broker** repo with working service
2. **PRD** at `../bt-servant-message-broker/docs/prd.md`
3. **GitHub Issue** in `unfoldingWord/bt-servant-whatsapp-gateway` for broker integration
4. **GitHub Issue** in `unfoldingWord/bt-servant-web-client` for broker integration
5. **GitHub Issue** (optional) in `unfoldingWord/bt-servant-worker` for 429 simplification
6. **README** and **CLAUDE.md** in bt-servant-message-broker
