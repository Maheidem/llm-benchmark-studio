# Configuration Schema

## Provider Parameter Registry

The provider parameter registry defines the parameter support, ranges, and conflict rules for each LLM provider. It uses a three-tier architecture implemented in `provider_params.py`.

### Three-Tier Architecture

**Tier 1 -- Universal Parameters**

Supported by all providers:

| Parameter | Type | Description |
|-----------|------|-------------|
| `temperature` | float | Sampling temperature |
| `max_tokens` | int | Maximum output tokens |
| `stop` | string_array | Stop sequences |

**Tier 2 -- Common Parameters**

Supported by most providers (with variations):

| Parameter | Type | Description |
|-----------|------|-------------|
| `top_p` | float | Nucleus sampling |
| `top_k` | int | Top-k sampling |
| `frequency_penalty` | float | Frequency penalty |
| `presence_penalty` | float | Presence penalty |
| `seed` | int | Random seed |
| `reasoning_effort` | enum | Reasoning mode (none/low/medium/high) |

**Tier 3 -- Provider-Specific Parameters**

JSON passthrough for any LiteLLM-supported parameter. Bypasses validation. Passed via `provider_params.passthrough` in request bodies.

### Provider Support Matrix

| Provider | Temp Range | top_p | top_k | freq_penalty | pres_penalty | seed | reasoning |
|----------|-----------|-------|-------|--------------|-------------|------|-----------|
| OpenAI | 0-2 | Yes | No | Yes (-2 to 2) | Yes (-2 to 2) | Deprecated | Yes (o-series, GPT-5) |
| Anthropic | 0-1 | Yes* | Yes (1-500) | No | No | No | Yes (maps to thinking.budget_tokens) |
| Gemini | 0-2 | Yes | Yes (1-100) | Yes (-2 to 2) | Yes (-2 to 2) | Yes | Yes (maps to thinkingConfig) |
| Ollama | 0-2 | Yes | Yes (1-500) | Yes (-2 to 2) | Yes (-2 to 2) | Yes | No |
| LM Studio | 0-2 | Yes | Yes (1-500) | Yes (-2 to 2) | Yes (-2 to 2) | Yes | No |
| Mistral | 0-1.5 | Yes | Partial | Yes (-2 to 2) | Yes (-2 to 2) | Yes | No |
| DeepSeek | 0-2 | Yes | No | Yes (-2 to 2) | Yes (-2 to 2) | No | Yes (binary: on/off) |
| Cohere | 0-1 | Yes (max 0.99) | Yes (0-500) | Yes (0-1) | Yes (0-1) | Yes | No |
| xAI (Grok) | 0-2 | Yes | No | Yes (-2 to 2) | Yes (-2 to 2) | Yes | Partial |
| vLLM | 0-2 | Yes | Yes (1-500) | Yes (-2 to 2) | Yes (-2 to 2) | Yes | No |

*Anthropic: Cannot use top_p and temperature simultaneously on newer models.

### Tier 3 Provider-Specific Passthrough Examples

Each provider has documented Tier 3 parameters that can be passed via the `passthrough` field:

| Provider | Parameters |
|----------|-----------|
| OpenAI | `service_tier` (auto/default/flex/priority), `prediction`, `web_search_options` |
| Anthropic | `cache_control`, `inference_geo` |
| Gemini | `safety_settings` |
| Ollama | `mirostat` (0/1/2), `mirostat_eta`, `mirostat_tau`, `repetition_penalty`, `num_ctx`, `min_p`, `keep_alive` |
| LM Studio | `repetition_penalty`, `min_p` |
| Mistral | `safe_prompt` |
| Cohere | `safety_mode` (CONTEXTUAL/STRICT/OFF), `documents`, `citation_options` |
| vLLM | `repetition_penalty`, `min_p`, `typical_p`, `guided_json`, `guided_choice`, `best_of`, `ignore_eos` |

Note: vLLM parameters must be passed as direct kwargs, not via `extra_body`.

### Model-Specific Overrides

Some models have locked or modified parameters:

- **GPT-5**: Temperature locked to 1.0
- **O-series (o1, o3, o4)**: Temperature locked to 1.0; uses `max_completion_tokens` instead of `max_tokens`; stop sequences not supported
- **Gemini 3**: Temperature minimum clamped to 1.0

### Conflict Resolution

The system resolves parameter conflicts using four action types:

| Action | Description |
|--------|-------------|
| `drop` | Parameter removed from request (hard mutual exclusion) |
| `warn` | Parameter passed through unchanged with a warning |
| `rename` | Parameter renamed/remapped (value preserved) |
| `clamp` | Value adjusted to provider's valid range |

**Conflict rules by provider:**

| Conflict | Action | Resolution |
|----------|--------|-----------|
| Anthropic: temperature + top_p both set | drop | Drops top_p, keeps temperature |
| Anthropic: freq_penalty / pres_penalty / seed | warn | Passed through (provider may reject) |
| OpenAI O-series: max_tokens | rename | Converts to max_completion_tokens |
| OpenAI: top_k set | warn | Passed through (provider may reject) |
| DeepSeek R1: thinking mode + sampling params | warn | Params have no effect in thinking mode |
| DeepSeek: top_k or seed | warn | Passed through (provider may reject) |
| xAI: reasoning + penalties/stop | warn | Provider may reject when reasoning active |
| xAI: top_k set | warn | Passed through (provider may reject) |
| Cohere: penalty > 1.0 | clamp | Clamped to 0-1 range |
| Cohere: top_p > 0.99 | clamp | Clamped to 0.99 |
| Mistral: top_k set | warn | Limited support |

### Provider Identification

The system identifies which provider registry to use through a resolution chain:

1. **Explicit provider_key** from config.yaml (if it matches a registry key)
2. **Model ID prefix** detection (e.g., `anthropic/`, `gemini/`, `ollama/`)
3. **Fallback** to `_unknown` (OpenAI-compatible defaults)

Recognized prefixes: `anthropic/`, `gemini/`, `vertex_ai/`, `ollama/`, `ollama_chat/`, `lm_studio/`, `mistral/`, `deepseek/`, `cohere/`, `cohere_chat/`, `xai/`, `vllm/`, `openai/`.

### Parameter Validation API

Validate parameters before sending them to a provider:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider_key": "openai",
    "model_id": "gpt-4o",
    "params": {
      "temperature": 1.5,
      "top_p": 0.9,
      "top_k": 50
    }
  }' \
  http://localhost:8501/api/provider-params/validate
```

**Response:**

```json
{
  "valid": false,
  "has_warnings": true,
  "adjustments": [
    {
      "param": "top_k",
      "original": 50,
      "adjusted": 50,
      "action": "warn",
      "reason": "OpenAI may not support top_k -- passing through"
    }
  ],
  "warnings": [],
  "resolved_params": {
    "temperature": 1.5,
    "top_p": 0.9,
    "top_k": 50
  }
}
```

The `valid` field is `false` when any parameter was dropped or clamped. Warnings (pass-through with notice) do not invalidate the request.

### Full Registry API

Get the complete provider parameter registry:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/provider-params/registry
```

### build_litellm_kwargs

The `build_litellm_kwargs()` function is the central pipeline for preparing parameters for LiteLLM calls. It:

1. Merges explicit temperature/max_tokens with provider_params
2. Runs validation and conflict resolution
3. Applies model-specific `skip_params` from config
4. Merges Tier 3 passthrough parameters (bypass validation)
5. Returns a clean kwargs dict ready for `litellm.completion()`

---

## Search Space Presets

The param tuner supports saved search space presets for reuse across tuning sessions. Presets are stored per-user in the Phase 10 settings.

### Custom Presets

Users can save up to 20 custom presets via the Settings API:

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "param_tuner": {
      "presets": [
        {
          "name": "Conservative Sampling",
          "search_space": {
            "temperature": [0.0, 0.3, 0.5],
            "top_p": [0.8, 0.95]
          }
        },
        {
          "name": "Aggressive Exploration",
          "search_space": {
            "temperature": [0.5, 0.8, 1.2, 1.5],
            "top_p": [0.7, 0.8, 0.9, 1.0],
            "top_k": [20, 50, 100]
          }
        }
      ]
    }
  }' \
  http://localhost:8501/api/settings/phase10
```

### Built-in Vendor Presets

The system includes vendor-recommended presets that are always available (returned from `POST /api/param-support/seed`):

**Qwen3 Coder 30B (Recommended):**

```json
{
  "name": "Qwen3 Coder 30B (Recommended)",
  "builtin": true,
  "search_space": {
    "temperature": [0.7],
    "top_p": [0.8],
    "top_k": [20]
  },
  "system_prompt": "Greedy decoding (temp=0) worsens quality. Always use sampling."
}
```

**GLM-4.7 Flash (Z.AI Recommended):**

```json
{
  "name": "GLM-4.7 Flash (Z.AI Recommended)",
  "builtin": true,
  "search_space": {
    "temperature": [0.8],
    "top_p": [0.6],
    "top_k": [2]
  },
  "system_prompt": "Very low top_k recommended for MoE architecture."
}
```

Built-in presets have `"builtin": true` and cannot be deleted by users.

### Per-Model Search Spaces

The param tuner supports `per_model_search_spaces` which allows different search spaces per model in a single tuning run:

```json
{
  "suite_id": "suite-id",
  "models": ["gpt-4o", "ollama/qwen3-coder"],
  "search_space": {
    "temperature": [0.0, 0.5, 1.0]
  },
  "per_model_search_spaces": {
    "ollama/qwen3-coder": {
      "temperature": [0.7],
      "top_p": [0.8],
      "top_k": [20]
    }
  }
}
```

Models listed in `per_model_search_spaces` use their custom space; all others fall back to the global `search_space`.

---

## Configuration YAML Schema

The configuration file follows this schema:

```yaml
defaults:
  max_tokens: <int>           # 1-128000
  temperature: <float>        # 0.0-2.0
  context_tiers: <list[int]>  # Token counts
  prompt: <string>            # Default prompt

prompt_templates:
  <template_key>:
    category: <string>        # reasoning, code, creative, short_qa, general
    label: <string>           # Display name
    prompt: <string>          # Prompt text

providers:
  <provider_key>:
    display_name: <string>
    api_key_env: <string>     # Optional: env var name for API key
    api_base: <string>        # Optional: custom API base URL
    api_key: <string>         # Optional: inline API key
    model_id_prefix: <string> # Optional: LiteLLM prefix (e.g., "anthropic")
    models:
      - id: <string>                    # LiteLLM model ID
        display_name: <string>
        context_window: <int>           # Optional, default 128000
        max_output_tokens: <int>        # Optional
        skip_params: <list[string]>     # Optional: params to omit (e.g., ["temperature"])
        system_prompt: <string>         # Optional: per-model system prompt
        input_cost_per_mtok: <float>    # Optional: input cost per million tokens
        output_cost_per_mtok: <float>   # Optional: output cost per million tokens
```

Each user gets their own copy of the configuration stored in the `user_configs` table. The base configuration comes from `config.yaml` and is copied on first access.

---

## Database Schema

### Users

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',  -- 'admin' or 'user'
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    onboarding_completed INTEGER DEFAULT 0
);
```

### Refresh Tokens

```sql
CREATE TABLE refresh_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### User API Keys

```sql
CREATE TABLE user_api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider_key TEXT NOT NULL,
    key_name TEXT NOT NULL,
    encrypted_value TEXT NOT NULL,  -- Fernet encrypted
    updated_at TEXT,
    UNIQUE(user_id, provider_key)
);
```

### User Configs

```sql
CREATE TABLE user_configs (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    config_json TEXT NOT NULL,  -- Full provider/model config
    updated_at TEXT NOT NULL
);
```

### Benchmark Runs

```sql
CREATE TABLE benchmark_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    prompt TEXT,
    context_tiers TEXT,       -- JSON array
    results_json TEXT NOT NULL, -- Full results
    metadata TEXT              -- JSON (source, schedule_id, etc.)
);
```

### Tool Suites

```sql
CREATE TABLE tool_suites (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    tools_json TEXT NOT NULL,   -- OpenAI function calling schema
    system_prompt TEXT          -- Optional suite-level system prompt
);
```

### Tool Test Cases

```sql
CREATE TABLE tool_test_cases (
    id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL REFERENCES tool_suites(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    expected_tool TEXT,         -- string or JSON array
    expected_params TEXT,       -- JSON object
    param_scoring TEXT NOT NULL DEFAULT 'exact',
    multi_turn_config TEXT,     -- JSON for multi-turn cases
    scoring_config_json TEXT    -- JSON for fuzzy scoring rules
);
```

### Tool Eval Runs

```sql
CREATE TABLE tool_eval_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    suite_id TEXT NOT NULL,
    suite_name TEXT,
    models_json TEXT NOT NULL,
    results_json TEXT,
    summary_json TEXT,
    config_json TEXT,           -- Eval configuration snapshot
    experiment_id TEXT,
    timestamp TEXT NOT NULL
);
```

### Experiments

```sql
CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    suite_id TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    baseline_eval_id TEXT,
    baseline_score REAL,
    best_score REAL,
    best_source TEXT,
    best_source_id TEXT,
    best_config_json TEXT,
    suite_snapshot_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### Param Tune Runs

```sql
CREATE TABLE param_tune_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    suite_id TEXT NOT NULL,
    models_json TEXT,
    search_space_json TEXT,
    results_json TEXT,
    best_config_json TEXT,
    best_score REAL,
    status TEXT,
    experiment_id TEXT,
    timestamp TEXT NOT NULL
);
```

### Prompt Tune Runs

```sql
CREATE TABLE prompt_tune_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    suite_id TEXT NOT NULL,
    mode TEXT,
    target_models_json TEXT,
    meta_model TEXT,
    results_json TEXT,
    best_prompt TEXT,
    best_score REAL,
    status TEXT,
    experiment_id TEXT,
    timestamp TEXT NOT NULL
);
```

### Judge Reports

```sql
CREATE TABLE judge_reports (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    eval_run_id TEXT,
    judge_model TEXT,
    mode TEXT,
    verdicts_json TEXT,
    report_json TEXT,
    overall_grade TEXT,
    overall_score REAL,
    experiment_id TEXT,
    status TEXT,
    timestamp TEXT NOT NULL
);
```

### Jobs

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    params_json TEXT,
    progress_pct INTEGER DEFAULT 0,
    progress_detail TEXT DEFAULT '',
    result_ref TEXT,
    error_msg TEXT,
    timeout_seconds INTEGER DEFAULT 7200,
    timeout_at TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
```

### Schedules

```sql
CREATE TABLE schedules (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    models_json TEXT NOT NULL,
    max_tokens INTEGER DEFAULT 512,
    temperature REAL DEFAULT 0.7,
    interval_hours INTEGER NOT NULL,
    enabled INTEGER DEFAULT 1,
    last_run TEXT,
    next_run TEXT NOT NULL
);
```

### Rate Limits

```sql
CREATE TABLE rate_limits (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    benchmarks_per_hour INTEGER DEFAULT 20,
    max_concurrent INTEGER DEFAULT 1,
    max_runs_per_benchmark INTEGER DEFAULT 10,
    updated_at TEXT,
    updated_by TEXT
);
```

### Audit Log

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,              -- NULL-able (preserved when user deleted)
    username TEXT,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    detail TEXT,               -- JSON
    ip_address TEXT,
    user_agent TEXT,
    timestamp TEXT NOT NULL
);
```
