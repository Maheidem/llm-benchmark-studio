# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LLM Benchmark Studio measures token throughput (tokens/sec) and latency (TTFT) across multiple LLM providers simultaneously. It has two interfaces: a CLI tool (`benchmark.py`) and a web dashboard (`app.py` + `index.html`).

## Commands

### Setup
```bash
uv sync                    # Install dependencies (uses uv lockfile)
cp .env.example .env       # Then add API keys
```

### Run Web Dashboard
```bash
python app.py                          # Default: http://localhost:8501
python app.py --host 0.0.0.0 --port 3333  # Custom host/port
```

### Run CLI Benchmarks
```bash
python benchmark.py                                    # All providers, all models
python benchmark.py --provider openai --runs 3         # Filter by provider
python benchmark.py --model GLM --runs 1               # Filter by model name substring
python benchmark.py --context-tiers 0,5000,50000       # Custom context tiers
python benchmark.py --max-tokens 1024 --temperature 0.5
python benchmark.py --no-save                          # Skip saving results JSON
python benchmark.py --verbose                          # Enable LiteLLM debug logging
```

### No Test Suite or Linter Configured
Manual validation: run a single-model benchmark to verify changes work.

## Architecture

**Three files form the entire application:**

- **`benchmark.py`** - Core engine. Defines `Target` (model endpoint metadata), `RunResult`, and `AggregatedResult` dataclasses. `run_single()` executes one streaming LLM call via LiteLLM measuring TTFT and tokens/sec. `run_benchmarks()` orchestrates multi-run loops with Rich progress display. CLI entry point with argparse.

- **`app.py`** - FastAPI backend. Serves the dashboard, provides REST API for config CRUD (`/api/config/*`), API key management (`/api/env`), and benchmark execution (`/api/benchmark`). Benchmark endpoint streams results via SSE using an asyncio.Queue producer-consumer pattern where providers run in parallel but models within a provider run sequentially (to avoid self-contention).

- **`index.html`** - Single-file web dashboard (Tailwind CSS + Chart.js). Connects to SSE endpoint for real-time progress. Handles model selection, parameter configuration, results visualization, and history browsing.

### Key Data Flow
```
config.yaml → build_targets() → run_single() via LiteLLM streaming → RunResult
                                                                        ↓
                                                              AggregatedResult → JSON file in results/
```

### Concurrency Model (Web)
Provider groups execute in parallel via `asyncio.create_task()`. Within each provider group, models run sequentially. Results flow through `asyncio.Queue` → SSE events → browser updates.

## Configuration

- **`config.yaml`** - Providers, models, defaults (max_tokens, temperature, context_tiers, prompt). Each provider has `api_base`, `api_key_env`, optional `model_prefix`, and a list of models with `id`, `display_name`, optional `context_window` and `skip_temperature`.
- **`.env`** - API keys referenced by `api_key_env` in config.yaml. Managed via web UI at `/api/env`.
- **Results** - Timestamped JSON files saved to `results/` directory.

## LiteLLM Provider Patterns
All LLM calls go through `litellm.completion()` with `stream=True`. Model IDs follow LiteLLM conventions:
- OpenAI: `gpt-5.2` (no prefix)
- Anthropic: `anthropic/claude-opus-4-6`
- Google: `gemini/gemini-3-pro-preview`
- Local (LM Studio): `lm_studio/model-name` with custom `api_base`
- Custom endpoints: prefix + model id with `api_base` override

Some models use `skip_temperature: true` (Anthropic, Codex) - the engine omits the temperature parameter for these.

## Context Tier Testing
Benchmarks can test across multiple context window sizes. `generate_context_text()` creates filler system prompts to pad input to target token counts. Tiers exceeding a model's `context_window - max_tokens - 100` headroom are automatically skipped.
