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
uv run pytest                                  # Run all (988 tests, 31 files, ~2s)
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
├── db.py                   # DatabaseManager, SQLite+aiosqlite, 18+ tables, WAL mode
├── auth.py                 # JWT + bcrypt auth, refresh tokens, RBAC (user/admin)
├── keyvault.py             # Fernet-encrypted API key storage per user
├── job_registry.py         # JobRegistry singleton: background jobs, queuing, concurrency
├── job_handlers.py         # Handler functions for each job type (8 types)
├── ws_manager.py           # WebSocket ConnectionManager (multi-tab, reconnect)
├── provider_params.py      # 3-tier param registry (10 providers), validate/build kwargs
├── schemas.py              # Pydantic request/response models
├── config.yaml             # Provider & model definitions
├── routers/                # 24 FastAPI routers + helpers.py shared utilities
├── frontend/src/           # Vue 3 SPA (Vite + Pinia + Tailwind)
│   ├── stores/             # 10 Pinia stores
│   ├── composables/        # 7 composables (useWebSocket, useToast, useModal, etc.)
│   ├── views/              # Page components (Benchmark, ToolEval, Analytics, etc.)
│   └── components/         # Reusable UI components
├── tests/                  # 31 test files, 988 tests, pytest + async TestClient
├── .github/workflows/      # CI/CD: ci.yml (build -> staging -> prod)
└── results/                # Timestamped JSON benchmark results
```

## Architecture

### Backend Stack
- **FastAPI + Uvicorn** with modular routers (24 modules in `routers/`)
- **SQLite + aiosqlite** (WAL mode), 18+ tables, `DatabaseManager` context manager
- **JobRegistry** -- singleton managing background jobs with per-user concurrency limits, asyncio tasks, SQLite persistence, WebSocket status broadcasts. 8 job types: benchmark, tool_eval, param_tune, prompt_tune, judge, judge_compare, scheduled_benchmark, prompt_auto_optimize
- **WebSocket ConnectionManager** -- real-time push for job status, multi-tab support, auto-reconnect
- **Auth** -- JWT access tokens + refresh tokens, bcrypt passwords, `ADMIN_EMAIL` env var for auto-promotion, rate limiting
- **Keyvault** -- per-user Fernet-encrypted API key storage
- **provider_params.py** -- 3-tier param registry (10 providers), identifies supported params, validates ranges, builds LiteLLM kwargs

### Frontend Stack
- **Vue 3** SPA with Vite, Pinia state management, Vue Router, Tailwind CSS
- 10 Pinia stores, 7 composables, component-based architecture
- WebSocket integration for real-time job updates and notifications

### Key Data Flow
```
config.yaml -> build_targets() -> JobRegistry.submit() -> job_handlers
                                                           |
                                              WebSocket broadcast -> Vue frontend
                                                           |
                                              SQLite persistence -> results/
```

### Concurrency Model
Provider groups execute in parallel via `asyncio.create_task()`. Models within a provider run sequentially (avoids self-contention). JobRegistry enforces per-user concurrency limits and queues excess jobs.

## API Surface (24 routers)

| Group | Routers | Key endpoints |
|-------|---------|---------------|
| Auth | `auth`, `keys`, `onboarding` | `/api/auth/*`, `/api/keys/*` |
| Benchmark | `benchmark`, `config`, `env` | `/api/benchmark`, `/api/config/*`, `/api/env` |
| Eval & Tune | `tool_eval`, `param_tune`, `prompt_tune` | `/api/tool-eval/*`, `/api/param-tune/*` |
| Analysis | `judge`, `analytics`, `experiments`, `prompt_versions` | `/api/judge/*`, `/api/analytics/*` |
| Platform | `jobs`, `schedules`, `settings`, `admin` | `/api/jobs/*`, `/api/schedules/*` |
| Infra | `websocket`, `discovery`, `export_import`, `mcp` | `/ws`, `/api/discovery/*` |
| Helpers | `helpers` | Shared utilities for routers |

## Database (18+ tables)

Core: `users`, `refresh_tokens`, `user_api_keys`, `user_configs`, `rate_limits`, `audit_log`, `password_reset_tokens`
Benchmarks: `benchmark_runs`, `experiments`, `model_profiles`
Tool Eval: `tool_suites`, `tool_test_cases`, `tool_eval_runs`
Tuning: `param_tune_runs`, `prompt_tune_runs`, `prompt_versions`
Platform: `schedules`, `judge_reports`, `jobs`

## Configuration

- **`config.yaml`** -- Providers, models, defaults. Each provider has `api_base`, `api_key_env`, optional `model_prefix`, model list with `id`, `display_name`, `context_window`, `skip_temperature`
- **`.env`** -- API keys referenced by `api_key_env`. Managed via web UI or CLI
- **`ADMIN_EMAIL`** -- env var, auto-promotes matching user to admin role

## LiteLLM Provider Patterns

All LLM calls use `litellm.completion(stream=True)` with 120s timeout, 0 retries. Model ID conventions:
- OpenAI: `gpt-5.2` (no prefix)
- Anthropic: `anthropic/claude-opus-4-6` (`skip_temperature: true`)
- Google: `gemini/gemini-3-pro-preview`
- Local: `lm_studio/model-name` with custom `api_base`

## Design Patterns

### Architectural
| Pattern | Where | Purpose |
|---------|-------|---------|
| Singleton | `DatabaseManager`, `JobRegistry`, `KeyVault`, `ConnectionManager` | Module-level instances, single point of access |
| Producer-Consumer | `registry.submit()` -> `job_handlers.py` | Decoupled job submission from execution |
| Observer | `ws_manager.send_to_user()`, `broadcast_to_admins()` | WebSocket push to all user tabs / admin events |
| State Machine | `JobStatus` enum + `VALID_TRANSITIONS` dict | Enforces PENDING -> QUEUED -> RUNNING -> DONE/FAILED/CANCELLED/INTERRUPTED |
| Middleware Pipeline | `SecurityHeadersMiddleware`, `LoggingMiddleware`, `CORSMiddleware` | Layered request processing in app.py |

### Structural
| Pattern | Where | Purpose |
|---------|-------|---------|
| Adapter/Bridge | `PROVIDER_REGISTRY` in `provider_params.py` | Adapts 10 providers to unified 3-tier param interface via `build_litellm_kwargs()` |
| Repository | `db.py` | 80+ SQL functions behind named methods (`create_*`, `get_*`, `update_*`, `delete_*`) |
| Decorator | `Depends(auth.get_current_user)` | Auth injection across all routers |

### Behavioral
| Pattern | Where | Purpose |
|---------|-------|---------|
| Handler Registry | `job_registry.register_handler(job_type, handler_fn)` | Maps 8 job types to async handler functions |
| Strategy | tool_eval scoring, judge strategies | Multiple scoring (exact, fuzzy, contains, semantic), judge modes (cross-case, multi-turn) |

### Concurrency
| Pattern | Where | Purpose |
|---------|-------|---------|
| Async Task Management | `asyncio.Task` in `_running` dict | Each job runs as cancellable task via `asyncio.Event` |
| Lock Pattern | `asyncio.Lock` on `_user_slots`, `_connections` | Protects shared state in job_registry and ws_manager |
| Parallel Provider Execution | `asyncio.create_task()` per provider group | Providers parallel, models within provider sequential |

## Software Patterns

### Layered Architecture
```
app.py (entrypoint, middleware) -> routers/ (HTTP, validation) -> job_handlers.py (business logic) -> benchmark.py (engine) -> db.py (data)
```

### Naming Conventions
- Async functions: `async def` everywhere
- DB functions: `create_*`, `get_*`, `update_*`, `delete_*`, `get_*_by_*` for lookups
- Private: `_` prefix for internals (`_get_user_config`, `_check_rate_limit`, `_log_buffer`)

### Error Handling
- Global `ValidationError` handler -> 422 responses
- `sanitize_error()` strips API keys from error messages (regex for `sk-*`, `gsk_*`, `sk-ant-*`)
- Job recovery on startup: orphaned running jobs marked as interrupted

### Configuration Hierarchy
- Env vars via `os.environ.get(key, default)` (JWT_SECRET, FERNET_KEY, ADMIN_EMAIL, etc.)
- YAML config for providers/models (`config.yaml`)
- Per-user config in `user_configs` table, retrieved via `_get_user_config(user_id)`
- 3-tier param registry: Tier 1 (universal), Tier 2 (common), Tier 3 (provider-specific passthrough)

### Database Access
- `DatabaseManager` wraps `aiosqlite.connect()` with WAL mode, `busy_timeout=5000`, `foreign_keys=ON`
- Row factory: `aiosqlite.Row` -> `dict(row)` conversion
- ID generation: `DEFAULT (lower(hex(randomblob(16))))` for UUIDs
- No connection pool -- per-call connections (sufficient for single-instance)

### Auth and Security
- JWT: HS256, 24h access tokens, 7-day refresh tokens in HttpOnly cookies
- Passwords: bcrypt with `gensalt()`
- API keys: Fernet symmetric encryption (keyvault.py)
- Refresh tokens: SHA-256 hashed before DB storage
- Rate limiting: IP-based, 5 attempts / 5 min window, 15 min lockout
- RBAC: user/admin roles, ADMIN_EMAIL auto-promotion
- Dependency injection: `Depends(get_current_user)` for auth, module-level injection for `ws_manager`

## Test Patterns

### Organization
- 31 test files in `tests/`, 988 tests total, runs in ~2s
- Naming: `test_*.py`, classes `Test*`, methods `test_*`
- Categories: unit (pure functions), API contracts, integration, E2E smoke

### Fixtures (conftest.py)
| Fixture | Scope | Purpose |
|---------|-------|---------|
| `event_loop` | session | Single event loop for all async tests |
| `_temp_db_dir` | session | Isolated temp directory for test DB |
| `_patch_db_path` | session | Monkeypatches `db.DB_PATH` before app import |
| `_init_test_db` | session | Initializes schema once per session |
| `app_client` | session | Ready-to-use HTTP client |
| `test_user`, `test_admin` | session | Pre-authenticated test accounts |

### Mocking Strategy
- **LiteLLM**: mock `litellm.completion()` -- NO real API calls (except E2E smoke)
- **Database**: real test SQLite DB (not mocked) -- tests actual SQL, constraints, FKs
- **HTTP**: FastAPI TestClient via `httpx.ASGITransport` -- real async HTTP
- **Config**: `test_config.yaml` fixture provides dummy providers

### Async Testing
- `@pytest.mark.asyncio` on all async tests
- `@pytest_asyncio.fixture` for async fixtures
- Session-scoped event loop for performance

### Assertion Patterns
- HTTP status codes: `assert resp.status_code == 200/401/422/429`
- Response body: `assert "access_token" in data`
- DB state: `user = await db.get_user_by_email(...); assert user is not None`
- Field presence: `assert all(key in result for key in [...])`

## Product Patterns

### Job-Based Architecture (core product pattern)
1. HTTP endpoint -> `registry.submit(job_type, user_id, params)` -> returns job_id immediately
2. JobRegistry watchdog picks up job -> transitions PENDING -> RUNNING -> executes handler
3. Handler calls `progress_cb()` -> WebSocket push -> frontend updates in real-time
4. Completion -> job marked DONE, result persisted to DB

8 job types: `benchmark`, `tool_eval`, `judge`, `judge_compare`, `param_tune`, `prompt_tune`, `scheduled_benchmark`, `prompt_auto_optimize`

Per-user concurrency limiting with FIFO queue for overflow.

### Real-Time WebSocket Pattern
- Endpoint: `/ws` (JWT or query param auth)
- Multi-tab: max 5 connections per user
- Auto-reconnect: server sends `reconnect` init event with current job state
- Message format: `{"type": "benchmark_result", "job_id": "...", "data": {...}}`
- Broadcasting: `send_to_user()` for per-user, `broadcast_to_admins()` for admin events

### Multi-User Isolation
- Every DB query scoped to `user_id` parameter
- Foreign keys with ON DELETE CASCADE
- Per-user config, API keys, job history all isolated
- Admin-only endpoints for cross-user visibility

### Configuration-Driven Extensibility
- New providers: add to `config.yaml` (no code changes)
- New job types: register handler in app.py lifespan + add Pydantic schema + add route
- New routers: add file to `routers/`, register in `__init__.py`

### Audit and Persistence
- `audit_log` table: user_id, action, resource_type, detail JSON, IP, user-agent (90-day retention)
- Results stored as JSON blobs in DB for flexibility
- Experiment tracking groups related runs with best-score tracking

## CI/CD Pipeline

- **GitHub Actions** (`ci.yml`): build -> smoke test -> deploy
- **Registry**: `ghcr.io/maheidem/llm-benchmark-studio`
- **Staging**: push to `main` -> auto-deploy (port 8502, tag `:main`)
- **Production**: push tag `v*.*.*` -> deploy (port 8501, tag `:major.minor`)
- **Deploy method**: Portainer CE REST API (pull -> stop -> remove -> start)
- **Docker**: multi-stage build, healthcheck at `/healthz`

## Development Notes

- Always use `async/await` -- the entire backend is async-first
- Use `DatabaseManager` context manager for all DB access (`async with db.DatabaseManager() as d:`)
- Pydantic models in `schemas.py` for all request/response validation
- Job-based operations go through `job_registry.submit()`, not direct execution
- WebSocket events follow pattern: `{"type": "job_type", "status": "...", "data": {...}}`
- Frontend builds to `static/` directory, served by FastAPI in production

## Quality Gate (MANDATORY)

**Every feature follows the journey-first workflow. No exceptions.**

### The Rule
Before writing ANY feature code (routers, views, stores, job handlers, schemas), a user journey document MUST exist in `.documentation/user-journeys/{tab}/`. The PreToolUse hook will **block** edits to feature files without journey docs.

### Feature Workflow (journey-first)
```
0. /define-journey {tab}/{name}  -> journey doc (preconditions, steps, success criteria)
1. solutions-architect           -> design doc (files, endpoints, schemas, events)
2. fastapi-backend + vue-frontend -> implement (parallel when API contract defined)
3. /generate-tests               -> Playwright E2E test from journey doc
4. /validate                     -> run tests against REAL providers (Zai GLM)
5. product-owner                 -> acceptance check
6. librarian                     -> update docs and memory
```

### Quality Gate Commands
| Command | Phase | Purpose |
|---------|-------|---------|
| `/define-journey` | 0 | Create journey doc BEFORE code |
| `/audit-journeys` | 1-3 | Discover, map, find gaps in existing app |
| `/generate-tests` | 4 | Create Playwright tests from journey docs |
| `/validate` | 5 | Run E2E tests (standalone or in-flow) |

### E2E Test Configuration
- Config: `e2e/.env.test` (gitignored) — provider, model, API key, test account
- Template: `e2e/.env.test.example` (committed)
- Provider: Zai GLM-4.5-Air (real API calls, no mocking)
- Account: one consolidated test account shared across all tests
- Time tolerance: HIGH — real provider tests may take minutes, this is intentional

### Journey Doc Location
```
.documentation/user-journeys/
├── benchmark/          # run, compare, cancel, history
├── tool-eval/
│   ├── suite/          # create, import
│   ├── evaluate/       # run, view results
│   ├── param-tune/     # grid, bayesian
│   ├── prompt-tune/    # run, manage versions
│   ├── judge/          # compare, auto-judge, reports
│   └── auto-optimize/  # run, review iterations
├── analytics/          # leaderboard, compare, export
└── settings/           # api-keys, providers, admin
```

### Gate Config
`.quality-gate.json` in project root defines which files are gated and how they map to tabs. Modify this when adding new routers or feature areas.

## Agent Playbook

This project has 7 specialized agents in `.claude/agents/`. Use them -- don't reinvent what they already know.

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
"How should we build X?" --> solutions-architect (design first)
"Build the backend for X" --> fastapi-backend
"Build the UI for X"      --> vue-frontend
"Add provider/metric X"   --> benchmark-engine
"Write tests for X"       --> qa-engineer
"Does X match the spec?"  --> product-owner
"Update docs after X"     --> librarian
```

### Collaboration Rules

1. **Journey before design** -- Run `/define-journey` FIRST. The quality gate hook blocks feature code edits without journey docs.
2. **Design before code** -- For non-trivial features, run `solutions-architect` first. It produces a design doc that implementation agents follow.
2. **Validate after code** -- After implementation, run `product-owner` to check spec compliance and `qa-engineer` to verify test coverage.
3. **Document after shipping** -- After a feature lands, run `librarian` to update docs and memory files.
4. **Backend + Frontend in parallel** -- `fastapi-backend` and `vue-frontend` can run simultaneously on the same feature when the API contract is defined.
5. **Don't bypass specialists** -- If a task touches routers, use `fastapi-backend`. If it touches stores/views, use `vue-frontend`. Don't use `general-programmer-agent` for work these specialists handle better.
6. **Read-only agents are cheap** -- `solutions-architect` and `product-owner` never modify code. Use them liberally for design reviews and acceptance checks.

### Full Feature Workflow

```
0. /define-journey       -> journey doc (MANDATORY — gate blocks code without it)
1. solutions-architect   -> design doc (files, endpoints, schemas, events)
2. fastapi-backend       -> implements backend (parallel with frontend)
   vue-frontend          -> implements frontend (parallel with backend)
   benchmark-engine      -> if engine/provider changes needed
3. /generate-tests       -> Playwright E2E tests from journey doc
4. /validate             -> run tests against real providers
5. product-owner         -> acceptance check (pass/fail report)
6. librarian             -> updates docs, memory, cross-references
```
