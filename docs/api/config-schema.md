# Configuration Schema

## Provider Parameter Registry

The provider parameter registry defines the parameter support, ranges, and conflict rules for each LLM provider. It uses a three-tier architecture.

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

JSON passthrough for any LiteLLM-supported parameter. Bypasses validation.

### Provider Support Matrix

| Provider | temp range | top_p | top_k | freq_penalty | pres_penalty | seed | reasoning |
|----------|-----------|-------|-------|--------------|-------------|------|-----------|
| OpenAI | 0-2 | Yes | No | Yes | Yes | Deprecated | Yes |
| Anthropic | 0-1 | Yes* | Yes | No | No | No | Yes |
| Gemini | 0-2 | Yes | Yes | Yes | Yes | Yes | Yes |
| Ollama | 0-2 | Yes | Yes | Yes | Yes | Yes | No |
| LM Studio | 0-2 | Yes | Yes | Yes | Yes | Yes | No |
| Mistral | 0-1.5 | Yes | Partial | Yes | Yes | Yes | No |
| DeepSeek | 0-2 | Yes | No | Yes | Yes | No | Yes |
| Cohere | 0-1 | Yes (max 0.99) | Yes | Yes (0-1) | Yes (0-1) | Yes | No |
| xAI (Grok) | 0-2 | Yes | No | Yes | Yes | Yes | Partial |
| vLLM | 0-2 | Yes | Yes | Yes | Yes | Yes | No |

*Anthropic: Cannot use top_p and temperature simultaneously on newer models.

### Model-Specific Overrides

Some models have locked parameters:

- **GPT-5**: Temperature locked to 1.0
- **O-series (o1, o3, o4)**: Temperature locked to 1.0; uses `max_completion_tokens` instead of `max_tokens`; stop sequences not supported
- **Gemini 3**: Temperature minimum clamped to 1.0

### Conflict Resolution

The system automatically resolves parameter conflicts:

| Conflict | Resolution |
|----------|-----------|
| Anthropic: temperature + top_p both set | Drops top_p, keeps temperature |
| Anthropic: thinking enabled + temperature/top_k | Drops temperature and top_k |
| OpenAI O-series: max_tokens | Converts to max_completion_tokens |
| DeepSeek R1: thinking mode + sampling params | Warning (params have no effect) |
| xAI: reasoning mode + penalties/stop | Drops penalties and stop sequences |
| Cohere: penalty > 1.0 | Clamps to 0-1 range |

### Parameter Validation API

Validate parameters before sending them to a provider:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
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
  "adjustments": [
    {
      "param": "top_k",
      "original": 50,
      "adjusted": null,
      "reason": "OpenAI does not support top_k"
    }
  ],
  "warnings": [],
  "resolved_params": {
    "temperature": 1.5,
    "top_p": 0.9
  }
}
```

### Full Registry API

Get the complete provider parameter registry:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/provider-params/registry
```

## Configuration YAML Schema

The configuration file follows this schema:

```yaml
defaults:
  max_tokens: <int>           # 1-16384
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
    api_key_env: <string>     # Optional
    api_base: <string>        # Optional
    api_key: <string>         # Optional
    model_id_prefix: <string> # Optional
    models:
      - id: <string>                    # LiteLLM model ID
        display_name: <string>
        context_window: <int>           # Optional, default 128000
        max_output_tokens: <int>        # Optional
        skip_params: <list[string]>     # Optional
        input_cost_per_mtok: <float>    # Optional
        output_cost_per_mtok: <float>   # Optional
```

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

### User API Keys

```sql
CREATE TABLE user_api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider_key TEXT NOT NULL,
    key_name TEXT NOT NULL,
    encrypted_value TEXT NOT NULL,  -- Fernet encrypted
    UNIQUE(user_id, provider_key)
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
    tools_json TEXT NOT NULL    -- OpenAI function calling schema
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
    multi_turn_config TEXT      -- JSON for multi-turn cases
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
