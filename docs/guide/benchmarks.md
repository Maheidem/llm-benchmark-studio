# Running Benchmarks

LLM Benchmark Studio measures token throughput (tokens/sec), time to first token (TTFT), and cost across multiple LLM providers. Benchmarks can be run through the web dashboard or the CLI.

## Web Dashboard

### Selecting Models

1. Navigate to the **Benchmark** screen
2. Models are grouped by provider (OpenAI, Anthropic, Google Gemini, etc.)
3. Check the models you want to benchmark
4. Models from different providers run in parallel; models within the same provider run sequentially to avoid self-contention

### Configuring Parameters

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| Prompt | Text | Recursion explanation | The prompt sent to each model |
| Runs | 1-20 | 3 | Number of iterations per model |
| Max Tokens | 1-16384 | 512 | Maximum output tokens |
| Temperature | 0.0-2.0 | 0.7 | Sampling temperature |
| Context Tiers | List of ints | [0] | Token counts for context testing |
| Warmup | Boolean | true | Run one discarded warmup iteration |

**Prompt Templates**: Select from pre-defined prompts or create your own in Configuration. Templates are organized by category (reasoning, code, creative, Q&A).

**Context Tiers**: Test how models perform with different input sizes. The engine generates filler text (code snippets, prose, JSON, documentation) to pad the system prompt to the target token count. Models whose context window is too small for a tier automatically skip it.

### Benchmark Execution Flow

When you click **Run Benchmark**, the following happens:

1. The frontend sends a `POST /api/benchmark` request with the selected models, parameters, and options
2. The server validates the request, checks rate limits, and submits the job to the **JobRegistry**
3. The API returns a `job_id` immediately (the benchmark runs in the background)
4. Real-time progress and results are delivered via **WebSocket** events

```json
// POST /api/benchmark response
{"job_id": "a1b2c3d4...", "status": "submitted"}
```

### Real-Time Results

Results stream via WebSocket as each run completes. The frontend receives the following event sequence:

1. **`job_created`**: Job has been submitted to the registry
2. **`benchmark_init`**: Sent before execution begins; contains the list of targets, run count, context tiers, and max tokens so the frontend can set up per-provider progress tracking
3. **`benchmark_progress`**: Emitted before each individual run starts; includes provider, model, run number, and context tier
4. **`benchmark_result`**: Individual run metrics (tokens/sec, TTFT, total time, cost, input/output tokens)
5. **`job_progress`**: Overall progress percentage and detail string (e.g., "GPT-4o, Run 2/3")
6. **`job_completed`**: All runs finished and results saved; includes `result_ref` (the benchmark_run ID)

If a benchmark fails, a `job_failed` event is sent with the error message.

### Understanding Metrics

| Metric | Unit | Description |
|--------|------|-------------|
| Tokens/sec | tok/s | Output token generation speed |
| Input Tokens/sec | tok/s | Input processing speed |
| TTFT | ms | Time to first token (latency) |
| Total Time | seconds | End-to-end request duration |
| Output Tokens | count | Number of tokens generated |
| Input Tokens | count | Number of tokens in the prompt |
| Cost | USD | Estimated cost per run |

### Cancelling a Benchmark

Benchmarks can be cancelled through several methods:

- **Cancel button**: Click **Cancel** in the benchmark UI. This sends `POST /api/benchmark/cancel` with the `job_id`
- **Job tracker**: Cancel from the notification widget dropdown
- **WebSocket**: Send `{"type": "cancel", "job_id": "..."}` over the WebSocket connection

The system signals the cancel event, remaining provider tasks are stopped, and partial results are not saved. A `job_cancelled` WebSocket event confirms the cancellation.

## CLI Usage

The CLI tool runs benchmarks from the terminal with Rich-formatted output:

```bash
# Run all providers and models
python benchmark.py

# Filter by provider
python benchmark.py --provider openai

# Filter by model name (substring match)
python benchmark.py --model GPT

# Custom number of runs
python benchmark.py --runs 5

# Custom prompt
python benchmark.py --prompt "Write a haiku about programming"

# Custom context tiers
python benchmark.py --context-tiers 0,5000,50000

# Adjust output parameters
python benchmark.py --max-tokens 1024 --temperature 0.5

# Skip saving results to JSON file
python benchmark.py --no-save

# Enable LiteLLM debug logging
python benchmark.py --verbose
```

CLI results are saved as timestamped JSON files in the `results/` directory.

## Concurrency Model

The benchmark engine uses an asyncio-based concurrency model managed by the JobRegistry:

1. **Provider groups** execute in parallel via `asyncio.create_task()`
2. **Models within a provider** run sequentially (avoids API self-contention)
3. **Results flow** through an `asyncio.Queue` to the handler, which broadcasts them via WebSocket
4. **Per-user concurrency** is managed by the JobRegistry (configurable limit, default 1). Additional benchmark submissions are queued rather than rejected
5. **Job progress** updates are persisted to the database and broadcast to all connected tabs

## Rate Limiting

- Default: 2000 benchmark executions per user per hour
- Configurable via `BENCHMARK_RATE_LIMIT` environment variable
- Per-user rate limits can be set by admins

## Results Storage

Benchmark results are saved in two places:

1. **Database**: Per-user `benchmark_runs` table with full results JSON
2. **JSON files**: Timestamped files in the `results/` directory (for backward compatibility)

Results include all individual run data plus aggregated statistics (averages, standard deviation, min/max, percentiles).

## Context Tier Testing

Context tiers test model performance across different input sizes:

```
context_tiers: [0, 1000, 5000, 10000, 50000, 100000]
```

For each tier, the engine:

1. Generates filler text to pad the input to the target token count
2. Checks if the tier fits within the model's context window (with headroom for max_tokens + 100)
3. Skips the tier if it exceeds the model's capacity
4. Runs the specified number of iterations

The filler text alternates between diverse content types (Python code, prose, JSON data, technical documentation, networking concepts) to simulate realistic workloads.

## Provider-Specific Parameters

The [Provider Parameter Registry](../api/config-schema.md) handles provider-specific parameter rules:

- **Temperature clamping**: GPT-5 locks to 1.0; Gemini 3 clamps minimum to 1.0
- **Skip params**: Some models (like Anthropic's Claude) skip the temperature parameter
- **Conflict resolution**: Automatic handling of parameter conflicts (e.g., Anthropic's temperature + top_p restriction)
