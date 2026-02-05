# bt-servant-message-broker

Centralized message coordination service for the BT Servant system. Provides per-user message queueing, FIFO ordering guarantees, and session management between client applications and the AI compute engine.

## Features

- Per-user message queues with strict FIFO ordering
- API key authentication for client requests
- SSE stream proxying from worker to clients
- Health monitoring with queue statistics

## Architecture

```
Clients (WhatsApp, Web, etc.)
         │
         ▼
   Message Broker (this service)
   - Per-user queues (Redis)
   - FIFO ordering
   - Auth validation
         │
         ▼
   bt-servant-worker
   - AI orchestration
   - MCP tools
```

## Development

### Prerequisites

- Python 3.12+
- Redis (for local development)

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

```bash
# Linting
ruff check .

# Formatting
ruff format .

# Type checking
mypy .
pyright

# Architecture validation
lint-imports

# Security scanning
bandit -r src/bt_servant_message_broker
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/message` | Submit a message for processing |
| GET | `/api/v1/queue/{user_id}` | Get queue status for a user |
| GET | `/api/v1/stream` | SSE stream endpoint (proxied) |
| GET | `/health` | Health check with statistics |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379` |
| `WORKER_BASE_URL` | bt-servant-worker URL | `http://localhost:8787` |
| `WORKER_API_KEY` | API key for worker auth | |
| `BROKER_API_KEY` | API key for client auth | |
| `LOG_LEVEL` | Logging level | `INFO` |
| `HOST` | Server bind host | `0.0.0.0` |
| `PORT` | Server bind port | `8000` |

## Deployment

Deployed to Fly.io via GitHub Actions on push to main.

```bash
# Manual deploy (if needed)
flyctl deploy
```

## License

MIT
