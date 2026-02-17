# Contributing

## Development Setup

1. Clone the repository:

    ```bash
    git clone https://github.com/maheidem/llm-benchmark-studio.git
    cd llm-benchmark-studio
    ```

2. Install dependencies:

    ```bash
    uv sync
    ```

3. Set up environment:

    ```bash
    cp .env.example .env
    # Edit .env with at least one provider API key
    ```

4. Run the application:

    ```bash
    python app.py
    ```

## Project Structure

```
llm-benchmark-studio/
├── app.py                 # FastAPI backend (all routes, eval engine, scheduler)
├── benchmark.py           # Core benchmark engine (data structures, CLI)
├── auth.py                # Authentication (JWT, bcrypt, rate limiting)
├── db.py                  # Database layer (schema, CRUD)
├── keyvault.py            # Fernet encryption for API keys
├── provider_params.py     # Provider parameter registry and validation
├── index.html             # Single-file web dashboard
├── config.yaml            # Default provider/model configuration
├── pyproject.toml         # Python dependencies (uv)
├── uv.lock                # Lockfile
├── Dockerfile             # Docker image definition
├── docker-compose.yml     # Docker Compose for local development
├── .env.example           # Example environment variables
├── .github/
│   └── workflows/
│       ├── ci.yml         # CI/CD pipeline
│       └── docs.yml       # Documentation deployment
├── deploy/
│   ├── docker-compose.staging.yml
│   └── docker-compose.prod.yml
├── docs/                  # MkDocs documentation (this site)
├── data/                  # SQLite database and Fernet key (not committed)
└── results/               # Benchmark result JSON files (not committed)
```

## Code Conventions

### Backend (Python)

- **Framework**: FastAPI with async handlers
- **Database**: aiosqlite with WAL mode, raw SQL (no ORM)
- **Auth**: Dependency injection via `Depends(auth.get_current_user)`
- **Error handling**: Return `JSONResponse` with appropriate status codes
- **Validation**: Pydantic for config schema; manual validation for request bodies
- **Naming**: snake_case for functions and variables

### Frontend (HTML/JS)

- **Single file**: All frontend code lives in `index.html`
- **Styling**: Tailwind CSS (loaded via CDN)
- **Charts**: Chart.js (loaded via CDN)
- **State management**: Vanilla JavaScript with DOM manipulation
- **API calls**: `fetch()` with JWT Authorization header

### Configuration

- **YAML**: Provider and model definitions
- **JSON**: Database storage for user configs, results, tool definitions
- **Environment**: Secrets and deployment settings via `.env`

## Making Changes

### Backend Changes

1. All API routes are in `app.py`
2. Follow existing patterns for new endpoints:
    - Use `Depends(auth.get_current_user)` for authenticated routes
    - Use `Depends(auth.require_admin)` for admin-only routes
    - Return `JSONResponse` for errors with appropriate status codes
    - Log significant actions to the audit log
3. Database schema changes go in `db.py` in the `init_db()` function
4. Use `try/except` with `ALTER TABLE` for backward-compatible schema migrations

### Frontend Changes

1. All UI code is in `index.html`
2. Follow existing UI patterns and Tailwind class conventions
3. Test in dark mode (the default theme)
4. Ensure SSE event handlers match the backend event types

### Testing

There is no automated test suite. Manual validation:

1. Run a single-model benchmark to verify speed metrics
2. Run a tool eval to verify scoring
3. Test the specific feature you changed
4. Check the browser console for JavaScript errors
5. Verify the Docker build: `docker compose up --build`

## Validation Before Submitting

```bash
# Verify the app starts
python app.py

# Verify Docker build
docker compose up --build

# Test the health endpoint
curl http://localhost:8501/healthz
```

## CI/CD

- Push to `main` triggers build, smoke test, and staging deploy
- Create a version tag (`v1.2.0`) for production deploy
- PRs trigger build and smoke test only (no deploy)
