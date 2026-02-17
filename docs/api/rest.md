# REST API Reference

All API endpoints require authentication via JWT bearer token unless noted otherwise. Tokens are obtained from the authentication endpoints.

Base URL: `http://localhost:8501`

## Authentication

### Register

Create a new user account. The first registered user is automatically promoted to admin.

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

A refresh token is set as an HttpOnly cookie.

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

### Generate CLI Token

```
POST /api/auth/cli-token
```

Generates a long-lived JWT (30 days) for CLI usage.

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

Returns the user's providers, models, and defaults.

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

```json
{
  "model_id": "gpt-4o",
  "provider_key": "openai",
  "display_name": "GPT-4o Updated",
  "context_window": 128000,
  "skip_params": ["temperature"]
}
```

### Delete Model

```
DELETE /api/config/model
```

```json
{ "provider_key": "openai", "model_id": "gpt-4o-mini" }
```

### Discover Models

```
GET /api/models/discover?provider_key=openai
```

Fetches available models from the provider's API. Supports OpenAI, Anthropic, Gemini, and OpenAI-compatible endpoints.

### Prompt Templates

```
GET /api/config/prompts           # List templates
POST /api/config/prompts          # Add template
```

---

## API Keys

### Per-User Keys

```
GET /api/keys                     # List user's key status per provider
PUT /api/keys                     # Set/update a key
DELETE /api/keys                  # Remove a key
```

**Set a key:**

```json
{ "provider_key": "openai", "value": "sk-..." }
```

### Global Environment Keys (Admin Only)

```
GET /api/env                      # List env keys (masked)
PUT /api/env                      # Set/update env key
DELETE /api/env                   # Remove env key
```

---

## Benchmarks

### Run Benchmark

```
POST /api/benchmark
```

Returns a Server-Sent Events (SSE) stream.

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

**SSE event types:**

| Event Type | Description |
|------------|-------------|
| `progress` | Current run/total, model, context tier |
| `result` | Individual run metrics |
| `skipped` | Context tier skipped (too large) |
| `heartbeat` | Keep-alive (every 15s) |
| `cancelled` | Benchmark was cancelled |
| `complete` | All runs finished |
| `error` | Error occurred |

### Cancel Benchmark

```
POST /api/benchmark/cancel
```

### Rate Limit Status

```
GET /api/user/rate-limit
```

```json
{ "limit": 2000, "remaining": 1995, "window": "1 hour" }
```

---

## History

```
GET /api/history                  # List benchmark runs
GET /api/history/{run_id}         # Get specific run
DELETE /api/history/{run_id}      # Delete a run
```

---

## Tool Suites

```
GET /api/tool-suites                              # List suites
POST /api/tool-suites                             # Create suite
GET /api/tool-suites/{suite_id}                   # Get suite with cases
PUT /api/tool-suites/{suite_id}                   # Update suite
DELETE /api/tool-suites/{suite_id}                # Delete suite
GET /api/tool-suites/{suite_id}/cases             # List cases
POST /api/tool-suites/{suite_id}/cases            # Add case(s)
PUT /api/tool-suites/{suite_id}/cases/{case_id}   # Update case
DELETE /api/tool-suites/{suite_id}/cases/{case_id} # Delete case
```

### Import Suite from JSON

```
POST /api/tool-eval/import
```

See [Tool Calling Evaluation](../guide/tool-eval.md) for the JSON format.

### Import from MCP Server

```
POST /api/mcp/discover            # Discover tools from MCP server
POST /api/mcp/import              # Import MCP tools as a suite
```

---

## Tool Eval

### Run Eval

```
POST /api/tool-eval
```

Returns SSE stream.

```json
{
  "suite_id": "suite-id",
  "models": ["gpt-4o"],
  "temperature": 0.0,
  "tool_choice": "required",
  "judge": {
    "enabled": true,
    "mode": "live_inline",
    "judge_model": "anthropic/claude-sonnet-4-5",
    "custom_instructions": "Focus on parameter completeness"
  }
}
```

### Cancel Eval

```
POST /api/tool-eval/cancel
```

### Eval History

```
GET /api/tool-eval/history                 # List runs
GET /api/tool-eval/history/{eval_id}       # Get run details
DELETE /api/tool-eval/history/{eval_id}    # Delete run
```

---

## Param Tuner

```
POST /api/tool-eval/param-tune                     # Run tune (SSE)
POST /api/tool-eval/param-tune/cancel              # Cancel
GET /api/tool-eval/param-tune/history              # List runs
GET /api/tool-eval/param-tune/history/{tune_id}    # Get details
DELETE /api/tool-eval/param-tune/history/{tune_id} # Delete
```

---

## Prompt Tuner

```
POST /api/tool-eval/prompt-tune                     # Run tune (SSE)
POST /api/tool-eval/prompt-tune/cancel              # Cancel
GET /api/tool-eval/prompt-tune/estimate             # Cost estimate
GET /api/tool-eval/prompt-tune/history              # List runs
GET /api/tool-eval/prompt-tune/history/{tune_id}    # Get details
DELETE /api/tool-eval/prompt-tune/history/{tune_id} # Delete
```

---

## Judge

```
POST /api/tool-eval/judge                          # Run judge (SSE)
POST /api/tool-eval/judge/compare                  # Judge compare (SSE)
POST /api/tool-eval/judge/cancel                   # Cancel
GET /api/tool-eval/judge/reports                   # List reports
GET /api/tool-eval/judge/reports/{report_id}       # Get report
DELETE /api/tool-eval/judge/reports/{report_id}    # Delete report
```

---

## Analytics

```
GET /api/analytics/leaderboard     # Model rankings
GET /api/analytics/trends          # Performance over time
GET /api/analytics/compare         # Side-by-side comparison
```

---

## Schedules

```
GET /api/schedules                           # List schedules
POST /api/schedules                          # Create schedule
PUT /api/schedules/{schedule_id}             # Update schedule
DELETE /api/schedules/{schedule_id}          # Delete schedule
POST /api/schedules/{schedule_id}/trigger    # Trigger immediately
```

---

## Export / Import

```
GET /api/export/history            # Benchmark history as CSV
GET /api/export/leaderboard        # Leaderboard as CSV
GET /api/export/tool-eval          # Tool eval runs as CSV
GET /api/export/eval/{eval_id}     # Single eval run as JSON
GET /api/export/run/{run_id}       # Single benchmark run as JSON
GET /api/export/settings           # Configuration backup as JSON
POST /api/import/settings          # Restore configuration from JSON
```

---

## Provider Parameters

```
GET /api/provider-params/registry           # Full parameter registry
POST /api/provider-params/validate          # Validate params for a provider
```

---

## Provider Health

```
GET /api/health/providers          # Check connectivity to all providers
```

---

## Settings (Phase 10)

```
GET /api/settings/phase10          # Get Phase 10 settings
PUT /api/settings/phase10          # Update Phase 10 settings
```

---

## Onboarding

```
GET /api/onboarding/status         # Check onboarding completion
POST /api/onboarding/complete      # Mark onboarding as complete
```

---

## Admin Endpoints

All admin endpoints require the `admin` role.

```
GET /api/admin/users                               # List all users
PUT /api/admin/users/{user_id}/role                # Change user role
DELETE /api/admin/users/{user_id}                  # Delete user
GET /api/admin/stats                               # Usage statistics
GET /api/admin/system                              # System health
GET /api/admin/audit                               # Audit log
PUT /api/admin/users/{user_id}/rate-limit          # Set rate limits
GET /api/admin/users/{user_id}/rate-limit          # Get rate limits
```

See [User Management](../admin/users.md) for details.
