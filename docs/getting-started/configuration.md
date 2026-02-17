# Configuration

LLM Benchmark Studio uses a YAML-based configuration system. Each user gets their own configuration stored in the database, initialized from a default template on first login.

## Configuration Structure

The configuration has three top-level sections:

```yaml
defaults:
  max_tokens: 512
  temperature: 0.7
  context_tiers: [0]
  prompt: "Explain the concept of recursion in programming..."

prompt_templates:
  recursion:
    category: reasoning
    label: Explain Recursion
    prompt: "Explain the concept of recursion in programming..."

providers:
  openai:
    display_name: OpenAI
    api_key_env: OPENAI_API_KEY
    models:
      - id: gpt-4o
        display_name: GPT-4o
        context_window: 128000
```

### Defaults

| Field | Type | Description |
|-------|------|-------------|
| `max_tokens` | int | Default maximum output tokens (1-16384) |
| `temperature` | float | Default sampling temperature (0.0-2.0) |
| `context_tiers` | list[int] | Token counts for context window testing |
| `prompt` | string | Default benchmark prompt |

### Prompt Templates

Named prompt templates organized by category:

```yaml
prompt_templates:
  my_template:
    category: reasoning    # Category for grouping
    label: My Template     # Display name in the UI
    prompt: "Your prompt text here..."
```

Categories include: `reasoning`, `code`, `creative`, `short_qa`, `general`.

### Providers

Each provider configures an LLM API endpoint:

```yaml
providers:
  provider_key:
    display_name: Provider Name    # Shown in the UI
    api_key_env: API_KEY_ENV_VAR   # Environment variable for the API key
    api_base: https://api.example.com/v1  # Optional: custom API base URL
    api_key: literal-key           # Optional: direct API key (not recommended)
    model_id_prefix: provider      # Optional: LiteLLM model prefix
    models:
      - id: provider/model-name
        display_name: Model Name
        context_window: 128000
        max_output_tokens: 8000    # Optional: max output token limit
        skip_params:               # Optional: params to omit from API calls
          - temperature
        input_cost_per_mtok: 3.0   # Optional: custom $/1M input tokens
        output_cost_per_mtok: 15.0 # Optional: custom $/1M output tokens
```

#### Provider Fields

| Field | Required | Description |
|-------|----------|-------------|
| `display_name` | Yes | Human-readable name |
| `api_key_env` | No | Environment variable name for the API key |
| `api_base` | No | Custom API base URL (for local/self-hosted models) |
| `api_key` | No | Direct API key value |
| `model_id_prefix` | No | LiteLLM prefix (e.g., `anthropic`, `gemini`) |
| `models` | Yes | List of model configurations |

#### Model Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | LiteLLM model identifier |
| `display_name` | Yes | Human-readable name |
| `context_window` | No | Max context tokens (default: 128000) |
| `max_output_tokens` | No | Max output tokens for this model |
| `skip_params` | No | Parameters to exclude from API calls |
| `input_cost_per_mtok` | No | Custom input cost per million tokens |
| `output_cost_per_mtok` | No | Custom output cost per million tokens |

## LiteLLM Model ID Conventions

All LLM calls go through [LiteLLM](https://docs.litellm.ai/). Model IDs follow LiteLLM conventions:

| Provider | Model ID Format | Example |
|----------|----------------|---------|
| OpenAI | `model-name` (no prefix) | `gpt-4o` |
| Anthropic | `anthropic/model-name` | `anthropic/claude-sonnet-4-5` |
| Google Gemini | `gemini/model-name` | `gemini/gemini-2.5-flash` |
| LM Studio | `lm_studio/model-name` | `lm_studio/my-model` |
| Ollama | `ollama/model-name` | `ollama/llama3` |
| Custom OpenAI-compatible | `openai/model-name` with `api_base` | Any |

## Managing Configuration via the UI

### Adding a Provider

1. Navigate to Configuration
2. Click **Add Provider**
3. Fill in the provider key, display name, and API settings
4. Save

### Adding a Model

1. Open the provider in the Configuration screen
2. Click **Add Model**
3. Enter the model ID (following LiteLLM conventions), display name, and context window
4. Save

### Model Discovery

For providers that support it, click **Discover Models** to fetch available models from the provider's API. This works with:

- OpenAI
- Anthropic
- Google Gemini
- Any OpenAI-compatible endpoint (LM Studio, Ollama, vLLM)

## Managing Configuration via the API

All configuration changes are made through the REST API:

```bash
# Get current configuration
curl -H "Authorization: Bearer $TOKEN" http://localhost:8501/api/config

# Add a provider
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider_key": "my_provider", "display_name": "My Provider", "api_base": "http://localhost:1234/v1"}' \
  http://localhost:8501/api/config/provider

# Add a model to a provider
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider_key": "my_provider", "id": "my-model", "display_name": "My Model", "context_window": 32000}' \
  http://localhost:8501/api/config/model
```

See the [REST API Reference](../api/rest.md) for all configuration endpoints.

## Environment Variables

These environment variables control application behavior:

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET` | Auto-generated | Secret key for JWT token signing |
| `ADMIN_EMAIL` | (none) | Auto-promote this email to admin on startup |
| `ADMIN_PASSWORD` | (none) | Auto-create admin account with this password |
| `FERNET_KEY` | Auto-generated | Master encryption key for API key storage |
| `BENCHMARK_RATE_LIMIT` | `2000` | Max benchmark runs per user per hour |
| `COOKIE_SECURE` | `false` | Set to `true` for HTTPS deployments |
| `CORS_ORIGINS` | (none) | Comma-separated list of allowed CORS origins |
| `LOG_LEVEL` | `warning` | Uvicorn log level |
| `APP_VERSION` | `dev` | Application version (set by Docker build) |

## Local LLM Configuration

To benchmark local models running on LM Studio, Ollama, or vLLM:

```yaml
providers:
  lm_studio:
    display_name: LM Studio (Local)
    api_base: http://localhost:1234/v1
    api_key: not-needed
    model_id_prefix: lm_studio
    models:
      - id: lm_studio/my-local-model
        display_name: My Local Model
        context_window: 32000
```

!!! tip "Network Access"
    If running the application in Docker and the local LLM server is on the host machine, use `host.docker.internal` instead of `localhost` for the `api_base` URL.
