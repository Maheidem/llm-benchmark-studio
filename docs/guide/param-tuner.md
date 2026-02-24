# Param Tuner

The Parameter Tuner performs a grid search across parameter combinations to find the optimal settings for tool calling accuracy. Think of it as GridSearchCV for LLM tool calling.

## How It Works

1. Define a **search space** with parameter ranges
2. The tuner generates all combinations (Cartesian product)
3. Each combination runs the full tool eval suite against selected models
4. Parameters are validated and clamped per-provider via the 3-tier param registry
5. Results are ranked by overall accuracy, with per-test-case drill-down available

## Search Space Configuration

Define parameter ranges as numeric ranges or categorical values:

```json
{
  "temperature": { "min": 0.0, "max": 1.0, "step": 0.2 },
  "tool_choice": ["auto", "required"],
  "top_p": { "min": 0.5, "max": 1.0, "step": 0.25 }
}
```

This produces combinations like:

| temperature | tool_choice | top_p |
|-------------|-------------|-------|
| 0.0 | auto | 0.5 |
| 0.0 | auto | 0.75 |
| 0.0 | auto | 1.0 |
| 0.0 | required | 0.5 |
| ... | ... | ... |

### Numeric Ranges

```json
{
  "param_name": { "min": 0.0, "max": 1.0, "step": 0.1 }
}
```

Generates: `[0.0, 0.1, 0.2, ..., 1.0]`

### Categorical Values

```json
{
  "tool_choice": ["auto", "required"]
}
```

## Phase 2: Custom Passthrough Params

Beyond the standard `temperature`, `top_p`, and `tool_choice`, the param tuner supports provider-specific parameters in the search space. These are validated and clamped through the 3-tier param registry.

### Tier 2 (Common) Parameters

Parameters supported by most providers with per-provider validation:

| Parameter | Type | Description |
|-----------|------|-------------|
| `top_p` | float | Nucleus sampling threshold |
| `top_k` | int | Top-K sampling cutoff |
| `frequency_penalty` | float | Penalize repeated tokens |
| `presence_penalty` | float | Penalize tokens already seen |
| `seed` | int | Reproducibility seed |
| `reasoning_effort` | enum | Reasoning depth (`none`, `low`, `medium`, `high`) |

### Tier 3 (Provider-Specific) Parameters

Passed through to the LLM provider without validation. Examples:

| Provider | Parameter | Description |
|----------|-----------|-------------|
| Ollama / LM Studio | `repetition_penalty` | Multiplicative penalty (not same as presence_penalty) |
| Ollama / LM Studio | `min_p` | Minimum probability threshold |
| Ollama | `mirostat` | Mirostat sampling mode (0, 1, 2) |
| Ollama | `num_ctx` | Context window size override |
| Ollama | `keep_alive` | Model memory duration |
| vLLM | `guided_json` | JSON schema for constrained decoding |
| vLLM | `best_of` | Generate N sequences, return best |
| OpenAI | `service_tier` | Priority tier (auto, default, flex, priority) |

Include these in the search space like any other parameter:

```json
{
  "temperature": [0.0, 0.5, 1.0],
  "repetition_penalty": [1.0, 1.1, 1.2],
  "min_p": { "min": 0.0, "max": 0.1, "step": 0.05 }
}
```

## Per-Model Search Spaces

Different models may benefit from different parameter ranges. The tuner supports per-model search spaces via `per_model_search_spaces`:

```json
{
  "suite_id": "...",
  "models": ["gpt-4o", "ollama/qwen3-coder-30b"],
  "search_space": {},
  "per_model_search_spaces": {
    "gpt-4o": {
      "temperature": [0.0, 0.5, 1.0],
      "top_p": [0.8, 1.0]
    },
    "ollama/qwen3-coder-30b": {
      "temperature": [0.7],
      "top_p": [0.8],
      "top_k": [20],
      "repetition_penalty": [1.0, 1.1]
    }
  }
}
```

When `per_model_search_spaces` is provided, each model uses its own search space. Models not listed in `per_model_search_spaces` fall back to the global `search_space`.

## Phase 3: Search Space Presets

Save and reuse search space configurations as presets.

### Built-in Presets

The system includes vendor-recommended presets:

| Preset | Parameters | Notes |
|--------|-----------|-------|
| Qwen3 Coder 30B (Recommended) | temp=0.7, top_p=0.8, top_k=20 | Greedy decoding (temp=0) worsens quality |
| GLM-4.7 Flash (Z.AI Recommended) | temp=0.8, top_p=0.6, top_k=2 | Very low top_k for MoE architecture |

### User Presets

Save your own presets via the Settings page:

1. Navigate to **Settings** > **Param Tuner** section
2. Configure a search space
3. Click **Save Preset**
4. Name the preset for later use

Presets are stored in the Phase 10 settings (`param_tuner.presets` array). They can be loaded, updated, or deleted from the Settings page or directly from the Param Tuner UI before starting a tune run.

## Phase 4: Settings Page Per-Model Param Config

The Settings page provides a dedicated section for configuring parameter support per model:

- **Param Support Seed**: `POST /api/param-support/seed` auto-detects which parameters each model supports by querying the provider registry
- **GGUF/MLX Auto-Detection**: `GET /api/lm-studio/detect?provider_key=...` probes local LM Studio instances to detect whether they serve GGUF or MLX models, enabling accurate parameter range selection

## Compatibility Matrix

Before running a tune, the UI shows a **compatibility matrix** that displays which parameters are supported by each selected model. This grid helps you:

- Identify which parameters will be dropped or clamped for specific models
- Understand provider-specific restrictions before committing to a long-running tune
- Spot unsupported parameters early (marked as "warn" or "drop")

The matrix is built client-side from the 3-tier parameter registry (`provider_params.py`).

## Running a Tune

### Execution via JobRegistry

All param tune runs execute through the **JobRegistry**, providing background execution, cancellation, and WebSocket progress updates.

### Steps

1. Navigate to **Tool Eval** and select a suite
2. Click **Param Tuner**
3. Select models to tune
4. Define the search space (or load a preset)
5. Review the compatibility matrix
6. Click **Run Tune**

The API returns `{"job_id": "...", "status": "submitted"}`.

### WebSocket Event Flow

```
POST /api/tool-eval/param-tune  -->  { job_id, status: "submitted" }
                                          |
                                 WebSocket events:
                                          |
    job_created  --->  tune_start  --->  combo_result (per combination)
                                                |
                                         job_progress (percentage + detail)
                                                |
                                         tune_complete (best_config, best_score)
```

Key WebSocket event types:

| Event | Payload | Description |
|-------|---------|-------------|
| `tune_start` | `tune_id`, `total_combos`, `models`, `suite_name` | Tuning session started |
| `combo_result` | Full result object (see below) | One combination completed |
| `job_progress` | `progress_pct`, `progress_detail` | Progress percentage |
| `tune_complete` | `best_config`, `best_score`, `duration_s` | Tuning finished |

### combo_result Payload

Each `combo_result` includes:

```json
{
  "combo_index": 3,
  "model_id": "gpt-4o",
  "provider_key": "openai",
  "model_name": "GPT-4o",
  "config": { "temperature": 0.5, "top_p": 0.8 },
  "overall_score": 0.85,
  "tool_accuracy": 90.0,
  "param_accuracy": 80.0,
  "latency_avg_ms": 1200,
  "cases_passed": 8,
  "cases_total": 10,
  "adjustments": [],
  "case_results": [...]
}
```

### Drill-Down Modal

Click any result row in the UI to see the **drill-down modal** with per-test-case details. The `case_results` array in each combo result contains:

| Field | Description |
|-------|-------------|
| `test_case_id` | ID of the test case |
| `prompt` | The user prompt |
| `expected_tool` | Expected tool name |
| `actual_tool` | Tool the model actually called |
| `expected_params` | Expected parameters |
| `actual_params` | Parameters the model provided |
| `tool_selection_score` | 0.0 or 1.0 |
| `param_accuracy` | 0.0-1.0 or null |
| `overall_score` | Weighted composite |
| `success` | Whether the API call succeeded |
| `error` | Error message if failed |
| `latency_ms` | Response time in ms |

### Dropped/Clamped Param Badges

When the 3-tier registry modifies a parameter, the UI displays badges with tooltips:

- **Clamped** (yellow): Parameter value was adjusted to fit the provider's valid range
- **Dropped** (red): Parameter was removed due to a hard conflict (e.g., Anthropic temp+top_p)
- **Warn** (orange): Parameter was passed through but the provider may not support it

Each badge shows the original value, the adjusted value, and the reason.

### Estimating Run Count

Before starting, review the total combinations:

```
total_combinations = product_of_all_param_values * num_models
total_api_calls = total_combinations * num_test_cases
```

Duplicate combinations (after provider validation and clamping) are automatically deduplicated per model.

> **Cost Awareness**: A search space with many parameters and fine steps can produce thousands of combinations. Each combination runs the full test suite against each model. Monitor costs carefully.

## The 3-Tier Parameter Registry

All parameter validation flows through `provider_params.py`, which defines a 3-tier architecture:

### Tier 1 -- Universal

`temperature`, `max_tokens`, `stop` -- supported by all providers. Ranges and defaults are provider-specific.

### Tier 2 -- Common

`top_p`, `top_k`, `frequency_penalty`, `presence_penalty`, `seed`, `reasoning_effort` -- supported by most providers with per-provider validation rules.

### Tier 3 -- Provider-Specific

Passthrough parameters sent directly to the LLM via LiteLLM. No validation, no clamping.

### Supported Providers (10)

| Provider | Key | Notes |
|----------|-----|-------|
| OpenAI | `openai` | GPT-5 locks temp to 1.0, O-series uses max_completion_tokens |
| Anthropic | `anthropic` | max_tokens required, temp+top_p mutual exclusion |
| Google Gemini | `gemini` | Gemini 3 degrades below temp 1.0 |
| Ollama | `ollama` | Full local param support (mirostat, num_ctx, etc.) |
| LM Studio | `lm_studio` | Similar to Ollama, supports repetition_penalty, min_p |
| Mistral | `mistral` | Limited top_k, seed sent as random_seed |
| DeepSeek | `deepseek` | R1 ignores sampling params in thinking mode |
| Cohere | `cohere` | Penalty range 0-1 only, top_p max 0.99 |
| xAI (Grok) | `xai` | Reasoning models reject penalties and stop |
| vLLM | `vllm` | Direct kwargs (no extra_body), guided_json support |

### Validation Pipeline

```
User params  -->  identify_provider()
                        |
                        v
              validate_params()
                        |
                        v
              clamp_temperature() (model-specific overrides)
                        |
                        v
              resolve_conflicts() (provider-specific rules)
                        |
                        v
              Apply skip_params (from config.yaml)
                        |
                        v
              Merge passthrough (Tier 3)
                        |
                        v
              Final kwargs for litellm.acompletion()
```

## Results

The tuner produces:

- **Per-combination scores**: Overall accuracy for each parameter set
- **Best combination**: The parameters that achieved the highest accuracy
- **Comparison table**: Side-by-side results for all combinations (sortable by any column)
- **Provider-specific adjustments**: Notes on parameter clamping, drops, or warnings
- **Per-test-case drill-down**: Click any result row to see individual case results
- **ETA tracking**: Real-time estimate of remaining time based on completion rate

Results are incrementally saved to the database, so partial results survive disconnections.

## Experiment Integration

When a param tune is linked to an experiment:

1. The best result is **auto-promoted** as a tool eval run in the experiment timeline
2. The experiment's **best config** is updated if the tune achieves a higher score
3. The promoted eval run includes a `promoted_from: "param_tune:{tune_id}"` marker

This allows the experiment timeline to show a continuous improvement curve across eval runs, param tunes, and prompt tunes.

## Tune History

```bash
# List tune runs
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/param-tune/history

# Get a specific tune run (includes full results)
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/param-tune/history/{tune_id}

# Delete a tune run
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/param-tune/history/{tune_id}
```

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tool-eval/param-tune` | Start param tuning (returns job_id) |
| `POST` | `/api/tool-eval/param-tune/cancel` | Cancel running tune |
| `GET` | `/api/tool-eval/param-tune/history` | List tune runs |
| `GET` | `/api/tool-eval/param-tune/history/{id}` | Get tune run details |
| `DELETE` | `/api/tool-eval/param-tune/history/{id}` | Delete tune run |

### Request Body for POST /api/tool-eval/param-tune

```json
{
  "suite_id": "required - tool suite ID",
  "models": ["model_id_1", "model_id_2"],
  "targets": [{"provider_key": "openai", "model_id": "gpt-4o"}],
  "search_space": {
    "temperature": { "min": 0.0, "max": 1.0, "step": 0.5 },
    "top_p": [0.8, 1.0]
  },
  "per_model_search_spaces": {},
  "experiment_id": "optional - link to experiment"
}
```

Either `models` (legacy model_id list) or `targets` (precise provider_key+model_id pairs) must be provided. `targets` format is preferred for avoiding ambiguity when the same model ID exists under multiple providers.

## Best Practices

- Start with a coarse grid (large steps) to identify promising regions
- Then refine with a fine grid around the best-performing area
- Use `temperature` and `tool_choice` as primary axes -- they have the most impact
- Keep the number of test cases manageable (5-10) for faster iteration
- Review the compatibility matrix before starting -- unsupported params waste combinations
- Use per-model search spaces when tuning across very different providers (e.g., OpenAI vs Ollama)
- Load vendor presets for local models to start with recommended settings
- Link tune runs to an experiment for automatic best-config tracking
