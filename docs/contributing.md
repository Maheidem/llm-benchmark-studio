# Contributing

## Development Setup

1. Clone the repository:

    ```bash
    git clone https://github.com/maheidem/llm-benchmark-studio.git
    cd llm-benchmark-studio
    ```

2. Install backend dependencies:

    ```bash
    uv sync
    ```

3. Install frontend dependencies:

    ```bash
    cd frontend && npm install && cd ..
    ```

4. Set up environment:

    ```bash
    cp .env.example .env
    # Edit .env with at least one provider API key
    ```

5. Run the application:

    ```bash
    # Backend (serves the built frontend from static/)
    python app.py

    # Frontend dev server (hot reload, proxies API to backend)
    cd frontend && npm run dev
    ```

## Project Structure

```
llm-benchmark-studio/
├── app.py                  # FastAPI orchestrator (lifespan, middleware, logging)
├── benchmark.py            # Core benchmark engine (Target, RunResult, CLI)
├── auth.py                 # JWT + bcrypt authentication
├── db.py                   # SQLite database layer (16 tables)
├── keyvault.py             # Fernet encryption for API keys
├── provider_params.py      # Provider parameter registry (10 providers)
├── job_registry.py         # JobRegistry singleton (background jobs, queuing)
├── job_handlers.py         # Handler functions for 6 job types
├── ws_manager.py           # WebSocket ConnectionManager
├── schemas.py              # Pydantic request/response models
├── config.yaml             # Default provider/model configuration
├── routers/                # 20 FastAPI router modules
│   ├── __init__.py         # Router registration (all_routers list)
│   ├── helpers.py          # Shared utilities (scoring, target selection)
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
│   ├── env.py              # /api/env
│   └── onboarding.py       # /api/onboarding/*
├── frontend/               # Vue 3 SPA
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.vue
│       ├── main.js
│       ├── router/         # Vue Router (history mode)
│       │   └── index.js
│       ├── stores/         # 8 Pinia stores
│       │   ├── auth.js
│       │   ├── benchmark.js
│       │   ├── config.js
│       │   ├── judge.js
│       │   ├── notifications.js
│       │   ├── paramTuner.js
│       │   ├── promptTuner.js
│       │   └── toolEval.js
│       ├── composables/    # 7 composables
│       │   ├── useWebSocket.js
│       │   ├── useToast.js
│       │   ├── useModal.js
│       │   ├── useChartTheme.js
│       │   ├── useProviderColors.js
│       │   ├── useActiveSession.js
│       │   └── useSharedContext.js
│       ├── views/          # Page components
│       ├── components/     # Reusable UI components (40+)
│       ├── utils/          # Utility functions
│       └── assets/         # CSS and static assets
├── tests/                  # 20 test files, ~6,700 lines
│   ├── conftest.py         # Shared fixtures (test DB, auth tokens)
│   ├── test_api_contracts.py
│   ├── test_scoring.py
│   ├── test_provider_params.py
│   └── ...
├── Dockerfile              # Multi-stage build (Node frontend + Python backend)
├── docker-compose.yml      # Docker Compose for local development
├── pyproject.toml          # Python dependencies (uv)
├── uv.lock                 # Lockfile
├── .env.example            # Example environment variables
├── .github/
│   └── workflows/
│       └── ci.yml          # CI/CD pipeline (test -> build -> deploy)
├── docs/                   # MkDocs documentation (this site)
├── data/                   # SQLite database and Fernet key (gitignored)
└── results/                # Benchmark result JSON files (gitignored)
```

## Code Conventions

### Backend (Python)

- **Async-first**: All route handlers and database operations use `async/await`
- **Framework**: FastAPI with modular routers in `routers/`
- **Database**: `aiosqlite` with WAL mode, raw SQL, `DatabaseManager` singleton
- **Auth**: Dependency injection via `Depends(auth.get_current_user)` and `Depends(auth.require_admin)`
- **Validation**: Pydantic v2 models in `schemas.py` for all request/response schemas
- **Error handling**: Return `JSONResponse` with appropriate HTTP status codes
- **Job execution**: Long-running operations go through `job_registry.submit()`, not direct execution
- **WebSocket events**: Follow pattern `{"type": "event_type", "job_id": "...", "data": {...}}`
- **Naming**: `snake_case` for functions and variables, private helpers prefixed with `_`
- **Logging**: Structured JSON logging via `logging` stdlib, no third-party log libraries

### Frontend (Vue 3)

- **Framework**: Vue 3 with Composition API (`<script setup>`)
- **State management**: Pinia stores (one per domain)
- **Routing**: Vue Router with history mode
- **Styling**: Tailwind CSS utility classes
- **Real-time**: `useWebSocket` composable for all WebSocket communication
- **Notifications**: `useToast` composable for user feedback
- **API calls**: `fetch()` with JWT Authorization header (managed by auth store)
- **Components**: Domain-organized directories under `components/`
- **Charts**: Chart.js integrated via `useChartTheme` composable

### Configuration

- **YAML**: Provider and model definitions (`config.yaml`, per-user `user_configs`)
- **JSON**: Database storage for results, tool definitions, search spaces
- **Environment**: Secrets and deployment settings via `.env`

## Making Changes

### Adding a New Router

1. Create a new file in `routers/` (e.g., `routers/my_feature.py`)
2. Define an `APIRouter` with a prefix: `router = APIRouter(prefix="/api/my-feature", tags=["my-feature"])`
3. Use `Depends(auth.get_current_user)` for authenticated routes
4. Import and register in `routers/__init__.py` (add to `all_routers` list)
5. Add Pydantic request/response models to `schemas.py` if needed

### Adding a New Job Type

1. Add the handler function in `job_handlers.py` following the signature:
    ```python
    async def my_handler(job_id, params, cancel_event, progress_cb) -> str | None:
    ```
2. Register it in `register_all_handlers()` at the bottom of `job_handlers.py`
3. Add the job type to the `jobs` table CHECK constraint in `db.py`
4. Create a router endpoint that calls `job_registry.submit("my_type", user_id, params)`

### Backend Changes

1. API routes go in the appropriate router module under `routers/`
2. Follow existing patterns:
    - Use `Depends(auth.get_current_user)` for authenticated routes
    - Use `Depends(auth.require_admin)` for admin-only routes
    - Return `JSONResponse` for errors with appropriate status codes
    - Log significant actions to the audit log via `db.log_audit()`
3. Database schema changes go in `db.py` in the `init_db()` function
4. Use `try/except` with `ALTER TABLE` for backward-compatible schema migrations
5. Add Pydantic models to `schemas.py` for request validation

### Frontend Changes

1. Page components go in `frontend/src/views/`
2. Reusable components go in `frontend/src/components/<domain>/`
3. State management goes in `frontend/src/stores/`
4. Follow existing patterns:
    - Use Composition API with `<script setup>`
    - Use Tailwind CSS for styling
    - Test in dark mode (the default theme)
    - WebSocket event handlers should match backend event types
5. Build for production: `cd frontend && npm run build` (outputs to `static/`)

### Testing

```bash
# Run all tests (~405 tests, ~4 seconds)
uv run pytest

# Run a specific test file
uv run pytest tests/test_scoring.py -v

# Run tests matching a name pattern
uv run pytest -k "test_benchmark" -v

# Run with verbose output
uv run pytest -v
```

Test conventions:

- Framework: `pytest` with `pytest-asyncio` for async tests
- HTTP testing: `httpx.ASGITransport` with FastAPI `TestClient`
- No external API calls: All LiteLLM calls are mocked
- Fixtures in `conftest.py`: test database, auth tokens, sample data
- Test categories: unit (pure functions), API contracts, integration, E2E smoke
- E2E smoke tests (`test_e2e_smoke.py`) require a real API key and run only in CI on `main`

## Validation Before Submitting

```bash
# Run the test suite
uv run pytest

# Verify the app starts
python app.py

# Verify the frontend builds
cd frontend && npm run build

# Verify Docker build
docker compose up --build

# Test the health endpoint
curl http://localhost:8501/healthz
```

## CI/CD

- Push to `main` triggers: test -> build -> smoke test -> auto-deploy to staging
- Create a version tag (`v1.2.0`) for production deploy
- PRs trigger: test -> build -> smoke test (no deploy)
- Registry: `ghcr.io/maheidem/llm-benchmark-studio`
- Deploy method: Portainer CE REST API (pull image -> stop stack -> remove containers -> start stack)
