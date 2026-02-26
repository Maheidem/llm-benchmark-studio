# LLM Benchmark Studio

**Multi-user SaaS platform for benchmarking LLM providers -- measure speed, evaluate tool calling, tune parameters and prompts, all from one dashboard.**

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Vue 3](https://img.shields.io/badge/Vue%203-4FC08D?logo=vuedotjs&logoColor=white)
![LiteLLM](https://img.shields.io/badge/LiteLLM-powered-blue)

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [CLI Usage](#cli-usage)
- [Web Dashboard](#web-dashboard)
- [Configuration](#configuration)
- [Supported Providers](#supported-providers)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [API Surface](#api-surface)
- [Database Schema](#database-schema)
- [Testing](#testing)
- [Docker Deployment](#docker-deployment)
- [CI/CD Pipeline](#cicd-pipeline)
- [Contributing](#contributing)
- [License](#license)

---

## Features

### Speed Benchmarking
- **Multi-provider parallel execution** -- benchmark OpenAI, Anthropic, Google Gemini, and any LiteLLM-compatible endpoint side by side
- **Streaming metrics** -- output tokens/sec, input tokens/sec (prefill speed), time-to-first-token (TTFT), total latency
- **Context tier stress testing** -- measure performance degradation across context sizes (1K to 150K+ tokens)
- **Statistical rigor** -- multiple runs with std dev, min/max, p50/p95, IQR outlier detection, configurable warm-up runs
- **Cost tracking** -- per-request cost estimates via LiteLLM pricing + custom per-model pricing overrides

### Tool Calling Evaluation
- Define tool suites using OpenAI function calling JSON schema
- Build test cases with expected tools and parameters
- Score tool selection accuracy and parameter correctness (exact, fuzzy, contains, semantic modes)
- Multi-turn chain evaluation with mock tool responses
- Import tools directly from MCP servers
- Irrelevance detection for off-topic responses

### LLM-as-Judge
- Use one model to evaluate another model's tool calling quality
- Live inline scoring during eval runs or post-eval batch analysis
- Comparative judging between two eval runs
- Cross-case analysis with overall grades and recommendations
- Auto-judge on failure with configurable threshold

### Parameter Tuner
- Grid search across temperature, top_p, top_k, frequency_penalty, and provider-specific parameters
- Provider-aware validation with automatic clamping and conflict resolution (10 providers)
- Per-model search spaces with deduplication of resolved parameter combos
- Search space presets (save, load, delete) and built-in vendor presets
- Bayesian optimization via Optuna integration

### Prompt Tuner
- Quick mode (single generation) or evolutionary mode (multi-generation with selection)
- Meta-model generates prompt variations, target models evaluate them
- Automatic best-result promotion to experiments
- Prompt version registry with lineage tracking

### Experiments
- Group related eval, param tune, prompt tune, and judge runs into experiments
- Timeline view of all runs within an experiment
- Automatic baseline and best-score tracking

### Analytics and History
- Benchmark history browser with search and filtering
- Leaderboard rankings by speed, cost, and quality
- Public leaderboard support
- Trend analysis over configurable time periods (7d, 30d, 90d, all)
- CSV and JSON export/import for all data

### Scheduling
- Automated recurring benchmark runs with configurable intervals
- Model selection and parameter customization per schedule
- Results saved to history with schedule metadata

### Platform Features
- **Multi-user authentication** -- JWT access tokens (24h) + refresh tokens (7-day HttpOnly cookie) + OAuth support
- **Per-user API key vault** -- Fernet-encrypted storage, managed via web UI or API
- **Per-user configuration** -- providers, models, prompts stored independently per user
- **Admin dashboard** -- user management, audit logs, rate limits, active job monitoring
- **Real-time updates** -- WebSocket push for all job types (multi-tab support, auto-reconnect)
- **Job management** -- centralized JobRegistry with per-user concurrency limits and queuing
- **Onboarding wizard** -- guided setup for new users
- **Password reset** -- email-based reset flow with rate limiting (SMTP or dev-mode logging)
- **Model profiles** -- save and reuse per-model parameter configurations
- **SEO** -- robots.txt, sitemap.xml, CSP headers, security headers

---

## Quick Start

### Prerequisites

- Python 3.10+ (3.13 recommended)
- [uv](https://docs.astral.sh/uv/) package manager
- Node.js 22+ (only for frontend development)

### Setup

```bash
# 1. Clone the repository
git clone <repo-url> && cd llm-benchmark-studio

# 2. Install Python dependencies
uv sync

# 3. Configure API keys
cp .env.example .env
# Edit .env and add your provider API keys

# 4. Launch the server
python app.py
# Open http://localhost:8501
```

The first registered user is automatically promoted to admin.

### Custom Host and Port

```bash
python app.py --port 3333              # custom port
python app.py --host 0.0.0.0           # bind all interfaces
python app.py --host 0.0.0.0 --port 80 # both
```

### Frontend Development (optional)

To work on the Vue frontend with hot-reload:

```bash
cd frontend && npm install && npm run dev   # Vite dev server on :5173
cd frontend && npm run build                # Build to static/ for production
```

---

## CLI Usage

The CLI tool (`benchmark.py`) runs benchmarks directly from the terminal against all providers defined in `config.yaml`.

```bash
# Run all configured models (3 runs each, with warm-up)
python benchmark.py

# Filter by provider or model name (substring match)
python benchmark.py --provider openai
python benchmark.py --model GLM

# Custom runs, tokens, temperature, and prompt
python benchmark.py --runs 5 --max-tokens 1024 --temperature 0.5
python benchmark.py --prompt "Write a merge sort in Python"

# Stress test across context tiers
python benchmark.py --context-tiers 0,5000,50000,100000

# Skip warm-up or result saving
python benchmark.py --no-warmup --no-save

# Remote mode: delegate to server API with JWT auth
python benchmark.py --token <JWT> --server http://localhost:8501

# Debug LiteLLM calls
python benchmark.py --verbose
```

### CLI Options Reference

| Flag | Description | Default |
|---|---|---|
| `--config` | Path to config YAML file | `config.yaml` |
| `--runs` | Number of runs per model | `3` |
| `--provider` | Filter by provider (substring) | all |
| `--model` | Filter by model (substring) | all |
| `--prompt` | Override benchmark prompt | from config |
| `--max-tokens` | Max output tokens | `512` |
| `--temperature` | Sampling temperature | `0.7` |
| `--context-tiers` | Comma-separated context sizes | `0` |
| `--no-warmup` | Skip warm-up run | false |
| `--no-save` | Skip saving results to JSON | false |
| `--verbose` | Show LiteLLM debug output | false |
| `--token` | JWT for remote API mode | - |
| `--server` | Server URL for remote mode | `http://localhost:8501` |

Results are saved as timestamped JSON files in `results/`.

---

## Web Dashboard

The Vue 3 SPA provides the following pages:

| Page | What it does |
|---|---|
| **Benchmark** | Select models, configure prompt/params, run benchmarks with real-time progress charts |
| **Tool Eval** | Create tool suites, define test cases, run evaluations with optional judge |
| **Analytics** | Leaderboard, trend charts, side-by-side comparison |
| **History** | Browse past benchmark runs, expand details, compare runs |
| **Schedules** | Create and manage recurring benchmark schedules |
| **Settings** | Add/edit providers and models, manage API keys, configure per-model parameters |
| **Admin** | User management, audit logs, rate limits, active jobs (admin only) |

### Frontend Stack

- **Vue 3** SPA with Vite, Vue Router, Tailwind CSS
- **10 Pinia stores**: auth, benchmark, config, judge, notifications, paramTuner, profiles, promptLibrary, promptTuner, toolEval
- **7 composables**: useActiveSession, useChartTheme, useModal, useProviderColors, useSharedContext, useToast, useWebSocket
- **Chart.js** with vue-chartjs for data visualization

---

## Configuration

### config.yaml

All default configuration lives in `config.yaml`:

```yaml
defaults:
  max_tokens: 512
  temperature: 0.7
  context_tiers: [0, 1000, 5000, 10000, 20000, 50000, 100000, 150000]
  prompt: "Explain the concept of recursion..."

prompt_templates:
  code_generation:
    category: code
    label: Generate Sorting Algorithm
    prompt: "Write a Python function that implements merge sort..."

providers:
  openai:
    display_name: OpenAI
    api_key_env: OPENAI_API_KEY       # references .env variable
    models:
      - id: gpt-5.2
        display_name: GPT-5.2
        context_window: 128000
        skip_params: [temperature]     # omit unsupported params
```

**Provider-level fields:** `display_name`, `api_base` (custom endpoints), `api_key_env` (env var reference), `api_key` (inline value), `model_id_prefix` (auto-prepended to model IDs).

**Model-level fields:** `id`, `display_name`, `context_window`, `max_output_tokens`, `skip_params`, `input_cost_per_mtok`, `output_cost_per_mtok`, `system_prompt`.

### Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `JWT_SECRET` | Secret key for JWT signing | Auto-generated (warns in prod) |
| `FERNET_KEY` | Master key for API key encryption | Auto-generated to `data/.fernet_key` |
| `ADMIN_EMAIL` | Auto-promote this email to admin on startup | - |
| `ADMIN_PASSWORD` | Create admin account on startup (with ADMIN_EMAIL) | - |
| `LOG_LEVEL` | Logging verbosity (`debug`, `info`, `warning`, `error`) | `warning` |
| `CORS_ORIGINS` | Comma-separated allowed CORS origins | disabled |
| `COOKIE_SECURE` | Set Secure flag on auth cookies | `false` |
| `APP_VERSION` | Version string for `/healthz` | `dev` |
| `BENCHMARK_RATE_LIMIT` | Max benchmarks per hour per user | `2000` |
| `SMTP_HOST` | SMTP server for password reset emails | - (dev mode logs URL) |
| `SMTP_PORT` | SMTP server port | `587` |
| `SMTP_USER` | SMTP username | - |
| `SMTP_PASSWORD` | SMTP password | - |
| `SMTP_FROM` | Sender email address | `noreply@benchmark.local` |
| `APP_BASE_URL` | App URL for email links | `http://localhost:8501` |
| `LOG_ACCESS_TOKEN` | Token for `/api/admin/logs` endpoint | - |

### Provider API Keys

Set these in `.env` (referenced by `api_key_env` in config.yaml):

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...
ZAI_API_KEY=...
```

Users can also store API keys per-provider via the web UI Settings page. These are Fernet-encrypted in the database and injected at runtime.

---

## Supported Providers

LLM Benchmark Studio uses [LiteLLM](https://docs.litellm.ai/) as its LLM gateway, supporting 100+ providers. The provider parameter registry covers 10 providers with full validation:

| Provider | Model Prefix | Notes |
|---|---|---|
| OpenAI | *(none)* | GPT-5.2, GPT-5.1-Codex, o-series |
| Anthropic | `anthropic/` | Claude Opus, Sonnet, Haiku |
| Google Gemini | `gemini/` | Gemini 3 Pro, Flash |
| ZAI GLM | `zai/` | GLM-4.7, GLM-4.5-Air, GLM-5 |
| LM Studio | `lm_studio/` | Local models via OpenAI-compatible API |
| Ollama | `ollama/` | Local models |
| Together | `together_ai/` | Together AI hosted models |
| DeepSeek | `deepseek/` | DeepSeek models |
| Cohere | `cohere_chat/` | Command models |
| xAI | `xai/` | Grok models |
| Any LiteLLM provider | varies | Add via `config.yaml` |

The 3-tier parameter system handles provider differences:
- **Tier 1 (Universal):** temperature, max_tokens, stop
- **Tier 2 (Common):** top_p, top_k, frequency_penalty, presence_penalty, seed, reasoning_effort
- **Tier 3 (Provider-Specific):** JSON passthrough for any LiteLLM-supported parameter

---

## Architecture

```
                    Vue 3 SPA (Vite + Pinia + Tailwind)
                         |
                    HTTP + WebSocket
                         |
                  FastAPI (app.py orchestrator)
                   /     |      \        \
            routers/  auth.py  db.py  job_registry.py
           (24 modules)  |       |         |
               |      JWT/bcrypt SQLite  ws_manager.py
           LiteLLM              |         |
               |         data/benchmark_studio.db
         LLM Providers         (18+ tables)
```

### Key Data Flow

`config.yaml` defines providers and models. `build_targets()` resolves them into `Target` objects. The web dashboard submits jobs via the REST API, which are managed by the `JobRegistry`. Job handlers execute benchmark/eval/tune logic using `litellm.completion(stream=True)` with a 120-second timeout and zero retries. Results are broadcast in real time via WebSocket to all connected tabs for the user.

### Concurrency Model

Provider groups execute in parallel via `asyncio.create_task()`. Models within a provider run sequentially to avoid self-contention. The `JobRegistry` enforces per-user concurrency limits and queues excess jobs. A watchdog task detects timed-out jobs every 60 seconds.

### Job Types

The `JobRegistry` manages 8 background job types:

| Job Type | Description |
|---|---|
| `benchmark` | Speed benchmarking across models |
| `tool_eval` | Tool calling accuracy evaluation |
| `judge` | LLM-as-judge post-eval analysis |
| `judge_compare` | Comparative judging between two runs |
| `param_tune` | Parameter grid search optimization |
| `prompt_tune` | Prompt variation generation and testing |
| `scheduled_benchmark` | Automated recurring benchmarks |
| `prompt_auto_optimize` | Automatic prompt optimization |

### Security

- **Authentication:** JWT access tokens (HS256, 24h expiry) + refresh tokens (7-day HttpOnly cookies)
- **Password hashing:** bcrypt
- **API key encryption:** Fernet symmetric encryption (master key from env var or auto-generated file)
- **Login rate limiting:** IP-based, 5 attempts per 5 minutes, 15-minute lockout
- **Security headers:** CSP, X-Frame-Options (DENY), X-Content-Type-Options, Referrer-Policy, Permissions-Policy
- **Input validation:** Pydantic models for all request/response schemas
- **Audit logging:** All auth events, sensitive operations logged with IP and user agent
- **RBAC:** `user` and `admin` roles; admin auto-promotion via `ADMIN_EMAIL` env var

---

## Project Structure

```
llm-benchmark-studio/
├── app.py                  # FastAPI orchestrator, lifespan, middleware, logging
├── benchmark.py            # Core benchmark engine (Target, RunResult, CLI)
├── auth.py                 # JWT + bcrypt auth, refresh tokens, rate limiting
├── db.py                   # SQLite + aiosqlite, 18+ tables, WAL mode
├── keyvault.py             # Fernet-encrypted API key storage
├── provider_params.py      # 3-tier param registry (10 providers), validation
├── job_registry.py         # JobRegistry singleton: background jobs, queuing
├── job_handlers.py         # Handler functions for 8 job types
├── ws_manager.py           # WebSocket ConnectionManager (multi-tab, reconnect)
├── schemas.py              # Pydantic request/response models
├── mailer.py               # SMTP email for password reset (stdlib smtplib)
├── config.yaml             # Default provider/model configuration
├── routers/                # 24 FastAPI router modules
│   ├── auth.py             # /api/auth/* endpoints
│   ├── keys.py             # /api/keys/* (encrypted API key management)
│   ├── onboarding.py       # /api/onboarding/*
│   ├── oauth.py            # /api/oauth/* (OAuth flow)
│   ├── benchmark.py        # /api/benchmark
│   ├── config.py           # /api/config/*
│   ├── env.py              # /api/env
│   ├── tool_eval.py        # /api/tool-eval/*
│   ├── param_tune.py       # /api/param-tune/*
│   ├── prompt_tune.py      # /api/prompt-tune/*
│   ├── judge.py            # /api/judge/*
│   ├── experiments.py      # /api/experiments/*
│   ├── analytics.py        # /api/analytics/*
│   ├── admin.py            # /api/admin/*
│   ├── jobs.py             # /api/jobs/*
│   ├── schedules.py        # /api/schedules/*
│   ├── settings.py         # /api/settings/*
│   ├── profiles.py         # /api/profiles/*
│   ├── prompt_versions.py  # /api/prompt-versions/*
│   ├── leaderboard.py      # /api/leaderboard/*
│   ├── discovery.py        # /api/discovery/*
│   ├── mcp.py              # /api/mcp/*
│   ├── export_import.py    # /api/export/*, /api/import/*
│   ├── websocket.py        # /ws
│   ├── helpers.py          # Shared utilities (scoring, parsing, filtering)
│   └── __init__.py         # Router registration (all_routers list)
├── frontend/               # Vue 3 SPA (Vite + Pinia + Tailwind CSS)
│   └── src/
│       ├── App.vue
│       ├── main.js
│       ├── router/         # Vue Router configuration
│       ├── stores/         # 10 Pinia stores
│       ├── composables/    # 7 composables (useWebSocket, useToast, etc.)
│       ├── views/          # Page components
│       └── components/     # Reusable UI components
├── tests/                  # 31 test files, 988 tests
│   ├── conftest.py         # Fixtures: test DB, auth tokens, sample data
│   ├── test_api_contracts.py
│   ├── test_scoring.py
│   ├── test_provider_params.py
│   ├── test_job_registry.py
│   ├── test_ws_manager.py
│   └── ...
├── Dockerfile              # Multi-stage build (Node 22 + Python 3.13)
├── docker-compose.yml      # Single-service compose for local/production
├── pyproject.toml          # Dependencies (uv)
├── .env.example            # Template for environment variables
├── .github/workflows/      # CI/CD: test -> build -> staging -> prod
└── results/                # Timestamped JSON benchmark results
```

---

## API Surface

The backend exposes 24 routers organized into functional groups:

| Group | Routers | Key Endpoints |
|---|---|---|
| **Auth** | auth, keys, onboarding, oauth | `/api/auth/register`, `/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout`, `/api/auth/me`, `/api/auth/forgot-password`, `/api/auth/reset-password`, `/api/keys/*`, `/api/oauth/*` |
| **Benchmark** | benchmark, config, env | `/api/benchmark`, `/api/config/*`, `/api/env` |
| **Eval & Tune** | tool_eval, param_tune, prompt_tune | `/api/tool-eval/*`, `/api/param-tune/*`, `/api/prompt-tune/*` |
| **Analysis** | judge, analytics, experiments, leaderboard | `/api/judge/*`, `/api/analytics/*`, `/api/experiments/*`, `/api/leaderboard/*` |
| **Platform** | jobs, schedules, settings, admin, profiles, prompt_versions | `/api/jobs/*`, `/api/schedules/*`, `/api/settings/*`, `/api/admin/*`, `/api/profiles/*`, `/api/prompt-versions/*` |
| **Infra** | websocket, discovery, export_import, mcp | `/ws`, `/api/discovery/*`, `/api/export/*`, `/api/import/*`, `/api/mcp/*` |

### Infrastructure Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/healthz` | GET | Health check (returns `{"status": "ok", "version": "..."}`) |
| `/robots.txt` | GET | Search engine directives |
| `/sitemap.xml` | GET | XML sitemap |
| `/` | GET | Serves the Vue SPA |
| `/{path}` | GET | SPA catch-all for Vue Router history mode |

All API endpoints require JWT authentication via `Authorization: Bearer <token>` header, except `/api/auth/register`, `/api/auth/login`, `/api/auth/forgot-password`, `/api/auth/reset-password`, `/healthz`, and the public leaderboard.

---

## Database Schema

SQLite database with WAL mode, stored at `data/benchmark_studio.db`. Key tables:

| Category | Tables |
|---|---|
| **Users & Auth** | `users`, `refresh_tokens`, `password_reset_tokens`, `user_api_keys`, `user_configs`, `rate_limits`, `audit_log` |
| **Benchmarks** | `benchmark_runs`, `experiments` |
| **Tool Eval** | `tool_suites`, `tool_test_cases`, `tool_eval_runs` |
| **Tuning** | `param_tune_runs`, `prompt_tune_runs`, `prompt_versions` |
| **Platform** | `schedules`, `judge_reports`, `jobs`, `model_profiles` |

All tables use `TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16))))` for IDs, with foreign keys and CASCADE deletes enforced via `PRAGMA foreign_keys=ON`. The `DatabaseManager` class provides a centralized connection manager with `busy_timeout=5000` for concurrent access.

---

## Testing

```bash
# Run all tests (~988 tests, ~2 seconds)
uv run pytest

# Run a specific test file
uv run pytest tests/test_scoring.py -v

# Run tests matching a name pattern
uv run pytest -k "test_benchmark" -v

# Run with output
uv run pytest -s -v
```

### Test Architecture

- **Framework:** pytest with pytest-asyncio
- **HTTP testing:** async TestClient via `httpx.ASGITransport`
- **No external API calls** -- all LiteLLM calls are mocked
- **Fixtures** in `conftest.py`: test database, auth tokens, sample data, mock providers
- **31 test files** covering: unit tests (pure functions, scoring, parsing), API contract tests, integration tests, E2E smoke tests
- **CI runs tests** on every push and pull request

---

## Docker Deployment

### Quick Deploy

```bash
# Build and run with Docker Compose
docker compose up --build

# Stop
docker compose down
```

### Dockerfile

Multi-stage build:
1. **Stage 1 (Node 22 Alpine):** Builds the Vue frontend via `npm run build`
2. **Stage 2 (Python 3.13 Slim):** Installs Python deps via `uv sync`, copies app code and built frontend assets, runs as non-root user

### docker-compose.yml

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
      - ADMIN_EMAIL=${ADMIN_EMAIL:-}
      - LOG_LEVEL=${LOG_LEVEL:-warning}
    restart: unless-stopped
```

The `data/` volume is critical -- it contains the SQLite database and the auto-generated Fernet encryption key. Back up `data/` regularly in production.

### Health Check

The container includes a built-in health check hitting `GET /healthz` every 30 seconds.

---

## CI/CD Pipeline

Defined in `.github/workflows/ci.yml`:

| Trigger | Pipeline | Deploy Target |
|---|---|---|
| Push to `main` | test -> build -> smoke test -> deploy | Staging (port 8502, tag `:main`) |
| Push tag `v*.*.*` | test -> build -> smoke test -> deploy | Production (port 8501, tag `:major.minor`) |
| Pull request | test -> build -> smoke test | No deploy |

### Details

- **Container registry:** `ghcr.io/maheidem/llm-benchmark-studio`
- **Deploy method:** Portainer CE REST API (pull image, stop stack, remove containers, start stack)
- **Test levels:**
  - Level 1: API contracts and unit tests (no secrets required)
  - Level 2: E2E smoke tests (requires `ZAI_API_KEY`, runs only on `main` push)

---

## Contributing

### Development Setup

```bash
# Install all dependencies (including dev)
uv sync

# Run the test suite
uv run pytest

# Start the backend
python app.py

# Start the frontend dev server (separate terminal)
cd frontend && npm install && npm run dev
```

### Code Conventions

- **Backend:** Async-first Python (always use `async/await`). All database access through the `DatabaseManager` context manager. Pydantic models for request/response validation. Job-based operations go through `job_registry.submit()`.
- **Frontend:** Vue 3 Composition API. Pinia for state management. Tailwind CSS for styling. WebSocket events follow the pattern `{"type": "job_type", "status": "...", "data": {...}}`.
- **Testing:** All new features require tests. Mock all external API calls. Use fixtures from `conftest.py`.

### Adding a New LLM Provider

1. Add the provider block to `config.yaml` with `display_name`, `api_key_env`, and model list
2. If the provider needs parameter validation rules, add an entry to `PROVIDER_REGISTRY` in `provider_params.py`
3. Set the API key in `.env`
4. The provider will be available in both the CLI and web UI immediately

---

## License

MIT
