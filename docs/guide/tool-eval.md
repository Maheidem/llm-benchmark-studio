# Tool Calling Evaluation

The Tool Calling Evaluation framework tests whether LLMs correctly use function calling (tool use). You define tool suites with test cases and run models against them to measure accuracy.

## Core Concepts

### Tool Suite

A collection of tools and test cases -- like a test suite for function calling.

- **Tools**: Defined in OpenAI function calling JSON schema format
- **Test Cases**: Each has a prompt, an expected tool, and optionally expected parameters
- **System Prompt**: Optional per-suite system prompt injected before test case prompts

### Test Case

A single evaluation scenario:

```json
{
  "prompt": "What's the weather in Paris?",
  "expected_tool": "get_weather",
  "expected_params": { "city": "Paris" },
  "param_scoring": "exact"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `prompt` | string | The user message sent to the model |
| `expected_tool` | string or list | The tool name(s) the model should call |
| `expected_params` | object or null | Expected parameter values (null = not scored) |
| `param_scoring` | string | Scoring mode: `exact` (default), `fuzzy`, `contains`, `semantic` |
| `scoring_config` | object or null | Advanced scoring configuration for fuzzy matching |

The `expected_tool` field supports multiple acceptable answers:

```json
{
  "expected_tool": ["search", "web_search"],
  "prompt": "Find information about quantum computing"
}
```

### Scoring Configuration

For advanced parameter matching, use `scoring_config`:

```json
{
  "scoring_config": {
    "mode": "case_insensitive",
    "epsilon": 0.01
  }
}
```

Available scoring modes:

| Mode | Behavior |
|------|----------|
| `exact` | Case-insensitive string, exact numeric (default) |
| `case_insensitive` | Case-insensitive string comparison |
| `contains` | Substring match (either direction) |
| `numeric_tolerance` | Float comparison within epsilon |
| `regex` | Regular expression match |

### tool_choice Parameter

Controls whether the model must call a tool:

| Value | Behavior |
|-------|----------|
| `required` | Model MUST call a tool (recommended for testing) |
| `auto` | Model can respond with text instead of calling a tool |
| `none` | Model cannot use tools (control test) |

If a provider does not support `required`, the engine automatically falls back to `auto`.

## Scoring

### Tool Selection Score (0.0 or 1.0)

- Did the model call the expected tool?
- Case-insensitive comparison
- Supports multiple acceptable tools
- If `expected_tool` is null, scores 1.0 only if the model also called nothing

### Parameter Accuracy (0.0 - 1.0 or null)

- Per-key comparison: `correct_params / total_expected_params`
- Strings are compared case-insensitively
- Numbers use float comparison
- If `expected_params` is null, returns null (not scored)
- If `expected_params` is `{}`, returns 1.0

### Overall Score (weighted)

```
overall = 0.6 * tool_score + 0.4 * param_score
```

If parameters are not scored (null): `overall = tool_score`

## Creating a Tool Suite

### Via the UI

1. Navigate to **Tool Eval**
2. Click **New Suite**
3. Enter a name and description
4. Add tool definitions in JSON format
5. Optionally set a **system prompt** for the suite
6. Add test cases with prompts and expected results

### Via JSON Import

Import a complete suite from a JSON file:

```json
{
  "name": "Weather API Suite",
  "description": "Tests weather tool calling",
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
          "type": "object",
          "properties": {
            "city": { "type": "string", "description": "City name" },
            "units": { "type": "string", "enum": ["celsius", "fahrenheit"] }
          },
          "required": ["city"]
        }
      }
    }
  ],
  "test_cases": [
    {
      "prompt": "What's the weather in Paris?",
      "expected_tool": "get_weather",
      "expected_params": { "city": "Paris" }
    },
    {
      "prompt": "Temperature in Tokyo in Fahrenheit",
      "expected_tool": "get_weather",
      "expected_params": { "city": "Tokyo", "units": "fahrenheit" }
    }
  ]
}
```

Import via API:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @suite.json \
  http://localhost:8501/api/tool-eval/import
```

### Via MCP Server

Import tools from an MCP (Model Context Protocol) server:

1. Click **Import from MCP**
2. Enter the MCP server SSE endpoint URL
3. The system connects, discovers tools, and creates a new suite
4. Add test cases manually for the discovered tools

## System Prompt Per Suite

Each tool suite can have its own system prompt that is injected before every test case prompt. This is useful for:

- Guiding the model's tool calling behavior for the entire suite
- Setting context that applies to all test cases
- Testing how different base instructions affect tool use accuracy

Set the system prompt via the UI when editing a suite, or via the API:

```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"system_prompt": "Always use the available tools. Never respond with plain text."}' \
  http://localhost:8501/api/tool-suites/{suite_id}
```

Additionally, when running an eval you can pass per-model system prompts via the `system_prompt` field (as a string for all models, or a dict keyed by `provider_key::model_id` for per-model prompts).

## Running an Eval

### Execution via JobRegistry

All eval runs execute through the **JobRegistry**, which provides:

- **Background execution**: The API returns a `job_id` immediately
- **Per-user concurrency limits**: Jobs queue if a user exceeds their limit
- **Cancellation**: Cancel running jobs via `job_id`
- **Persistence**: Job state is saved to SQLite, surviving server restarts
- **WebSocket progress**: Real-time updates pushed to the frontend

### Steps

1. Select a tool suite
2. Choose one or more models
3. Set temperature (default: 0.0 for deterministic results)
4. Set tool_choice (`required` recommended)
5. Optionally link to an **Experiment** for tracking
6. Click **Run Eval**

The API returns `{"job_id": "...", "status": "submitted"}`. Progress and results are delivered via WebSocket.

### WebSocket Event Flow

```
POST /api/tool-eval  -->  { job_id, status: "submitted" }
                              |
                     WebSocket events:
                              |
    job_created  --->  tool_eval_init  --->  tool_eval_progress (per case)
                                                     |
                                              tool_eval_result (per case)
                                                     |
                                              tool_eval_summary (per model)
                                                     |
                                              tool_eval_complete
```

Key WebSocket event types:

| Event | Description |
|-------|-------------|
| `job_created` | Job submitted to registry |
| `tool_eval_init` | Eval starting, includes target list and total cases |
| `tool_eval_progress` | Per-case progress update (current/total) |
| `tool_eval_result` | Individual test case result with scores |
| `tool_eval_summary` | Per-model aggregate scores |
| `tool_eval_complete` | Eval finished, includes `eval_id` and optional `delta` |
| `job_progress` | Generic progress update (percentage, detail text) |
| `job_completed` | Job finished successfully |
| `job_failed` | Job encountered an error |
| `job_cancelled` | Job was cancelled by user |

### Cancellation

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "abc123"}' \
  http://localhost:8501/api/tool-eval/cancel
```

## Multi-Turn Evaluation

Multi-turn test cases simulate tool calling chains where the model must call prerequisite tools before reaching the final expected tool.

Configure a multi-turn test case:

```json
{
  "prompt": "Book a flight from NYC to London for next Friday",
  "expected_tool": "book_flight",
  "expected_params": { "origin": "NYC", "destination": "London" },
  "multi_turn": true,
  "max_rounds": 5,
  "optimal_hops": 2,
  "valid_prerequisites": ["search_flights", "get_availability"],
  "mock_responses": {
    "search_flights": { "flights": [{"id": "FL123", "price": 450}] },
    "get_availability": { "available": true, "seats": 12 }
  }
}
```

### Multi-Turn Scoring

| Metric | Description |
|--------|-------------|
| Completion | Did the model reach the expected final tool? (weighted tool + param score) |
| Efficiency | `optimal_hops / actual_hops` (capped at 1.0) |
| Redundancy Penalty | -10% per consecutive identical tool call |
| Detour Penalty | -10% per call not in valid_prerequisites |
| Overall | `completion * efficiency - redundancy - detour` (clamped 0-1) |

## Experiment Tracking

Experiments group related eval runs, param tunes, prompt tunes, and judge reports together for tracking improvement over time.

### Creating an Experiment

1. Click **New Experiment** in the Tool Eval page
2. Name the experiment and select a suite
3. Optionally pin a baseline eval run

### Experiment Features

- **Baseline**: Pin an eval run as the reference point. All subsequent runs show a delta from baseline.
- **Timeline**: View all linked runs chronologically with scores and deltas.
- **Best Config**: The experiment automatically tracks the best-performing configuration across all linked runs (eval, param tune, prompt tune).
- **Run Best**: One-click to re-run the eval using the experiment's best config.

### Linking Runs to Experiments

When running an eval, param tune, or prompt tune, select an experiment from the dropdown. The run is automatically linked:

- Eval runs compute `delta = avg_score - baseline_score`
- Param tune runs auto-promote the best result as an eval run
- Prompt tune runs auto-promote the best prompt

### Experiment Timeline API

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/experiments/{experiment_id}/timeline
```

Returns entries ordered by timestamp, each with type (`eval`, `param_tune`, `prompt_tune`, `judge`), score, delta from baseline, and configuration summary.

## Execution Flow

```
POST /api/tool-eval (with suite_id, models, temperature, tool_choice)
        |
        v
JobRegistry.submit(job_type="tool_eval")
        |
        v
tool_eval_handler() in job_handlers.py
        |
        v
For each provider group (in parallel):
  For each model (sequentially within provider):
    For each test case:
        |
        v
    litellm.acompletion() (non-streaming)
        |
        v
    response.choices[0].message.tool_calls
        |
        v
    score_tool_selection() + score_params()
        |
        v
    compute_overall_score()
        |
        v
    WebSocket: tool_eval_result
        |
        v
Per-model summaries computed
        |
        v
Results saved to DB (tool_eval_runs table)
        |
        v
WebSocket: tool_eval_complete
```

Provider groups execute in parallel via `asyncio.create_task()`. Models within a provider run sequentially to avoid self-contention on rate-limited APIs.

## API Reference

### Tool Suite Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tool-suites` | List user's tool suites |
| `POST` | `/api/tool-suites` | Create a new suite |
| `GET` | `/api/tool-suites/{id}` | Get suite with tools and test cases |
| `PUT` | `/api/tool-suites/{id}` | Update suite (name, description, tools) |
| `PATCH` | `/api/tool-suites/{id}` | Patch suite fields (e.g., system_prompt) |
| `DELETE` | `/api/tool-suites/{id}` | Delete suite and its test cases |
| `GET` | `/api/tool-suites/{id}/export` | Export suite as JSON |
| `POST` | `/api/tool-eval/import` | Import suite from JSON |
| `GET` | `/api/tool-eval/import/example` | Download example import template |

### Test Case Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tool-suites/{id}/cases` | List test cases for a suite |
| `POST` | `/api/tool-suites/{id}/cases` | Create test case(s) (single or bulk) |
| `PUT` | `/api/tool-suites/{id}/cases/{cid}` | Update a test case |
| `DELETE` | `/api/tool-suites/{id}/cases/{cid}` | Delete a test case |

### Eval Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tool-eval` | Run eval (returns job_id) |
| `POST` | `/api/tool-eval/cancel` | Cancel running eval |
| `GET` | `/api/tool-eval/history` | List eval runs |
| `GET` | `/api/tool-eval/history/{id}` | Get eval run details |
| `DELETE` | `/api/tool-eval/history/{id}` | Delete eval run |

### Experiment Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/experiments` | List experiments |
| `POST` | `/api/experiments` | Create experiment |
| `GET` | `/api/experiments/{id}` | Get experiment details |
| `PUT` | `/api/experiments/{id}` | Update experiment |
| `DELETE` | `/api/experiments/{id}` | Delete experiment |
| `PUT` | `/api/experiments/{id}/baseline` | Pin baseline eval run |
| `GET` | `/api/experiments/{id}/timeline` | Get experiment timeline |
| `POST` | `/api/experiments/{id}/run-best` | Run eval with best config |

## Common Issues

**100% Tool Selection, 0% Parameter Accuracy**: The model picks the right tool but hallucinates parameter values. Ensure your `expected_params` match realistic model output patterns.

**0% everything with `auto`**: The model responds with text instead of calling tools. Switch to `required` for tool_choice.

**MCP suites failing**: Auto-generated prompts from MCP tool descriptions may be too vague. Use `required` for tool_choice and write more specific prompts.

**Local LLMs returning JSON in function name**: The engine automatically detects when a local LLM stuffs the entire tool call JSON into the function name field and extracts the actual tool name and parameters.

**Reconnection during eval**: If the WebSocket disconnects and reconnects, the frontend restores the active job from session storage. Completed results can be fetched from the DB via the `result_ref` stored on the job record.
