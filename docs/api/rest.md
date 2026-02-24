# REST API Reference

All API endpoints require authentication via JWT bearer token unless noted otherwise. Tokens are obtained from the authentication endpoints.

Base URL: `http://localhost:8501`

All long-running operations (benchmarks, tool evals, param tuning, prompt tuning, judge) return a `job_id` immediately. Real-time progress is delivered via [WebSocket](websocket.md), not the HTTP response.

---

## Authentication

### Register

Create a new user account. The first registered user is automatically promoted to admin. Users matching the `ADMIN_EMAIL` environment variable are also auto-promoted.

```
POST /api/auth/register
```

**Request body:**

```json
{
  "email": "user@example.com",
  "password": "minimum8chars"
}
```

**Response:**

```json
{
  "user": { "id": "abc123", "email": "user@example.com", "role": "user" },
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

A refresh token is set as an HttpOnly cookie. Access tokens expire after 24 hours.

### Login

```
POST /api/auth/login
```

**Request body:**

```json
{
  "email": "user@example.com",
  "password": "your-password"
}
```

**Response:** Same format as Register. Login is rate-limited (5 attempts per 5 minutes per IP, 15-minute lockout).

### Refresh Token

```
POST /api/auth/refresh
```

Uses the HttpOnly refresh token cookie to issue a new access token. No request body needed.

### Logout

```
POST /api/auth/logout
```

Revokes the refresh token.

### Get Current User

```
GET /api/auth/me
```

Returns the authenticated user's profile.

### Change Password

```
POST /api/auth/change-password
```

**Request body:**

```json
{
  "current_password": "old-password",
  "new_password": "new-password-min-8"
}
```

### Generate CLI Token

```
POST /api/auth/cli-token
```

Generates a long-lived JWT (30 days) for CLI usage. CLI tokens are accepted for both REST and WebSocket authentication.

**Response:**

```json
{
  "token": "eyJ...",
  "expires_in_days": 30
}
```

---

## Health & SEO

### Health Check

```
GET /healthz
```

No authentication required.

```json
{"status": "ok", "version": "1.2.0"}
```

### Robots.txt

```
GET /robots.txt
```

### Sitemap

```
GET /sitemap.xml
```

---

## Configuration

### Get Configuration

```
GET /api/config
```

Returns the user's providers, models, and defaults. Each provider includes its models with `model_id`, `display_name`, `context_window`, `max_output_tokens`, `skip_params`, and any custom fields (costs, system_prompt, etc.).

### Add Provider

```
POST /api/config/provider
```

```json
{
  "provider_key": "my_provider",
  "display_name": "My Provider",
  "api_base": "http://localhost:1234/v1",
  "api_key_env": "MY_API_KEY",
  "model_id_prefix": "my_provider"
}
```

### Update Provider

```
PUT /api/config/provider
```

```json
{
  "provider_key": "my_provider",
  "display_name": "Updated Name",
  "api_base": "http://new-url:1234/v1"
}
```

### Delete Provider

```
DELETE /api/config/provider
```

```json
{ "provider_key": "my_provider" }
```

### Add Model

```
POST /api/config/model
```

```json
{
  "provider_key": "openai",
  "id": "gpt-4o-mini",
  "display_name": "GPT-4o Mini",
  "context_window": 128000
}
```

### Update Model

```
PUT /api/config/model
```

Supports updating display_name, context_window, max_output_tokens, skip_params, system_prompt, and custom_fields. Can also rename the model via `new_model_id`.

```json
{
  "model_id": "gpt-4o",
  "provider_key": "openai",
  "display_name": "GPT-4o Updated",
  "context_window": 128000,
  "skip_params": ["temperature"],
  "system_prompt": "You are a helpful assistant.",
  "input_cost_per_mtok": 2.50,
  "output_cost_per_mtok": 10.00
}
```

### Delete Model

```
DELETE /api/config/model
```

```json
{ "provider_key": "openai", "model_id": "gpt-4o-mini" }
```

### Prompt Templates

```
GET /api/config/prompts           # List templates
POST /api/config/prompts          # Add template
```

**Add template:**

```json
{
  "key": "my_template",
  "label": "My Template",
  "category": "reasoning",
  "prompt": "Explain the concept of..."
}
```

---

## API Keys

### Per-User Keys

Keys are Fernet-encrypted and stored per-user. They override global environment keys.

```
GET /api/keys                     # List user's key status per provider
PUT /api/keys                     # Set/update a key
DELETE /api/keys                  # Remove a key
```

**Set a key:**

```json
{ "provider_key": "openai", "value": "sk-..." }
```

**Response from GET:** Returns per-provider status showing `has_user_key`, `has_global_key`, and display metadata. Never returns plaintext keys.

### Global Environment Keys (Admin Only)

```
GET /api/env                      # List env keys (masked)
PUT /api/env                      # Set/update env key
DELETE /api/env                   # Remove env key
```

Only provider API key environment variables from the safe list can be modified: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `MISTRAL_API_KEY`, `COHERE_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `XAI_API_KEY`, `DEEPINFRA_API_KEY`, `CEREBRAS_API_KEY`, `SAMBANOVA_API_KEY`, `OPENROUTER_API_KEY`.

---

## Benchmarks

### Run Benchmark

```
POST /api/benchmark
```

Submits a benchmark job to the JobRegistry. Returns a `job_id` immediately. Progress and results are delivered via [WebSocket](websocket.md).

**Request body:**

```json
{
  "models": ["gpt-4o", "anthropic/claude-sonnet-4-5"],
  "runs": 3,
  "max_tokens": 512,
  "temperature": 0.7,
  "prompt": "Explain recursion in programming",
  "context_tiers": [0, 5000],
  "warmup": true,
  "provider_params": {
    "top_p": 0.9,
    "passthrough": { "service_tier": "flex" }
  }
}
```

Alternatively, use precise `targets` instead of `models`:

```json
{
  "targets": [
    { "provider_key": "openai", "model_id": "gpt-4o" },
    { "provider_key": "lm_studio", "model_id": "lm_studio/qwen3-coder" }
  ],
  "runs": 1
}
```

**Response:**

```json
{
  "job_id": "a1b2c3d4e5f6...",
  "status": "submitted"
}
```

**WebSocket events for benchmark jobs:**

| Event Type | Description |
|------------|-------------|
| `job_created` | Job registered (pending or queued) |
| `job_started` | Job execution began |
| `benchmark_init` | Target list, run count, context tiers |
| `job_progress` | Progress percentage and detail string |
| `benchmark_result` | Individual run metrics (model, TPS, TTFT, cost) |
| `benchmark_skipped` | Context tier skipped (exceeds model window) |
| `job_completed` | All runs finished, includes `result_ref` (run ID) |
| `job_failed` | Error occurred |
| `job_cancelled` | Benchmark was cancelled |

### Cancel Benchmark

```
POST /api/benchmark/cancel
```

**Request body (optional):**

```json
{ "job_id": "a1b2c3d4..." }
```

If `job_id` is omitted, cancels the most recent active benchmark for the current user (backward compatibility).

### Rate Limit Status

```
GET /api/user/rate-limit
```

```json
{ "limit": 20, "remaining": 15, "window": "1 hour" }
```

---

## History

```
GET /api/history                  # List benchmark runs
GET /api/history/{run_id}         # Get specific run with full results
DELETE /api/history/{run_id}      # Delete a run
```

---

## Jobs

Job tracking endpoints for monitoring all background operations.

### List Jobs

```
GET /api/jobs
```

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Comma-separated status filter (e.g. `running,queued`) |
| `limit` | int | Max results (default 20) |

**Response:**

```json
{
  "jobs": [
    {
      "id": "abc123",
      "job_type": "benchmark",
      "status": "running",
      "progress_pct": 45,
      "progress_detail": "Benchmark: 3 models, 2 runs each",
      "created_at": "2026-02-20T10:00:00Z",
      "started_at": "2026-02-20T10:00:01Z"
    }
  ]
}
```

### Get Job

```
GET /api/jobs/{job_id}
```

Returns full job details including `params_json`, `result_ref`, `error_msg`, timing fields.

### Cancel Job

```
POST /api/jobs/{job_id}/cancel
```

Cancels a specific job. Works for pending, queued, and running jobs. Also cleans up orphaned linked tune runs if the job is already terminal.

### Admin: List All Active Jobs

```
GET /api/admin/jobs
```

Admin only. Lists all active jobs across all users.

### Admin: Cancel Any Job

```
POST /api/admin/jobs/{job_id}/cancel
```

Admin only. Can cancel any user's job.

**Job statuses:**

| Status | Description |
|--------|-------------|
| `pending` | Created, about to start |
| `queued` | Waiting for concurrency slot |
| `running` | Actively executing |
| `done` | Completed successfully |
| `failed` | Error occurred |
| `cancelled` | Cancelled by user or admin |
| `interrupted` | Server shutdown or timeout |

**Job types:** `benchmark`, `tool_eval`, `param_tune`, `prompt_tune`, `judge`, `judge_compare`, `schedule`

---

## Tool Suites

```
GET /api/tool-suites                              # List suites
POST /api/tool-suites                             # Create suite
GET /api/tool-suites/{suite_id}                   # Get suite with tools and test cases
PUT /api/tool-suites/{suite_id}                   # Update suite (name, description, tools, system_prompt)
PATCH /api/tool-suites/{suite_id}                 # Patch individual fields (lighter than PUT)
DELETE /api/tool-suites/{suite_id}                # Delete suite and all cases
GET /api/tool-suites/{suite_id}/export            # Export suite as downloadable JSON
GET /api/tool-suites/{suite_id}/cases             # List test cases
POST /api/tool-suites/{suite_id}/cases            # Add case(s) -- single or bulk via "cases" array
PUT /api/tool-suites/{suite_id}/cases/{case_id}   # Update case
DELETE /api/tool-suites/{suite_id}/cases/{case_id} # Delete case
```

### Import Suite from JSON

```
POST /api/tool-eval/import
```

See [Tool Calling Evaluation](../guide/tool-eval.md) for the JSON format.

### Import Example

```
GET /api/tool-eval/import/example
```

Returns a downloadable example JSON template showing the expected import format.

### Import from MCP Server

```
POST /api/mcp/discover            # Discover tools from MCP server
POST /api/mcp/import              # Import MCP tools as a suite
```

**Discover request:**

```json
{ "url": "http://localhost:3000/sse" }
```

**Import request:**

```json
{
  "tools": [{ "name": "...", "description": "...", "inputSchema": {...} }],
  "suite_name": "My MCP Suite",
  "suite_description": "Imported from MCP server",
  "generate_test_cases": true
}
```

When `generate_test_cases` is true, one sample test case is auto-generated per tool using realistic placeholder values derived from parameter names.

---

## Tool Eval

### Run Eval

```
POST /api/tool-eval
```

Submits a tool calling evaluation via the JobRegistry. Returns `job_id` immediately. Progress via WebSocket.

```json
{
  "suite_id": "suite-id",
  "models": ["gpt-4o"],
  "temperature": 0.0,
  "tool_choice": "required",
  "provider_params": { "top_p": 0.9 },
  "system_prompt": "Always use tools when available.",
  "experiment_id": "exp-id",
  "judge": {
    "enabled": true,
    "mode": "live_inline",
    "judge_model": "anthropic/claude-sonnet-4-5",
    "custom_instructions": "Focus on parameter completeness"
  }
}
```

Supports `targets` array for precise provider+model selection (same as benchmarks).

**Response:**

```json
{ "job_id": "abc123", "status": "submitted" }
```

### Cancel Eval

```
POST /api/tool-eval/cancel
```

```json
{ "job_id": "abc123" }
```

### Eval History

```
GET /api/tool-eval/history                 # List runs (includes summary per model)
GET /api/tool-eval/history/{eval_id}       # Get full run details with per-case results
DELETE /api/tool-eval/history/{eval_id}    # Delete run
```

---

## Param Tuner

Grid search over parameter combinations to find optimal settings for tool calling accuracy.

### Run Param Tune

```
POST /api/tool-eval/param-tune
```

Submits via JobRegistry. Returns `job_id` immediately.

```json
{
  "suite_id": "suite-id",
  "models": ["gpt-4o", "anthropic/claude-sonnet-4-5"],
  "search_space": {
    "temperature": [0.0, 0.3, 0.7],
    "top_p": [0.8, 0.95],
    "top_k": [20, 50]
  },
  "per_model_search_spaces": {
    "gpt-4o": { "temperature": [0.0, 0.5] },
    "anthropic/claude-sonnet-4-5": { "temperature": [0.0, 0.3] }
  },
  "experiment_id": "exp-id"
}
```

The `per_model_search_spaces` field is optional and overrides the global `search_space` for specific models.

**Response:**

```json
{ "job_id": "abc123", "status": "submitted" }
```

### Cancel Param Tune

```
POST /api/tool-eval/param-tune/cancel
```

```json
{ "job_id": "abc123" }
```

### Param Tune History

```
GET /api/tool-eval/param-tune/history              # List runs
GET /api/tool-eval/param-tune/history/{tune_id}    # Get details (all combos + best config)
DELETE /api/tool-eval/param-tune/history/{tune_id} # Delete
```

---

## Prompt Tuner

AI-powered system prompt optimization using a meta-model to generate and evaluate prompt variations.

### Run Prompt Tune

```
POST /api/tool-eval/prompt-tune
```

Submits via JobRegistry. Returns `job_id` immediately.

```json
{
  "suite_id": "suite-id",
  "mode": "quick",
  "target_models": ["gpt-4o"],
  "meta_model": "anthropic/claude-sonnet-4-5",
  "meta_provider_key": "anthropic",
  "base_prompt": "You are a helpful assistant that uses tools.",
  "config": {
    "population_size": 5,
    "generations": 1
  },
  "experiment_id": "exp-id"
}
```

| Mode | Description |
|------|-------------|
| `quick` | Single generation of prompt variations |
| `evolutionary` | Multiple generations with mutation of winning prompts |

**Response:**

```json
{ "job_id": "abc123", "status": "submitted" }
```

### Cancel Prompt Tune

```
POST /api/tool-eval/prompt-tune/cancel
```

```json
{ "job_id": "abc123" }
```

### Cost Estimate

```
GET /api/tool-eval/prompt-tune/estimate
```

**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `suite_id` | string | | Suite to estimate for |
| `mode` | string | `quick` | `quick` or `evolutionary` |
| `population_size` | int | 5 | Prompts per generation |
| `generations` | int | 1/3 | Generations to run |
| `num_models` | int | 1 | Number of target models |

**Response:**

```json
{
  "total_prompt_generations": 5,
  "total_eval_calls": 50,
  "total_api_calls": 51,
  "estimated_duration_s": 105,
  "warning": null
}
```

### Prompt Tune History

```
GET /api/tool-eval/prompt-tune/history              # List runs
GET /api/tool-eval/prompt-tune/history/{tune_id}    # Get details (all prompts + best)
DELETE /api/tool-eval/prompt-tune/history/{tune_id} # Delete
```

---

## Judge

AI-powered evaluation quality assessment. Uses a judge model to grade tool calling results.

### Run Post-Eval Judge

```
POST /api/tool-eval/judge
```

Submits via JobRegistry. Returns `job_id` immediately.

```json
{
  "eval_run_id": "eval-run-id",
  "judge_model": "anthropic/claude-sonnet-4-5",
  "judge_provider_key": "anthropic",
  "mode": "post_eval",
  "custom_instructions": "Focus on parameter completeness",
  "concurrency": 4,
  "experiment_id": "exp-id"
}
```

### Run Comparative Judge

```
POST /api/tool-eval/judge/compare
```

Compares two eval runs head-to-head. Requires common test cases between the runs.

```json
{
  "eval_run_id_a": "run-a-id",
  "eval_run_id_b": "run-b-id",
  "judge_model": "anthropic/claude-sonnet-4-5",
  "judge_provider_key": "anthropic",
  "concurrency": 4,
  "experiment_id": "exp-id"
}
```

### Cancel Judge

```
POST /api/tool-eval/judge/cancel
```

```json
{ "job_id": "abc123" }
```

### Judge Reports

```
GET /api/tool-eval/judge/reports                   # List reports
GET /api/tool-eval/judge/reports/{report_id}       # Get full report with verdicts
DELETE /api/tool-eval/judge/reports/{report_id}    # Delete report
```

---

## Experiments

Experiments group related eval, tune, and judge runs for A/B testing workflows.

### List Experiments

```
GET /api/experiments
```

### Create Experiment

```
POST /api/experiments
```

```json
{
  "name": "Temperature Optimization",
  "description": "Testing temperature ranges for weather API",
  "suite_id": "suite-id",
  "baseline_eval_id": "eval-id",
  "snapshot_suite": true
}
```

### Get Experiment

```
GET /api/experiments/{experiment_id}
```

Returns experiment with parsed `best_config` and `suite_name`.

### Update Experiment

```
PUT /api/experiments/{experiment_id}
```

```json
{
  "name": "Updated Name",
  "description": "Updated description",
  "status": "archived"
}
```

### Delete Experiment

```
DELETE /api/experiments/{experiment_id}
```

### Pin Baseline

```
PUT /api/experiments/{experiment_id}/baseline
```

Pin or re-pin a baseline eval run for score comparison.

```json
{ "eval_run_id": "eval-id" }
```

### Get Timeline

```
GET /api/experiments/{experiment_id}/timeline
```

Returns ordered timeline of all linked runs (eval, param_tune, prompt_tune, judge) with scores and delta from baseline.

### Run Best Config

```
POST /api/experiments/{experiment_id}/run-best
```

Convenience endpoint: runs a tool eval using the experiment's best discovered configuration. Optionally override models via request body.

---

## Analytics

### Leaderboard

```
GET /api/analytics/leaderboard
```

**Query parameters:**

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `type` | `benchmark`, `tool_eval` | `benchmark` | Data source |
| `period` | `7d`, `30d`, `90d`, `all` | `all` | Time window |

**Benchmark response:** Models ranked by avg TPS, with avg TTFT, avg cost, total runs.

**Tool eval response:** Models ranked by avg overall %, with avg tool %, avg param %, total evals.

### Trends

```
GET /api/analytics/trends
```

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `models` | string | Comma-separated model names (required) |
| `metric` | string | `tps` or `ttft` |
| `period` | string | `7d`, `30d`, `90d`, `all` |

Returns time-series data points for each model.

### Compare

```
GET /api/analytics/compare
```

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `runs` | string | Comma-separated run IDs (2-4 required) |

Side-by-side comparison of specific benchmark runs.

---

## Schedules

```
GET /api/schedules                           # List schedules
POST /api/schedules                          # Create schedule
PUT /api/schedules/{schedule_id}             # Update schedule
DELETE /api/schedules/{schedule_id}          # Delete schedule
POST /api/schedules/{schedule_id}/trigger    # Trigger immediately
```

**Create schedule:**

```json
{
  "name": "Daily GPT-4o Check",
  "prompt": "Explain recursion",
  "models": ["gpt-4o", "anthropic/claude-sonnet-4-5"],
  "max_tokens": 512,
  "temperature": 0.7,
  "interval_hours": 24
}
```

---

## Export / Import

### Export Endpoints

```
GET /api/export/history            # Benchmark history as CSV
GET /api/export/leaderboard        # Leaderboard as CSV (?type=benchmark|tool_eval&period=all)
GET /api/export/tool-eval          # Tool eval runs as CSV
GET /api/export/eval/{eval_id}     # Single eval run as JSON (with raw request/response)
GET /api/export/run/{run_id}       # Single benchmark run as CSV
GET /api/export/settings           # Configuration backup as JSON
```

### Import Endpoints

```
POST /api/import/settings          # Restore configuration from JSON
```

The settings import merges providers into existing config (adds new, updates existing). Requires `export_version` field in the payload.

---

## Provider Discovery & Health

### Discover Models

```
GET /api/models/discover?provider_key=openai
```

Fetches available models from the provider's API. Supports OpenAI, Anthropic, Gemini, and OpenAI-compatible endpoints (LM Studio, Ollama, vLLM).

### Detect LM Studio Backend

```
GET /api/lm-studio/detect?provider_key=lm_studio
```

Detects whether LM Studio is running GGUF or MLX models via `/v1/models`. Returns `backend_type` (`gguf`, `mlx`, `mixed`, `unknown`) and lists unsupported parameters for MLX backends.

### Provider Health Check

```
GET /api/health/providers
```

Sends a minimal completion request to one model per provider to verify connectivity, API key validity, and measure latency.

**Response:**

```json
{
  "providers": [
    { "name": "OpenAI", "status": "ok", "latency_ms": 245 },
    { "name": "Anthropic", "status": "error", "latency_ms": 5012, "error": "Authentication failed" }
  ]
}
```

---

## Provider Parameters

### Full Registry

```
GET /api/provider-params/registry
```

Returns the complete 3-tier parameter registry for all 10 supported providers. See [Configuration Schema](config-schema.md) for the full registry structure.

### Validate Parameters

```
POST /api/provider-params/validate
```

Validates parameters against provider constraints before running a benchmark or eval.

```json
{
  "provider_key": "openai",
  "model_id": "gpt-4o",
  "params": {
    "temperature": 1.5,
    "top_p": 0.9,
    "top_k": 50
  }
}
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

### Seed Param Support Config

```
POST /api/param-support/seed
```

Generates default parameter support configuration from the built-in provider registry. Used by the Settings page to initialize per-model parameter support UI.

---

## Settings

### Phase 10 Settings

```
GET /api/settings/phase10          # Get feature settings
PUT /api/settings/phase10          # Update feature settings
```

Settings for judge, param tuner, prompt tuner, and per-model parameter support configuration.

**PUT body:**

```json
{
  "judge": { "default_model": "anthropic/claude-sonnet-4-5", "concurrency": 4 },
  "param_tuner": {
    "presets": [
      { "name": "My Preset", "search_space": { "temperature": [0.0, 0.5, 1.0] } }
    ]
  },
  "prompt_tuner": { "population_size": 5, "generations": 3 },
  "param_support": {
    "provider_defaults": { "openai": { "params": {...} } },
    "model_overrides": {}
  }
}
```

Maximum 20 custom presets allowed per user.

---

## Onboarding

```
GET /api/onboarding/status         # Check onboarding completion
POST /api/onboarding/complete      # Mark onboarding as complete
```

---

## Admin Endpoints

All admin endpoints require the `admin` role.

### User Management

```
GET /api/admin/users                               # List all users (with stats)
PUT /api/admin/users/{user_id}/role                # Change user role (admin/user)
DELETE /api/admin/users/{user_id}                  # Delete user and all data
```

Cannot change your own role or delete your own account. Audit log entries are preserved (unlinked, not deleted) when a user is deleted.

### Rate Limits

```
PUT /api/admin/users/{user_id}/rate-limit          # Set rate limits
GET /api/admin/users/{user_id}/rate-limit          # Get rate limits
```

**Set rate limits:**

```json
{
  "benchmarks_per_hour": 50,
  "max_concurrent": 3,
  "max_runs_per_benchmark": 20
}
```

### Statistics

```
GET /api/admin/stats
```

Returns benchmark counts by time window (24h, 7d, 30d), top users, keys by provider, total user count.

### System Health

```
GET /api/admin/system
```

Returns database size, results count/size, active/queued job counts, connected WebSocket clients, process uptime.

### Audit Log

```
GET /api/admin/audit
```

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `user` | string | Filter by username (email) |
| `action` | string | Filter by action type |
| `since` | string | ISO timestamp cutoff |
| `limit` | int | Max entries (default 100) |
| `offset` | int | Pagination offset |

### Application Logs

```
GET /api/admin/logs
```

Returns recent application log entries from the in-memory ring buffer (2000 entries max).

**Authentication:** Either admin JWT or `LOG_ACCESS_TOKEN` query parameter.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `lines` | int | Number of log lines (1-2000, default 100) |
| `level` | string | Filter by level (ERROR, WARNING, INFO, DEBUG) |
| `search` | string | Case-insensitive text search |
| `token` | string | Static LOG_ACCESS_TOKEN for non-JWT access |

---

## Error Responses

All error responses follow a consistent format:

```json
{ "error": "Description of what went wrong" }
```

Or for Pydantic validation errors (422):

```json
{ "detail": "Validation error description" }
```

Common HTTP status codes:

| Code | Meaning |
|------|---------|
| 400 | Bad request (invalid input) |
| 401 | Not authenticated |
| 403 | Forbidden (insufficient role) |
| 404 | Resource not found |
| 422 | Validation error |
| 429 | Rate limit exceeded |
| 502 | Upstream provider error |
| 504 | Upstream provider timeout |
