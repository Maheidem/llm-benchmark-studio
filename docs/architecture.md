# Architecture

## System Overview

LLM Benchmark Studio is a multi-user SaaS platform built with a FastAPI backend serving a Vue 3 single-page application. The backend uses a modular router architecture with centralized job management and WebSocket-based real-time updates.

```
                    Vue 3 SPA (Vite + Pinia + Tailwind CSS)
                         |
                    HTTP + WebSocket
                         |
                  FastAPI (app.py orchestrator)
                   /     |      \        \
            routers/  auth.py  db.py  job_registry.py
          (20 modules)   |       |         |
               |      JWT/bcrypt SQLite  ws_manager.py
          job_handlers.py        |         |
               |         data/benchmark_studio.db
           LiteLLM              (16 tables)
               |
         LLM Providers (10+ supported)
```

## Core Files

| File | Purpose | Lines |
|------|---------|-------|
| `app.py` | FastAPI orchestrator -- lifespan, middleware, logging, router registration | ~580 |
| `benchmark.py` | Core benchmark engine -- Target, RunResult, run_single, CLI | ~980 |
| `db.py` | Database layer -- schema (16 tables), CRUD, DatabaseManager | ~1660 |
| `auth.py` | Authentication -- JWT (24h access, 7d refresh), bcrypt, login rate limiting | ~390 |
| `keyvault.py` | Fernet encryption for API key storage | ~66 |
| `provider_params.py` | 3-tier parameter registry (10 providers), validation, clamping | ~720 |
| `job_registry.py` | JobRegistry singleton -- background jobs, queuing, concurrency limits | ~400 |
| `job_handlers.py` | Handler functions for 6 job types (benchmark, tool_eval, param_tune, prompt_tune, judge, judge_compare) | ~1890 |
| `ws_manager.py` | WebSocket ConnectionManager -- per-user connections, multi-tab, admin broadcast | ~90 |
| `schemas.py` | Pydantic request/response models for all API endpoints | ~216 |
| `config.yaml` | Default provider/model configuration | ~130 |
| `routers/` | 20 FastAPI router modules (6,480 lines total) | see below |
| `frontend/src/` | Vue 3 SPA -- 8 stores, 7 composables, 50+ components | see below |
| `tests/` | 20 test files, ~6,700 lines, ~405 tests | see below |

### Router Modules

| Router | Endpoints | Lines |
|--------|-----------|-------|
| `helpers.py` | Shared utilities (scoring, target selection, rate limiting) | ~1080 |
| `tool_eval.py` | `/api/tool-eval/*` -- eval execution, run management | ~1046 |
| `judge.py` | `/api/judge/*` -- LLM-as-judge, comparative analysis | ~460 |
| `experiments.py` | `/api/experiments/*` -- experiment CRUD, timeline | ~420 |
| `prompt_tune.py` | `/api/prompt-tune/*` -- prompt tuning orchestration | ~387 |
| `export_import.py` | `/api/export/*`, `/api/import/*` -- data portability | ~357 |
| `discovery.py` | `/api/discovery/*` -- model discovery, LM Studio detection | ~330 |
| `config.py` | `/api/config/*` -- provider/model CRUD, custom pricing | ~328 |
| `admin.py` | `/api/admin/*` -- users, audit log, rate limits, logs | ~319 |
| `mcp.py` | `/api/mcp/*` -- MCP server tool import | ~250 |
| `analytics.py` | `/api/analytics/*` -- leaderboard, trends, comparison | ~227 |
| `websocket.py` | `/ws` -- WebSocket connection lifecycle | ~199 |
| `benchmark.py` | `/api/benchmark` -- benchmark submission | ~195 |
| `jobs.py` | `/api/jobs/*` -- job listing, cancellation | ~173 |
| `param_tune.py` | `/api/param-tune/*` -- parameter tuning submission | ~160 |
| `schedules.py` | `/api/schedules/*` -- scheduled benchmark CRUD | ~149 |
| `env.py` | `/api/env` -- environment variable management | ~130 |
| `keys.py` | `/api/keys/*` -- API key vault management | ~93 |
| `settings.py` | `/api/settings/*` -- per-user settings storage | ~75 |
| `auth.py` | `/api/auth/*` -- login, register, refresh, logout | ~30 |
| `onboarding.py` | `/api/onboarding/*` -- onboarding completion | ~27 |

### Frontend Architecture

| Directory | Contents |
|-----------|----------|
| `stores/` | 8 Pinia stores: `auth`, `benchmark`, `config`, `judge`, `notifications`, `paramTuner`, `promptTuner`, `toolEval` |
| `composables/` | 7 composables: `useWebSocket`, `useToast`, `useModal`, `useChartTheme`, `useProviderColors`, `useActiveSession`, `useSharedContext` |
| `views/` | Page components: `BenchmarkPage`, `ToolEvalPage`, `AnalyticsPage`, `HistoryPage`, `SchedulesPage`, `SettingsPage`, `AdminPage`, `LandingPage` |
| `components/` | 40+ reusable components organized by domain: `benchmark/`, `tool-eval/`, `analytics/`, `admin/`, `auth/`, `layout/`, `schedules/`, `settings/`, `ui/` |
| `router/` | Vue Router with history mode, route guards for auth |

## Data Flow

### Benchmark Execution

```
User selects models + params in Vue SPA
        |
        v
POST /api/benchmark (validated by Pydantic BenchmarkRequest)
        |
        v
routers/benchmark.py: validate, build params
        |
        v
job_registry.submit("benchmark", user_id, params)
        |
        v
JobRegistry checks per-user concurrency limit
        |
  [under limit]                [at limit]
        |                          |
   Start immediately          Queue (status: "queued")
   (status: "running")        Wait for slot to open
        |
        v
job_handlers.benchmark_handler()
        |
        v
Build targets from user config, inject per-user API keys
        |
        v
Group targets by provider
        |
        v
asyncio.create_task() per provider group (parallel)
    |
    |-- Provider A: model1 -> model2 -> model3 (sequential)
    |-- Provider B: model4 -> model5 (sequential)
    |-- Provider C: model6 (sequential)
    |
    v
Results flow through asyncio.Queue
        |
        v
WebSocket broadcast to all user tabs (interleaved results)
        |
        v
Results saved to DB (benchmark_runs) + JSON file
```

### Tool Eval Execution

```
User selects suite + models in Vue SPA
        |
        v
POST /api/tool-eval (validated by Pydantic ToolEvalRequest)
        |
        v
job_registry.submit("tool_eval", user_id, params)
        |
        v
job_handlers.tool_eval_handler()
        |
        v
Load suite tools + test cases from DB
        |
        v
Group targets by provider (parallel across providers)
    |
    |-- For each model: run all test cases (sequential)
    |   |
    |   |-- litellm.acompletion() with tools (non-streaming)
    |   |-- Extract tool_calls from response
    |   |-- Score: tool_selection + param_accuracy -> overall
    |
    v
Results via asyncio.Queue -> WebSocket broadcast
        |
        v
Per-model summaries computed
        |
        v
Optional: Judge evaluation (live inline or post-eval)
        |
        v
Saved to tool_eval_runs table
        |
        v
If experiment_id: auto-update baseline/best score
```

### WebSocket Event Flow

```
Vue SPA                                    FastAPI
  |                                          |
  |--- WS connect (/ws?token=JWT) --------->|
  |                                          |--- Validate JWT
  |                                          |--- ConnectionManager.connect()
  |<-- { type: "connected" } ---------------|
  |                                          |
  |--- Submit benchmark via REST ---------->|
  |                                          |--- JobRegistry.submit()
  |<-- { type: "job_created" } -------------|
  |<-- { type: "job_started" } -------------|
  |<-- { type: "benchmark_init" } ---------|
  |<-- { type: "benchmark_progress" } -----|    (repeated per model/run)
  |<-- { type: "benchmark_result" } -------|
  |<-- { type: "job_progress" } ------------|    (% complete)
  |<-- { type: "job_completed" } ----------|
  |                                          |
  |--- ping ------>|                         |
  |<-- pong -------|                         |
```

### Job Types

| Type | Handler | WebSocket Events |
|------|---------|-----------------|
| `benchmark` | `benchmark_handler` | `benchmark_init`, `benchmark_progress`, `benchmark_result` |
| `tool_eval` | `tool_eval_handler` | `tool_eval_init`, `tool_eval_progress`, `tool_eval_result`, `tool_eval_summary`, `tool_eval_complete` |
| `param_tune` | `param_tune_handler` | `tune_start`, `combo_result`, `tune_complete` |
| `prompt_tune` | `prompt_tune_handler` | `tune_start`, `generation_start`, `prompt_generated`, `prompt_eval_start`, `prompt_eval_result`, `generation_complete`, `tune_complete` |
| `judge` | `judge_handler` | `judge_start`, `judge_verdict`, `judge_report`, `judge_complete` |
| `judge_compare` | `judge_compare_handler` | `compare_start`, `compare_case`, `compare_complete` |

## Concurrency Model

### Job Registry

- **Per-user concurrency limits**: Configurable via `rate_limits` table (default: 1 concurrent job)
- **Queuing**: Excess jobs queued with FIFO ordering; auto-started when slots free up
- **State machine**: `pending -> queued -> running -> done/failed/cancelled/interrupted`
- **Validated transitions**: Invalid state transitions are logged but don't block execution
- **Timeout watchdog**: Background task checks every 60s for timed-out jobs (default: 2h)
- **Startup recovery**: On server restart, all running/pending/queued jobs marked as `interrupted`
- **Cancellation**: Via `asyncio.Event` -- handlers check `cancel_event.is_set()` between operations

### WebSocket Manager

- **Per-user connections**: Up to 5 concurrent WebSocket connections per user (multi-tab)
- **Message delivery**: Broadcast to ALL tabs for a user via `send_to_user()`
- **Admin broadcast**: `broadcast_to_admins()` for system-wide notifications
- **Dead connection cleanup**: Automatic removal on send failure
- **Connection limit**: Rejects connections beyond limit with code 4008

### Benchmark Execution

- **Cross-provider**: Fully parallel via `asyncio.create_task()` per provider group
- **Within provider**: Sequential model execution (avoids API self-contention)
- **Result collection**: `asyncio.Queue` for non-blocking result aggregation
- **Cancellation**: `cancel_event` checked between runs; tasks cancelled on cancel

### Background Scheduler

- Single background task (`_run_scheduler`) checks for due schedules every 60 seconds
- Scheduled benchmarks run sequentially, one at a time
- Uses per-user API keys and configuration

## Authentication

```
Register -> bcrypt hash -> store in users table
         -> issue access token (24h JWT)
         -> issue refresh token (7-day JWT, HttpOnly cookie)
         -> first user auto-promoted to admin

Login -> verify bcrypt hash
      -> check login rate limiter (5 attempts / 5 min / IP, 15 min lockout)
      -> issue new tokens
      -> audit log entry

API Request -> extract Bearer token from Authorization header
            -> decode JWT (access or cli type)
            -> load user from DB
            -> inject into route handler via Depends()

Token Refresh -> validate refresh token from HttpOnly cookie
              -> verify token exists in DB (not revoked)
              -> issue new access token (refresh token stays the same)

Admin routes -> get_current_user + check role == "admin"

ADMIN_EMAIL env var -> auto-promote matching user on startup
```

## Database

**SQLite** with **aiosqlite** for async access and **WAL mode** for concurrent reads.

### 16 Tables

```
users
  |-- refresh_tokens (CASCADE)
  |-- user_api_keys (CASCADE)
  |-- user_configs (CASCADE)
  |-- benchmark_runs (CASCADE)
  |-- rate_limits (CASCADE)
  |-- audit_log (user_id nullable, survives user deletion)
  |-- tool_suites (CASCADE)
  |     |-- tool_test_cases (CASCADE)
  |     |-- experiments (CASCADE)
  |-- tool_eval_runs (CASCADE, FK to tool_suites + experiments)
  |-- param_tune_runs (CASCADE, FK to tool_suites + experiments)
  |-- prompt_tune_runs (CASCADE, FK to tool_suites + experiments)
  |-- judge_reports (FK to tool_eval_runs + experiments)
  |-- schedules
  |-- jobs (CASCADE)
```

| Table | Purpose |
|-------|---------|
| `users` | User accounts with email, bcrypt hash, role (user/admin) |
| `refresh_tokens` | Hashed refresh tokens with expiry |
| `user_api_keys` | Fernet-encrypted API keys per user per provider |
| `user_configs` | Per-user YAML config (providers, models) |
| `benchmark_runs` | Benchmark results with prompt, tiers, results JSON |
| `rate_limits` | Per-user rate limits (benchmarks/hour, max concurrent, max runs) |
| `audit_log` | All significant user actions with timestamps, IPs |
| `tool_suites` | Tool definitions (OpenAI function calling schema) |
| `tool_test_cases` | Test cases with expected tool/params, scoring config |
| `tool_eval_runs` | Eval results with per-model summaries |
| `param_tune_runs` | Parameter tuning results with search space and best config |
| `prompt_tune_runs` | Prompt tuning results with generations and best prompt |
| `judge_reports` | Judge verdicts and cross-case analysis |
| `experiments` | Experiment metadata with baseline/best tracking |
| `schedules` | Recurring benchmark schedules |
| `jobs` | Universal job tracker (7 types, 7 statuses) |

### Key Design Decisions

- **DatabaseManager singleton**: Centralized connection management with `fetch_one`, `fetch_all`, `execute` methods -- eliminates repeated connect/row_factory/commit patterns
- **Per-user config in DB**: Each user's provider/model configuration stored as YAML in `user_configs`
- **Fernet encryption**: User API keys encrypted at rest, decrypted only when needed for LLM calls
- **Audit log preservation**: Audit entries survive user deletion (user_id nullable)
- **UUID primary keys**: All tables use randomly generated hex IDs
- **Schema migrations**: `try/except` with `ALTER TABLE` for backward-compatible additions

## LiteLLM Integration

All LLM calls go through [LiteLLM](https://docs.litellm.ai/), which provides a unified API across providers:

- **Benchmarks**: `litellm.completion()` with `stream=True`, 120s timeout, 0 retries
- **Tool evals**: `litellm.acompletion()` (non-streaming, to capture tool_calls)
- **Model IDs**: Follow LiteLLM conventions with provider prefixes (`anthropic/`, `gemini/`, etc.)
- **API keys**: Passed per-call via the `api_key` parameter (decrypted from vault)

### Provider Parameter Handling

The `provider_params.py` module implements a three-tier parameter system:

1. **Tier 1 (Universal)**: `temperature`, `max_tokens`, `stop` -- supported by all providers
2. **Tier 2 (Common)**: `top_p`, `top_k`, `frequency_penalty`, `presence_penalty`, `seed`, `reasoning_effort` -- with provider-specific ranges, support flags, and conflict rules
3. **Tier 3 (Passthrough)**: Provider-specific parameters that bypass validation (e.g., `repetition_penalty`, `min_p`, `mirostat`)

Parameters are validated, clamped to valid ranges, and conflict-resolved before being passed to LiteLLM. The philosophy is "warn, don't drop" -- user-requested params pass through unless there is a hard conflict (e.g., Anthropic's mutual exclusion of temperature + top_p).

## Security

- **Content Security Policy**: Restricts script/style/font/connect sources
- **CORS**: Disabled by default, configurable via `CORS_ORIGINS` env var
- **Error sanitization**: API keys, Bearer tokens, and sensitive patterns stripped from error messages
- **Non-root Docker**: Application runs as dedicated `bench` user
- **HttpOnly cookies**: Refresh tokens stored in HttpOnly, SameSite=Strict cookies
- **Rate limiting**: Per-user benchmark rate limits + login attempt rate limiting with lockout
- **Security headers**: X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy
- **Request logging**: JSON-formatted structured logs with request IDs, user IDs, durations
- **In-memory log buffer**: Ring buffer (2000 entries) accessible via admin API endpoint
