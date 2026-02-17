# LLM Benchmark Studio

**Measure token throughput, latency, and tool calling accuracy across LLM providers -- all in one place.**

LLM Benchmark Studio is an open-source platform for evaluating Large Language Models. It combines speed benchmarking (tokens/sec, time to first token) with a tool calling evaluation framework, giving you a complete picture of model performance.

## Key Features

### Speed Benchmarking
- Real-time streaming measurements of tokens per second and time to first token (TTFT)
- Multi-provider parallel execution with per-provider sequential model runs
- Context window scaling tests across configurable token tiers
- Cost tracking with custom per-model pricing

### Tool Calling Evaluation
- Define tool suites using OpenAI function calling JSON schema
- Build test cases with expected tools and parameters
- Score tool selection accuracy and parameter correctness
- Multi-turn chain evaluation with mock tool responses
- Import tools directly from MCP servers

### LLM-as-Judge
- Use one model to evaluate another model's tool calling quality
- Live inline scoring during eval runs or post-eval batch analysis
- Cross-case analysis with overall grades and recommendations

### Parameter Tuner
- Grid search across temperature, top_p, top_k, and other parameters
- Finds the optimal parameter combination for tool calling accuracy
- Supports provider-specific parameter validation and clamping

### Prompt Tuner
- Test multiple system prompt variations against tool eval suites
- Compare prompt effectiveness across models
- Find the best prompt for your tool calling use case

### Analytics and History
- Benchmark history browser with search and filtering
- Leaderboard rankings by speed, cost, and quality
- Trend analysis over time
- CSV and JSON export for all data

### Scheduling
- Automated recurring benchmark runs
- Configurable intervals and model selection
- Results saved to history like manual runs

### Multi-User Support
- JWT authentication with access and refresh tokens
- Per-user API key management with Fernet encryption
- Per-user configuration (providers, models, prompts)
- Admin dashboard with user management, audit logs, and rate limits

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
| Database | SQLite with aiosqlite (WAL mode) |
| LLM Integration | LiteLLM (unified API for all providers) |
| Frontend | Single-file HTML, Tailwind CSS, Chart.js |
| Authentication | JWT (python-jose), bcrypt |
| Encryption | Fernet (cryptography) |
| CLI | Rich for terminal output |
| Containerization | Docker with multi-stage builds |
| CI/CD | GitHub Actions, GHCR, Portainer |
