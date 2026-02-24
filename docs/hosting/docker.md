# Docker Setup

LLM Benchmark Studio provides a Docker image for production deployments. The image uses a two-stage build: the Vue 3 frontend is compiled with Node.js, and the resulting static assets are served by the Python backend.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/maheidem/llm-benchmark-studio.git
cd llm-benchmark-studio

# Configure environment
cp .env.example .env
# Edit .env with your API keys and JWT_SECRET

# Start with Docker Compose
docker compose up -d
```

The application is available at `http://localhost:8501`.

## Docker Compose Configuration

The default `docker-compose.yml`:

```yaml
services:
  benchmark:
    build: .
    ports:
      - "8501:8501"
    volumes:
      - ./data:/app/data        # Persist SQLite DB and Fernet key
      - ./.env:/app/.env:ro     # API keys (read-only)
    environment:
      - JWT_SECRET=${JWT_SECRET:-change-me-in-production}
      - BENCHMARK_RATE_LIMIT=${BENCHMARK_RATE_LIMIT:-2000}
      - COOKIE_SECURE=${COOKIE_SECURE:-false}
      - LOG_LEVEL=${LOG_LEVEL:-warning}
      - ADMIN_EMAIL=${ADMIN_EMAIL:-}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8501/healthz')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

## Volumes

| Volume | Container Path | Description |
|--------|---------------|-------------|
| `./data` | `/app/data` | SQLite database and Fernet encryption key |
| `./.env` | `/app/.env` | Environment variables (read-only mount) |

!!! danger "Back Up the Data Volume"
    The `data/` directory contains:

    - `benchmark_studio.db` -- All user data, benchmark history, configurations
    - `.fernet_key` -- Encryption key for stored API keys

    Both must be backed up. If the Fernet key is lost, user API keys cannot be recovered.

## Dockerfile

The Dockerfile uses a two-stage build to produce a single production image:

```dockerfile
# Stage 1: Build Vue 3 frontend
FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend
FROM python:3.13-slim

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

WORKDIR /app

# Install dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY app.py benchmark.py auth.py db.py keyvault.py \
     provider_params.py job_registry.py job_handlers.py \
     schemas.py ws_manager.py config.yaml \
     migrate_to_multiuser.py ./
COPY routers/ routers/

# Copy built frontend assets from stage 1
COPY --from=frontend-build /static/ static/

# Create data directory
RUN mkdir -p data

# Non-root user
RUN useradd -m -s /bin/bash bench && chown -R bench:bench /app
USER bench

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/healthz')" || exit 1

CMD ["uv", "run", "python", "app.py", "--host", "0.0.0.0"]
```

### Build Stages

**Stage 1 -- Frontend (Node.js 22 Alpine)**:

1. Copies `frontend/package.json` and `frontend/package-lock.json`
2. Runs `npm ci` for deterministic dependency installation
3. Copies the full `frontend/` directory
4. Runs `npm run build` (Vite builds the Vue 3 SPA to `/static/`)

**Stage 2 -- Backend (Python 3.13 Slim)**:

1. Installs the `uv` package manager
2. Copies `pyproject.toml` and `uv.lock`, then runs `uv sync --frozen --no-dev` (cached layer)
3. Copies all Python application files and the `routers/` directory
4. Copies the built frontend assets from stage 1 into `static/`
5. Creates the `data/` directory, sets up a non-root user, and configures the health check

### Key Characteristics

- **Two-stage build** keeps the final image small (no Node.js runtime in production)
- Uses `uv` for fast, deterministic Python dependency installation
- Runs as non-root user `bench`
- Built-in health check on `/healthz`
- Build arg `APP_VERSION` sets the reported application version

## Building the Image

```bash
# Build locally
docker build -t llm-benchmark-studio .

# Build with version tag
docker build --build-arg APP_VERSION=1.2.0 -t llm-benchmark-studio:1.2.0 .
```

## Environment Variables

Pass these as environment variables or in the `.env` file:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JWT_SECRET` | Yes | Auto-generated | JWT signing key |
| `ADMIN_EMAIL` | No | (none) | Auto-promote to admin |
| `ADMIN_PASSWORD` | No | (none) | Auto-create admin account |
| `FERNET_KEY` | No | Auto-generated | Encryption master key |
| `BENCHMARK_RATE_LIMIT` | No | `2000` | Rate limit per hour |
| `COOKIE_SECURE` | No | `false` | `true` for HTTPS |
| `CORS_ORIGINS` | No | (none) | Allowed CORS origins |
| `LOG_LEVEL` | No | `warning` | Log verbosity |

Plus any LLM provider API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.).

## Connecting to Local LLM Servers

To benchmark local models (LM Studio, Ollama, vLLM) from within the Docker container:

```yaml
# In the user's config or config.yaml
providers:
  lm_studio:
    display_name: LM Studio
    api_base: http://host.docker.internal:1234/v1
    api_key: not-needed
    model_id_prefix: lm_studio
    models:
      - id: lm_studio/my-model
        display_name: My Local Model
```

Use `host.docker.internal` to access services running on the Docker host machine.

## Production Recommendations

- Set a strong, persistent `JWT_SECRET`
- Set `FERNET_KEY` explicitly (do not rely on auto-generation)
- Use `COOKIE_SECURE=true` behind HTTPS
- Mount `data/` as a persistent volume
- Set up regular backups of the SQLite database
- Use a reverse proxy (nginx, Caddy, Traefik) for TLS termination and WebSocket proxying
- Set `CORS_ORIGINS` if the frontend is served from a different domain
