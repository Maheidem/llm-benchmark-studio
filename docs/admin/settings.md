# System Settings

## Global Environment Keys

Admins can manage global API keys that serve as fallbacks for all users. These are stored in the `.env` file on the server.

```bash
# List all environment keys (values are masked)
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/env

# Set or update a key
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "OPENAI_API_KEY", "value": "sk-..."}' \
  http://localhost:8501/api/env

# Remove a key
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "OPENAI_API_KEY"}' \
  http://localhost:8501/api/env
```

!!! note "Key Priority"
    Per-user encrypted keys (set via `/api/keys`) take priority over global environment keys. Global keys serve as fallbacks for users who have not set their own.

## API Key Encryption

User API keys are encrypted using Fernet symmetric encryption before storage:

- The master key is resolved from (in order):
    1. `FERNET_KEY` environment variable
    2. `data/.fernet_key` file (auto-generated on first run)
- Auto-generated keys are written with `0600` permissions (owner-only)

!!! danger "Back Up Your Fernet Key"
    If the Fernet key is lost, all stored user API keys become unrecoverable. In production, set `FERNET_KEY` as an environment variable and back it up securely.

## Security Headers

The application adds security headers to all responses:

| Header | Value |
|--------|-------|
| Content-Security-Policy | Restricts script/style/font/image sources |
| X-Content-Type-Options | `nosniff` |
| X-Frame-Options | `DENY` |
| Referrer-Policy | `strict-origin-when-cross-origin` |
| Permissions-Policy | Disables camera, microphone, geolocation, payment |

## CORS Configuration

CORS is disabled by default. To enable it, set `CORS_ORIGINS`:

```bash
CORS_ORIGINS=https://example.com,https://staging.example.com
```

When set, the following CORS configuration is applied:

- Allowed origins: from the `CORS_ORIGINS` list
- Credentials: allowed
- Methods: GET, POST, PUT, DELETE
- Headers: Authorization, Content-Type

## Login Rate Limiting

The login endpoint is protected by IP-based rate limiting:

| Setting | Value |
|---------|-------|
| Max attempts per window | 5 |
| Window duration | 5 minutes (300 seconds) |
| Lockout duration | 15 minutes (900 seconds) |

After 5 failed login attempts from the same IP, that IP is locked out for 15 minutes.

## JWT Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `JWT_SECRET` | Auto-generated | Secret key for signing tokens |
| Access token expiry | 24 hours (1440 minutes) | Access tokens returned in JSON response body |
| Refresh token expiry | 7 days | Refresh tokens stored in HttpOnly cookies |
| CLI token expiry | 30 days | Long-lived tokens for CLI usage |
| `COOKIE_SECURE` | `false` | Set to `true` for HTTPS deployments |

The frontend proactively refreshes access tokens before they expire. Refresh tokens are stored as SHA-256 hashes in the database and can be revoked via the logout endpoint.

!!! warning "Production JWT Secret"
    Always set a strong `JWT_SECRET` in production. The auto-generated secret changes on each restart, invalidating all existing tokens.

## Feature Settings (Phase 10)

Per-user settings for advanced features are managed via the Settings page in the web UI. The Settings page has four tabs:

- **API Keys**: Manage per-user API keys for each provider (encrypted with Fernet)
- **Providers**: Configure provider endpoints, models, and model discovery
- **Judge**: Configure the LLM Judge model, mode, temperature, and custom instructions
- **Tuning**: Configure param tuner defaults (search space, presets) and prompt tuner settings

### Settings API

```bash
# Get Phase 10 settings
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/settings/phase10

# Save Phase 10 settings
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "judge": {"enabled": true, "model_id": "gpt-4o", "mode": "post_eval"},
    "param_tuner": {"max_combinations": 50, "temp_min": 0.0, "temp_max": 1.0},
    "prompt_tuner": {"mode": "quick", "generations": 3}
  }' \
  http://localhost:8501/api/settings/phase10
```

### Search Space Presets

The param tuner supports saved search space presets (up to 20 per user). Presets are stored within the Phase 10 settings under `param_tuner.presets`. Built-in vendor presets (e.g., Qwen3-Coder, GLM-4.7) are also available and cannot be edited.

### Per-Model Param Support

The `param_support` section stores per-provider default parameters and per-model overrides, allowing fine-grained control over which parameters are sent to each model.

## Settings Backup and Restore

Export your complete configuration:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/export/settings > backup.json
```

Restore from backup:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @backup.json \
  http://localhost:8501/api/import/settings
```

This exports and restores providers, models, prompt templates, and defaults. It does not include API keys or user data.

## Provider Health Check

Check connectivity to all configured providers:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/health/providers
```

This tests API connectivity for each provider that has a key configured.
