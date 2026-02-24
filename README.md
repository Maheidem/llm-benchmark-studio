# LLM Benchmark Studio

**Multi-user SaaS platform for benchmarking LLM providers -- measure speed, evaluate tool calling, tune parameters and prompts, all from one dashboard.**

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Vue 3](https://img.shields.io/badge/Vue%203-4FC08D?logo=vuedotjs&logoColor=white)
![LiteLLM](https://img.shields.io/badge/LiteLLM-powered-blue)

![Dashboard](screenshot.png)

## Features

### Speed Benchmarking
- **Multi-provider parallel execution** -- benchmark OpenAI, Anthropic, Google Gemini, and any LiteLLM-compatible endpoint side by side
- **Streaming metrics** -- tokens/sec (output), input tokens/sec (prefill), time-to-first-token, total latency
- **Context tier stress testing** -- measure degradation across context sizes (1K to 150K+ tokens)
- **Statistical rigor** -- multiple runs with std dev, min/max, p50/p95, IQR outlier detection, warm-up runs
- **Cost tracking** -- per-request cost estimates via LiteLLM pricing + custom per-model pricing

### Tool Calling Evaluation
- Define tool suites using OpenAI function calling JSON schema
- Build test cases with expected tools and parameters
- Score tool selection accuracy and parameter correctness (exact, fuzzy, contains, semantic)
- Multi-turn chain evaluation with mock tool responses
- Import tools directly from MCP servers

### LLM-as-Judge
- Use one model to evaluate another model's tool calling quality
- Live inline scoring during eval runs or post-eval batch analysis
- Comparative judging between two eval runs
- Cross-case analysis with overall grades and recommendations

### Parameter Tuner
- Grid search across temperature, top_p, top_k, frequency_penalty, and provider-specific parameters
- Provider-aware validation with automatic clamping and conflict resolution (10 providers)
- Per-model search spaces with deduplication of resolved parameter combos
- Search space presets (save, load, delete)

### Prompt Tuner
- Quick mode (single generation) or evolutionary mode (multi-generation with selection)
- Meta-model generates prompt variations, target models evaluate them
- Automatic best-result promotion to experiments

### Experiments
- Group related eval, param tune, prompt tune, and judge runs into experiments
- Timeline view of all runs within an experiment
- Automatic baseline and best-score tracking

### Analytics and History
- Benchmark history browser with search and filtering
- Leaderboard rankings by speed, cost, and quality
- Trend analysis over configurable time periods (7d, 30d, 90d, all)
- CSV and JSON export/import for all data

### Scheduling
- Automated recurring benchmark runs with configurable intervals
- Model selection and parameter customization per schedule
- Results saved to history with schedule metadata

### Platform Features
- **Multi-user authentication** -- JWT access tokens (24h) + refresh tokens (7-day HttpOnly cookie)
- **Per-user API key vault** -- Fernet-encrypted storage, managed via web UI or API
- **Per-user configuration** -- providers, models, prompts stored independently per user
- **Admin dashboard** -- user management, audit logs, rate limits, active job monitoring
- **Real-time updates** -- WebSocket push for all job types (multi-tab support, auto-reconnect)
- **Job management** -- centralized JobRegistry with per-user concurrency limits and queuing
- **Onboarding wizard** -- guided setup for new users
- **SEO** -- robots.txt, sitemap.xml, security headers

## Quick Start

```bash
# 1. Clone
git clone <repo-url> && cd llm-benchmark-studio

# 2. Install dependencies
uv sync

# 3. Configure API keys
cp .env.example .env
# Edit .env and add your keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)

# 4. Launch the server
python app.py
# Open http://localhost:8501
```

The first registered user is automatically promoted to admin.

## CLI Usage

```bash
# Run all configured models (3 runs each, with warm-up)
python benchmark.py

# Filter by provider or model name (substring match)
python benchmark.py --provider openai
python benchmark.py --model GLM

# Custom runs, tokens, and prompt
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

Results are saved as timestamped JSON files in `results/`.

## Web Dashboard

```bash
python app.py                    # http://localhost:8501
python app.py --port 3333        # custom port
python app.py --host 0.0.0.0     # bind all interfaces
```

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

## Configuration

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

Key fields per provider: `api_base` (custom endpoints), `api_key_env` (env var reference), `api_key` (inline), `model_id_prefix` (auto-prepended to model IDs).

Key fields per model: `id`, `display_name`, `context_window`, `max_output_tokens`, `skip_params`, `input_cost_per_mtok`, `output_cost_per_mtok`, `system_prompt`.

### Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `JWT_SECRET` | Secret key for JWT signing | Auto-generated (warn in prod) |
| `FERNET_KEY` | Master key for API key encryption | Auto-generated to `data/.fernet_key` |
| `ADMIN_EMAIL` | Auto-promote this email to admin | - |
| `ADMIN_PASSWORD` | Create admin account on startup | - |
| `LOG_LEVEL` | Logging verbosity | `warning` |
| `CORS_ORIGINS` | Comma-separated allowed origins | disabled |
| `COOKIE_SECURE` | Set Secure flag on cookies | `false` |

## Supported Providers

| Provider | Prefix | Notes |
|---|---|---|
| OpenAI | *(none)* | `gpt-5.2`, `gpt-5.1-codex`, etc. |
| Anthropic | `anthropic/` | Claude Opus, Sonnet, Haiku |
| Google Gemini | `gemini/` | Gemini 3 Pro, Flash |
| ZAI GLM | `zai/` | GLM-4.7, GLM-4.5-Air, GLM-5 |
| LM Studio | `lm_studio/` | Local models via OpenAI-compatible API |
| Ollama | `ollama/` | Local models |
| Mistral | `mistral/` | Mistral models |
| DeepSeek | `deepseek/` | DeepSeek models |
| Cohere | `cohere/` | Command models |
| xAI | `xai/` | Grok models |
| vLLM | `vllm/` | Self-hosted inference |
| Any LiteLLM provider | varies | Add via `config.yaml` |

## Project Structure

```
llm-benchmark-studio/
├── app.py                  # FastAPI orchestrator, lifespan, middleware, logging
├── benchmark.py            # Core benchmark engine (Target, RunResult, CLI)
├── auth.py                 # JWT + bcrypt auth, refresh tokens, rate limiting
├── db.py                   # SQLite + aiosqlite, 16 tables, WAL mode
├── keyvault.py             # Fernet-encrypted API key storage
├── provider_params.py      # 3-tier param registry (10 providers), validation
├── job_registry.py         # JobRegistry singleton: background jobs, queuing
├── job_handlers.py         # Handler functions for 7 job types
├── ws_manager.py           # WebSocket ConnectionManager (multi-tab, reconnect)
├── schemas.py              # Pydantic request/response models
├── config.yaml             # Default provider/model configuration
├── routers/                # 20 FastAPI router modules
│   ├── auth.py             # /api/auth/* endpoints
│   ├── benchmark.py        # /api/benchmark
│   ├── tool_eval.py        # /api/tool-eval/*
│   ├── param_tune.py       # /api/param-tune/*
│   ├── prompt_tune.py      # /api/prompt-tune/*
│   ├── judge.py            # /api/judge/*
│   ├── experiments.py      # /api/experiments/*
│   ├── analytics.py        # /api/analytics/*
│   ├── config.py           # /api/config/*
│   ├── admin.py            # /api/admin/*
│   ├── jobs.py             # /api/jobs/*
│   ├── schedules.py        # /api/schedules/*
│   ├── keys.py             # /api/keys/*
│   ├── settings.py         # /api/settings/*
│   ├── discovery.py        # /api/discovery/*
│   ├── mcp.py              # /api/mcp/*
│   ├── export_import.py    # /api/export/*, /api/import/*
│   ├── websocket.py        # /ws
│   ├── helpers.py          # Shared utilities for routers
│   └── __init__.py         # Router registration
├── frontend/               # Vue 3 SPA (Vite + Pinia + Tailwind CSS)
│   └── src/
│       ├── App.vue
│       ├── main.js
│       ├── router/         # Vue Router configuration
│       ├── stores/         # 8 Pinia stores
│       ├── composables/    # 7 composables (useWebSocket, useToast, etc.)
│       ├── views/          # Page components
│       └── components/     # Reusable UI components
├── tests/                  # 20 test files, ~6,700 lines
│   ├── conftest.py         # Fixtures: test DB, auth tokens, sample data
│   ├── test_api_contracts.py
│   ├── test_scoring.py
│   ├── test_provider_params.py
│   ├── test_job_registry.py
│   ├── test_ws_manager.py
│   └── ...                 # (20 files total)
├── Dockerfile              # Multi-stage build (Node frontend + Python backend)
├── pyproject.toml          # Dependencies (uv)
├── .github/workflows/      # CI/CD: test -> build -> staging -> prod
└── results/                # Timestamped JSON benchmark results
```

## Architecture

```
                    Vue 3 SPA (Vite + Pinia + Tailwind)
                         |
                    HTTP + WebSocket
                         |
                  FastAPI (app.py orchestrator)
                   /     |      \        \
            routers/  auth.py  db.py  job_registry.py
           (20 modules)  |       |         |
               |      JWT/bcrypt SQLite  ws_manager.py
           LiteLLM              |         |
               |         data/benchmark_studio.db
         LLM Providers         (16 tables)
```

**Key data flow:** `config.yaml` defines providers/models. `build_targets()` resolves them into `Target` objects. The web dashboard submits jobs via the REST API, which are managed by the `JobRegistry`. Job handlers execute benchmark/eval/tune logic using `litellm.completion(stream=True)` with a 120s timeout. Results are broadcast in real time via WebSocket to all connected tabs for the user.

**Concurrency model:** Provider groups execute in parallel via `asyncio.create_task()`. Models within a provider run sequentially to avoid self-contention. The `JobRegistry` enforces per-user concurrency limits and queues excess jobs. A watchdog task detects timed-out jobs every 60 seconds.

## Testing

```bash
# Run all tests (~405 tests, ~4 seconds)
uv run pytest

# Run a specific test file
uv run pytest tests/test_scoring.py -v

# Run tests matching a name pattern
uv run pytest -k "test_benchmark" -v
```

Tests use pytest with async TestClient (`httpx.ASGITransport`). All LiteLLM calls are mocked -- no external API calls required. The CI pipeline runs tests on every push and PR.

## Docker

```bash
# Build and run
docker compose up --build

# The Dockerfile uses a multi-stage build:
# Stage 1: Node 22 builds the Vue frontend
# Stage 2: Python 3.13 runs the FastAPI backend
# Frontend assets are served from static/
```

## CI/CD

- Push to `main` triggers: test -> build -> smoke test -> auto-deploy to staging
- Push tag `v*.*.*` triggers: test -> build -> smoke test -> deploy to production
- PRs trigger: test -> build -> smoke test (no deploy)
- Registry: `ghcr.io/maheidem/llm-benchmark-studio`
- Deploy method: Portainer CE REST API

## License

MIT
