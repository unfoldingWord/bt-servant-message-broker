# bt-servant-message-broker

Centralized message coordination service for the BT Servant system. Provides per-user message queueing, FIFO ordering guarantees, and session management between client applications and the AI compute engine.

## Problem Statement

The BT Servant system has multiple clients (WhatsApp gateway, web client) that communicate directly with bt-servant-worker. This creates race conditions:

1. Cloudflare Workers with `waitUntil()` spawn independent background tasks with no coordination
2. Multiple retry loops race to acquire the worker's lock, with no ordering guarantee
3. Multi-tab/multi-client scenarios bypass UI protections

This broker solves these problems by providing a centralized queue with per-user FIFO ordering.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Protocol-Specific Layer                          │
│                    (Cloudflare Workers / Vercel)                    │
│                                                                     │
│  bt-servant-whatsapp-gateway     bt-servant-web-client    (future)  │
│  - Meta webhook handling         - HTTP/SSE handling       Telegram │
│  - WhatsApp message parsing      - NextAuth                Discord  │
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
│                                                                      │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
                           ▼

┌──────────────────────────────────────────────────────────────────────┐
│              bt-servant-worker (Cloudflare Workers + DOs)            │
│                                                                      │
│  - AI orchestration (parallel across users)                          │
│  - MCP tool execution                                                │
│  - Chat history storage                                              │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

## Implementation Status

This project is being implemented in phases. See [docs/prd.md](docs/prd.md) for full details.

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Project Setup | ✅ Complete |
| 2 | Core Queue Logic (Redis) | 🔲 Not started |
| 3 | Worker Integration | 🔲 Not started |
| 4 | SSE Stream Proxying | 🔲 Not started |
| 5 | Client Migration Issues | 🔲 Not started |
| 6 | Production Deployment | 🔲 Not started |

### Phase 1 (Current)

Project foundation with:
- FastAPI app with `/health`, `/api/v1/message`, `/api/v1/queue/{user_id}` endpoints (stub implementations)
- Pydantic models matching the API spec
- API key authentication middleware
- Full tooling: ruff, mypy, pyright, import-linter, bandit, pytest
- CI/CD: GitHub Actions for testing and Fly.io deployment
- 71% test coverage

## Technology Stack

- **Runtime**: Python 3.12 + FastAPI
- **Queue Storage**: Redis (Upstash)
- **Deployment**: Fly.io
- **CI/CD**: GitHub Actions

## Development

### Prerequisites

- Python 3.12+
- Redis (for local development, once Phase 2 is implemented)

### Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies (including dev tools)
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install --hook-type commit-msg --hook-type pre-commit --hook-type pre-push
```

### Running Locally

```bash
# Start the server
uvicorn bt_servant_message_broker.main:app --reload

# Or use the module directly
python -m bt_servant_message_broker.main
```

### Running Tests

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=bt_servant_message_broker --cov-report=term-missing
```

### Code Quality

All checks run automatically via pre-commit hooks:

```bash
# Linting and formatting
ruff check .
ruff format .

# Type checking
mypy .
pyright

# Architecture validation (enforces api → services → models layering)
lint-imports

# Security scanning
bandit -r src/bt_servant_message_broker
```

## API Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| POST | `/api/v1/message` | Submit a message for processing | Stub |
| GET | `/api/v1/queue/{user_id}` | Get queue status for a user | Stub |
| GET | `/api/v1/stream` | SSE stream endpoint (proxied) | Not implemented |
| GET | `/health` | Health check with statistics | ✅ Working |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379` |
| `WORKER_BASE_URL` | bt-servant-worker URL | `http://localhost:8787` |
| `WORKER_API_KEY` | API key for worker auth | (empty) |
| `BROKER_API_KEY` | API key for client auth | (empty) |
| `LOG_LEVEL` | Logging level | `INFO` |
| `HOST` | Server bind host | `0.0.0.0` |
| `PORT` | Server bind port | `8000` |

## Deployment

Deployed to Fly.io automatically via GitHub Actions when changes are pushed to main.

```bash
# Manual deploy (if needed)
flyctl deploy

# Check deployment status
flyctl status

# View logs
flyctl logs
```

## License

MIT
