# Architecture

## System Overview

LLM Benchmark Studio is a Python-based web application that benchmarks LLM providers for speed and tool calling accuracy. It consists of a FastAPI backend serving a single-file HTML dashboard.

```
                    Browser (index.html)
                         |
                    HTTP / SSE
                         |
                  FastAPI (app.py)
                   /     |      \
          benchmark.py  auth.py  db.py
               |          |        |
           LiteLLM    JWT/bcrypt  SQLite
               |                   |
         LLM Providers        data/benchmark_studio.db
```

## Core Files

| File | Purpose | Lines |
|------|---------|-------|
| `app.py` | FastAPI backend -- all routes, eval engine, scheduler | ~5700 |
| `benchmark.py` | Core benchmark engine -- data structures, run_single, CLI | ~500 |
| `auth.py` | Authentication -- JWT, bcrypt, login rate limiting | ~250 |
| `db.py` | Database layer -- schema, CRUD operations | ~600 |
| `keyvault.py` | Fernet encryption for API key storage | ~60 |
| `provider_params.py` | Provider parameter registry and validation | ~690 |
| `index.html` | Single-file web dashboard (Tailwind CSS + Chart.js) | ~12000 |
| `config.yaml` | Default provider/model configuration | ~130 |

## Data Flow

### Benchmark Execution

```
User selects models + params
        |
        v
POST /api/benchmark (request body: models, runs, prompt, etc.)
        |
        v
Build targets from user config
        |
        v
Inject per-user API keys (user key > global fallback)
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
SSE events streamed to browser (interleaved)
        |
        v
Results saved to DB + JSON file
```

### Tool Eval Execution

```
User selects suite + models
        |
        v
POST /api/tool-eval (suite_id, models, temperature, tool_choice)
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
Results via asyncio.Queue -> SSE stream
        |
        v
Per-model summaries computed
        |
        v
Optional: Judge evaluation (live inline or post-eval)
        |
        v
Saved to tool_eval_runs table
```

## Concurrency Model

### Web Dashboard

- **Cross-provider**: Fully parallel via `asyncio.create_task()`
- **Within provider**: Sequential (avoids API self-contention)
- **Per-user locking**: One active benchmark/eval per user (via `asyncio.Lock`)
- **Cancellation**: `asyncio.Event` per user, checked between runs
- **Queue-based SSE**: Results flow through `asyncio.Queue` to the SSE generator

### Background Scheduler

- Single background task (`_run_scheduler`) checks for due schedules every 60 seconds
- Scheduled benchmarks run sequentially, one at a time
- Uses per-user API keys and configuration

## Authentication

```
Register -> bcrypt hash -> store in users table
         -> issue access token (15min JWT)
         -> issue refresh token (7-day JWT, HttpOnly cookie)

Login -> verify bcrypt hash
      -> check login rate limiter (5 attempts / 5 min / IP)
      -> issue new tokens

API Request -> extract Bearer token from Authorization header
            -> decode JWT (access or cli type)
            -> load user from DB
            -> inject into route handler via Depends()

Admin routes -> get_current_user + check role == "admin"
```

## Database

**SQLite** with **aiosqlite** for async access and **WAL mode** for concurrent reads.

### Table Relationships

```
users
  |-- refresh_tokens (CASCADE)
  |-- user_api_keys (CASCADE)
  |-- user_configs (CASCADE)
  |-- benchmark_runs (CASCADE)
  |-- rate_limits (CASCADE)
  |-- tool_suites (CASCADE)
  |     |-- tool_test_cases (CASCADE)
  |-- tool_eval_runs (CASCADE)
  |-- schedules (CASCADE)
  |-- audit_log (SET NULL on user delete)
```

### Key Design Decisions

- **Per-user config in DB**: Each user's provider/model configuration is stored as JSON in `user_configs`, not shared across users
- **Fernet encryption**: User API keys are encrypted at rest, decrypted only when needed for LLM calls
- **Audit log preservation**: Audit entries survive user deletion (user_id set to NULL)
- **UUID primary keys**: All tables use randomly generated hex IDs

## LiteLLM Integration

All LLM calls go through [LiteLLM](https://docs.litellm.ai/), which provides a unified API across providers:

- **Benchmarks**: `litellm.completion()` with `stream=True`
- **Tool evals**: `litellm.acompletion()` (non-streaming, to capture tool_calls)
- **Model IDs**: Follow LiteLLM conventions with provider prefixes
- **API keys**: Passed per-call via the `api_key` parameter

### Provider Parameter Handling

The `provider_params.py` module implements a three-tier parameter system:

1. **Tier 1**: Universal params (temperature, max_tokens, stop)
2. **Tier 2**: Common params with provider-specific ranges and support
3. **Tier 3**: Provider-specific passthrough (bypasses validation)

Parameters are validated, clamped, and conflict-resolved before being passed to LiteLLM.

## Security

- **Content Security Policy**: Restricts script/style/font sources
- **CORS**: Disabled by default, configurable via `CORS_ORIGINS`
- **Error sanitization**: API keys and tokens stripped from error messages
- **Non-root Docker**: Runs as dedicated `bench` user
- **HttpOnly cookies**: Refresh tokens stored in HttpOnly cookies
- **Rate limiting**: Per-user benchmark limits + login rate limiting
