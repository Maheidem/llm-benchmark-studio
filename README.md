# LLM Benchmark Studio

**Measure token throughput and latency across LLM providers -- side by side, in seconds.**

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![LiteLLM](https://img.shields.io/badge/LiteLLM-powered-blue)

![Dashboard](screenshot.png)

## Features

- **Multi-provider benchmarking** -- run the same prompt against OpenAI, Anthropic, Google Gemini, and any LiteLLM-compatible endpoint in one go
- **Parallel execution** -- providers benchmark concurrently; models within a provider run sequentially to avoid self-contention
- **Streaming measurement** -- tracks tokens/sec (output), input tokens/sec (prefill), time-to-first-token, and total latency
- **Context tier stress testing** -- test how models degrade across context sizes (1K, 5K, 10K, 50K, 100K+ tokens)
- **Statistical rigor** -- multiple runs with std dev, min/max, p50/p95, IQR-based outlier detection, and warm-up runs
- **Cost tracking** -- per-request cost estimates via LiteLLM's pricing data
- **Interactive web dashboard** -- real-time SSE progress, bar charts, TTFT charts, speed-vs-latency scatter plots
- **CSV export** -- one-click download of results
- **Run history** -- browse and compare past benchmark runs
- **Prompt library** -- built-in templates (reasoning, code gen, creative, Q&A) and custom prompts
- **Settings UI** -- manage providers, models, and API keys directly from the browser
- **Provider health checks** -- verify connectivity and latency before benchmarking
- **CLI tool** -- scriptable alternative with Rich terminal output and JSON result files

## Quick Start

```bash
# 1. Clone
git clone <repo-url> && cd llm_benchmarks

# 2. Install dependencies
uv sync

# 3. Configure API keys
cp .env.example .env
# Edit .env and add your keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)

# 4. Launch the dashboard
python app.py
# Open http://localhost:8501
```

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

# Debug LiteLLM calls
python benchmark.py --verbose
```

Results are saved as timestamped JSON files in `results/`.

## Web Dashboard

```bash
python app.py                    # http://localhost:8501
python app.py --port 3333        # custom port
```

The dashboard provides three tabs:

| Tab | What it does |
|---|---|
| **Benchmark** | Select models, configure prompt/params, run benchmarks with real-time progress, view charts and results table |
| **History** | Browse past runs, expand details, select multiple runs for side-by-side comparison |
| **Settings** | Add/edit/remove providers and models, manage API keys in `.env`, check provider health |

## Configuration

All configuration lives in `config.yaml`:

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

Key fields per model: `id`, `display_name`, `context_window`, `max_output_tokens`, `skip_params`.

## Supported Providers

| Provider | Prefix | Notes |
|---|---|---|
| OpenAI | *(none)* | `gpt-5.2`, `gpt-5.1-codex`, etc. |
| Anthropic | `anthropic/` | Claude Opus, Sonnet, Haiku |
| Google Gemini | `gemini/` | Gemini 3 Pro, Flash |
| ZAI GLM | `zai/` | GLM-4.7, GLM-4.5-Air |
| LM Studio | `lm_studio/` | Local models via OpenAI-compatible API |
| Any LiteLLM provider | varies | Add via `config.yaml` -- if LiteLLM supports it, so does Benchmark Studio |

## Architecture

```
                    config.yaml
                        |
            +-----------+-----------+
            |                       |
      benchmark.py              app.py (FastAPI)
      CLI + Rich output         REST API + SSE streaming
            |                       |
            +--- LiteLLM -----------+--- index.html
            |    (streaming)            (Tailwind + Chart.js)
            |                           Single-file dashboard
            v
     results/*.json
     (timestamped output)
```

**Core data flow:** `config.yaml` defines providers/models. `build_targets()` resolves them into `Target` objects. `run_single()` calls `litellm.completion(stream=True)`, measuring TTFT and tok/s. Results aggregate into `AggregatedResult` with variance stats, then save to JSON.

**Web concurrency model:** The `/api/benchmark` endpoint launches one `asyncio.Task` per provider. Results flow through an `asyncio.Queue` into SSE events consumed by the browser in real time. A lock prevents concurrent benchmark runs.
