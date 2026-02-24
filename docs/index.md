# LLM Benchmark Studio

**Measure token throughput, latency, and tool calling accuracy across LLM providers -- all in one place.**

LLM Benchmark Studio is a multi-user SaaS platform for evaluating Large Language Models. It combines speed benchmarking (tokens/sec, time to first token) with a tool calling evaluation framework, parameter tuning, prompt tuning, and LLM-as-judge -- giving you a complete picture of model performance.

## Key Features

### Speed Benchmarking
- Real-time streaming measurements of tokens per second and time to first token (TTFT)
- Multi-provider parallel execution with per-provider sequential model runs
- Context window scaling tests across configurable token tiers (1K to 150K+)
- Cost tracking with LiteLLM pricing and custom per-model pricing
- Statistical analysis with std dev, min/max, p50/p95, IQR outlier detection

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
- Grid search across temperature, top_p, top_k, and other parameters
- Provider-aware validation and clamping for 10 providers
- Per-model search spaces with automatic deduplication
- Search space presets (save, load, delete)
- Built-in vendor presets for specific model families

### Prompt Tuner
- Quick mode (single generation) and evolutionary mode (multi-generation with selection)
- Meta-model generates prompt variations, target models evaluate them
- Automatic best-result promotion to experiments

### Experiments
- Group related eval, param tune, prompt tune, and judge runs
- Timeline view of all runs within an experiment
- Automatic baseline and best-score tracking

### Analytics and History
- Benchmark history browser with search and filtering
- Leaderboard rankings by speed, cost, and quality
- Trend analysis over configurable time periods
- CSV and JSON export/import for all data

### Scheduling
- Automated recurring benchmark runs
- Configurable intervals and model selection
- Results saved to history with schedule metadata

### Multi-User Platform
- JWT authentication with 24-hour access tokens and 7-day refresh tokens
- Per-user API key management with Fernet encryption
- Per-user configuration (providers, models, prompts)
- Admin dashboard with user management, audit logs, and rate limits
- Real-time WebSocket updates for all job types (multi-tab support)
- Centralized job management with per-user concurrency limits and queuing

## Quick Links

- [Installation](getting-started/installation.md) -- Get up and running
- [Quick Start](getting-started/quickstart.md) -- Run your first benchmark
- [API Reference](api/rest.md) -- Full REST API documentation
- [Docker Setup](hosting/docker.md) -- Self-host with Docker
- [Architecture](architecture.md) -- How it all works

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.13, FastAPI, Uvicorn |
| Database | SQLite with aiosqlite (WAL mode), 16 tables |
| LLM Integration | LiteLLM (unified API for all providers) |
| Frontend | Vue 3, Vite, Pinia, Vue Router, Tailwind CSS |
| Real-time | WebSocket (ConnectionManager, multi-tab, auto-reconnect) |
| Job Management | JobRegistry (asyncio tasks, per-user concurrency, queuing) |
| Authentication | JWT (python-jose), bcrypt, refresh tokens |
| Encryption | Fernet (cryptography) for API key storage |
| Validation | Pydantic v2 for all request/response schemas |
| CLI | Rich for terminal output |
| Testing | pytest + pytest-asyncio, ~405 tests, ~4s runtime |
| Containerization | Docker with multi-stage builds (Node + Python) |
| CI/CD | GitHub Actions, GHCR, Portainer CE |
