# Installation

LLM Benchmark Studio can be run locally for development or deployed with Docker for production.

## Prerequisites

- Python 3.10 or higher (3.13 recommended)
- [uv](https://docs.astral.sh/uv/) package manager (recommended) or pip
- Node.js 18+ and npm (for building the frontend)
- At least one LLM provider API key (OpenAI, Anthropic, Google Gemini, etc.)

## Local Installation

### 1. Clone the Repository

```bash
git clone https://github.com/maheidem/llm-benchmark-studio.git
cd llm-benchmark-studio
```

### 2. Install Dependencies

Using uv (recommended):

```bash
uv sync
```

Using pip:

```bash
pip install -r requirements.txt
```

!!! note "Dependencies"
    The project uses `pyproject.toml` with uv lockfile. Key dependencies include:

    - `litellm` -- Unified LLM API
    - `fastapi` + `uvicorn` -- Web framework
    - `aiosqlite` -- Async SQLite
    - `bcrypt` + `python-jose` -- Authentication
    - `cryptography` -- API key encryption
    - `tiktoken` -- Token counting
    - `rich` -- CLI output
    - `httpx` -- HTTP client
    - `mcp` -- Model Context Protocol client

### 3. Build the Frontend

The web dashboard is a Vue 3 SPA that must be compiled before running the server:

```bash
cd frontend && npm ci && npm run build
```

This compiles the frontend into the `static/` directory, which FastAPI serves in production.

!!! note "Docker handles this automatically"
    If you use Docker Compose, the multi-stage Dockerfile builds the frontend automatically. You only need this step for local development without Docker.

### 4. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your API keys:

```bash
# LLM Provider API Keys
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...

# Authentication
JWT_SECRET=change-this-to-a-random-string

# Optional: Auto-create admin account on first startup
# ADMIN_EMAIL=admin@example.com
# ADMIN_PASSWORD=changeme123

# Optional: Encryption key (auto-generated if not set)
# FERNET_KEY=your-base64-fernet-key-here

# Deployment
COOKIE_SECURE=false
LOG_LEVEL=warning
```

### 5. Start the Application

```bash
python app.py
```

The web dashboard is available at `http://localhost:8501`.

Custom host and port:

```bash
python app.py --host 0.0.0.0 --port 3333
```

### Frontend Development Mode

For frontend development with hot-reload, run the Vite dev server alongside the backend:

```bash
# Terminal 1: Backend
python app.py

# Terminal 2: Frontend dev server
cd frontend && npm install && npm run dev
```

The Vite dev server proxies API requests to the backend.

## Docker Installation

See [Docker Setup](../hosting/docker.md) for the full Docker guide.

Quick start with Docker Compose:

```bash
# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start the application
docker compose up -d
```

Docker handles the frontend build automatically via a multi-stage Dockerfile (Node.js builds the Vue 3 SPA, then Python serves it).

The application will be available at `http://localhost:8501`.

## First-Time Setup

1. Open the dashboard at `http://localhost:8501`
2. Register a new account (the first user is automatically promoted to admin)
3. Complete the onboarding wizard to add your API keys
4. You are ready to run benchmarks

!!! tip "Admin Account"
    The first user to register always gets the `admin` role. Alternatively, set `ADMIN_EMAIL` and `ADMIN_PASSWORD` in your `.env` file to auto-create an admin account on startup.

## CLI Usage

The CLI tool (`benchmark.py`) can run benchmarks without the web dashboard:

```bash
python benchmark.py                          # All providers, all models
python benchmark.py --provider openai        # Filter by provider
python benchmark.py --model GPT              # Filter by model name
python benchmark.py --runs 3                 # Average over 3 runs
python benchmark.py --no-save                # Skip saving results
```

See [Running Benchmarks](../guide/benchmarks.md) for full CLI documentation.

## Verify Installation

Check that the application is running:

```bash
curl http://localhost:8501/healthz
```

Expected response:

```json
{"status": "ok", "version": "dev"}
```
