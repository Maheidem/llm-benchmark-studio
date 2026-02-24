# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LLM Benchmark Studio is a multi-user SaaS platform for benchmarking LLM providers. It measures token throughput (tokens/sec) and latency (TTFT), evaluates tool-use accuracy, tunes parameters and prompts, and provides analytics dashboards. Two interfaces: CLI (`benchmark.py`) and a Vue 3 SPA web dashboard.

## Commands

### Setup & Run
```bash
uv sync                                       # Install dependencies
cp .env.example .env                           # Add API keys
python app.py                                  # Start server (default :8501)
python app.py --host 0.0.0.0 --port 3333       # Custom host/port
```

### CLI Benchmarks
```bash
python benchmark.py                            # All providers, all models
python benchmark.py --provider openai --runs 3
python benchmark.py --model GLM --runs 1
python benchmark.py --context-tiers 0,5000,50000
python benchmark.py --max-tokens 1024 --temperature 0.5
python benchmark.py --no-save --verbose
```

### Tests
```bash
uv run pytest                                  # Run all (~405 tests, ~4s)
uv run pytest tests/test_benchmark.py -v       # Single file
uv run pytest -k "test_scoring" -v             # By name pattern
```

### Frontend (development)
```bash
cd frontend && npm install && npm run dev      # Vite dev server
cd frontend && npm run build                   # Build to static/
```

### Docker
```bash
docker compose up --build                      # Full stack
docker compose down
```

## Project Structure

```
├── app.py                  # FastAPI app, lifespan, middleware, logging
├── benchmark.py            # Core engine: Target, RunResult, run_single(), build_targets()
├── db.py                   # DatabaseManager, SQLite+aiosqlite, 16 tables, WAL mode
├── auth.py                 # JWT + bcrypt auth, refresh tokens, RBAC (user/admin)
├── keyvault.py             # Fernet-encrypted API key storage per user
├── job_registry.py         # JobRegistry singleton: background jobs, queuing, concurrency
├── job_handlers.py         # Handler functions for each job type (7 types)
├── ws_manager.py           # WebSocket ConnectionManager (multi-tab, reconnect)
├── provider_params.py      # 3-tier param registry (10 providers), validate/build kwargs
├── schemas.py              # Pydantic request/response models
├── config.yaml             # Provider & model definitions
├── routers/                # 22 FastAPI routers (see API Surface below)
├── frontend/src/           # Vue 3 SPA (Vite + Pinia + Tailwind)
│   ├── stores/             # 8 Pinia stores (auth, benchmark, config, judge, etc.)
│   ├── composables/        # 7 composables (useWebSocket, useToast, useModal, etc.)
│   ├── views/              # Page components (Benchmark, ToolEval, Analytics, etc.)
│   └── components/         # Reusable UI components
├── tests/                  # 20 test files, ~6700 lines, pytest + async TestClient
├── .github/workflows/      # CI/CD: ci.yml (build → staging → prod)
└── results/                # Timestamped JSON benchmark results
```

## Architecture

### Backend Stack
- **FastAPI + Uvicorn** with modular routers (22 modules in `routers/`)
- **SQLite + aiosqlite** (WAL mode), 16 tables, `DatabaseManager` context manager
- **JobRegistry** — singleton managing background jobs with per-user concurrency limits, asyncio tasks, SQLite persistence, WebSocket status broadcasts. 7 job types: benchmark, tool_eval, param_tune, prompt_tune, judge, schedule, discovery
- **WebSocket ConnectionManager** — real-time push for job status, multi-tab support, auto-reconnect
- **Auth** — JWT access tokens + refresh tokens, bcrypt passwords, `ADMIN_EMAIL` env var for auto-promotion, rate limiting
- **Keyvault** — per-user Fernet-encrypted API key storage
- **provider_params.py** — 3-tier param registry (10 providers), identifies supported params, validates ranges, builds LiteLLM kwargs

### Frontend Stack
- **Vue 3** SPA with Vite, Pinia state management, Vue Router, Tailwind CSS
- 8 Pinia stores, 7 composables, component-based architecture
- WebSocket integration for real-time job updates and notifications

### Key Data Flow
```
config.yaml → build_targets() → JobRegistry.submit() → job_handlers
                                                           ↓
                                              WebSocket broadcast → Vue frontend
                                                           ↓
                                              SQLite persistence → results/
```

### Concurrency Model
Provider groups execute in parallel via `asyncio.create_task()`. Models within a provider run sequentially (avoids self-contention). JobRegistry enforces per-user concurrency limits and queues excess jobs.

## API Surface (22 routers)

| Group | Routers | Key endpoints |
|-------|---------|---------------|
| Auth | `auth`, `keys`, `onboarding` | `/api/auth/*`, `/api/keys/*` |
| Benchmark | `benchmark`, `config`, `env` | `/api/benchmark`, `/api/config/*`, `/api/env` |
| Eval & Tune | `tool_eval`, `param_tune`, `prompt_tune` | `/api/tool-eval/*`, `/api/param-tune/*` |
| Analysis | `judge`, `analytics`, `experiments` | `/api/judge/*`, `/api/analytics/*` |
| Platform | `jobs`, `schedules`, `settings`, `admin` | `/api/jobs/*`, `/api/schedules/*` |
| Infra | `websocket`, `discovery`, `export_import`, `mcp` | `/ws`, `/api/discovery/*` |
| Helpers | `helpers` | Shared utilities for routers |

## Database (16 tables)

Core: `users`, `refresh_tokens`, `user_api_keys`, `user_configs`, `rate_limits`, `audit_log`
Benchmarks: `benchmark_runs`, `experiments`
Tool Eval: `tool_suites`, `tool_test_cases`, `tool_eval_runs`
Tuning: `param_tune_runs`, `prompt_tune_runs`
Platform: `schedules`, `judge_reports`, `jobs`

## Configuration

- **`config.yaml`** — Providers, models, defaults. Each provider has `api_base`, `api_key_env`, optional `model_prefix`, model list with `id`, `display_name`, `context_window`, `skip_temperature`
- **`.env`** — API keys referenced by `api_key_env`. Managed via web UI or CLI
- **`ADMIN_EMAIL`** — env var, auto-promotes matching user to admin role

## LiteLLM Provider Patterns

All LLM calls use `litellm.completion(stream=True)` with 120s timeout, 0 retries. Model ID conventions:
- OpenAI: `gpt-5.2` (no prefix)
- Anthropic: `anthropic/claude-opus-4-6` (`skip_temperature: true`)
- Google: `gemini/gemini-3-pro-preview`
- Local: `lm_studio/model-name` with custom `api_base`

## Testing Patterns

- Framework: pytest with async TestClient (`httpx.ASGITransport`)
- No external API calls — all LiteLLM calls mocked
- Fixtures in `conftest.py`: test DB, auth tokens, sample data
- Test categories: unit (pure functions), API contracts, integration, E2E smoke

## CI/CD Pipeline

- **GitHub Actions** (`ci.yml`): build → smoke test → deploy
- **Registry**: `ghcr.io/maheidem/llm-benchmark-studio`
- **Staging**: push to `main` → auto-deploy (port 8502, tag `:main`)
- **Production**: push tag `v*.*.*` → deploy (port 8501, tag `:major.minor`)
- **Deploy method**: Portainer CE REST API (pull → stop → remove → start)
- **Docker**: multi-stage build, healthcheck at `/healthz`

## Development Notes

- Always use `async/await` — the entire backend is async-first
- Use `DatabaseManager` context manager for all DB access (`async with db.DatabaseManager() as d:`)
- Pydantic models in `schemas.py` for all request/response validation
- Job-based operations go through `job_registry.submit()`, not direct execution
- WebSocket events follow pattern: `{"type": "job_type", "status": "...", "data": {...}}`
- Frontend builds to `static/` directory, served by FastAPI in production

## Agent Playbook

This project has 7 specialized agents in `.claude/agents/`. Use them — don't reinvent what they already know.

### Agent Roster

| Agent | Role | Writes Code? | When to Use |
|-------|------|:---:|-------------|
| `solutions-architect` | Designs features using established patterns | No | Before implementing any non-trivial feature |
| `product-owner` | Validates requirements vs implementation | No | After implementation, for acceptance checks |
| `fastapi-backend` | Backend development (routers, DB, jobs, auth) | Yes | Any Python backend work |
| `vue-frontend` | Frontend development (views, stores, composables) | Yes | Any Vue/Tailwind frontend work |
| `benchmark-engine` | Core engine, providers, config, LiteLLM | Yes | Benchmark logic, provider integration, param registry |
| `qa-engineer` | Tests (pytest, fixtures, mocks, coverage) | Yes | Writing/running/fixing tests |
| `librarian` | Docs, memory, cross-references, freshness | Yes | After sprints, doc audits, memory cleanup |

### Decision Flow

```
"How should we build X?" ──→ solutions-architect (design first)
"Build the backend for X" ──→ fastapi-backend
"Build the UI for X"      ──→ vue-frontend
"Add provider/metric X"   ──→ benchmark-engine
"Write tests for X"       ──→ qa-engineer
"Does X match the spec?"  ──→ product-owner
"Update docs after X"     ──→ librarian
```

### Collaboration Rules

1. **Design before code** — For non-trivial features, run `solutions-architect` first. It produces a design doc that implementation agents follow.
2. **Validate after code** — After implementation, run `product-owner` to check spec compliance and `qa-engineer` to verify test coverage.
3. **Document after shipping** — After a feature lands, run `librarian` to update docs and memory files.
4. **Backend + Frontend in parallel** — `fastapi-backend` and `vue-frontend` can run simultaneously on the same feature when the API contract is defined.
5. **Don't bypass specialists** — If a task touches routers, use `fastapi-backend`. If it touches stores/views, use `vue-frontend`. Don't use `general-programmer-agent` for work these specialists handle better.
6. **Read-only agents are cheap** — `solutions-architect` and `product-owner` never modify code. Use them liberally for design reviews and acceptance checks.

### Full Feature Workflow

```
1. solutions-architect  → design doc (files, endpoints, schemas, events)
2. fastapi-backend      → implements backend (parallel with frontend)
   vue-frontend         → implements frontend (parallel with backend)
   benchmark-engine     → if engine/provider changes needed
3. qa-engineer          → writes and runs tests
4. product-owner        → acceptance check (pass/fail report)
5. librarian            → updates docs, memory, cross-references
```
