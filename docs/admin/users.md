# User Management

Admin users can manage all user accounts, roles, rate limits, and view system-wide statistics.

## User Roles

| Role | Description |
|------|-------------|
| `admin` | Full access including user management, audit logs, system settings, and global API key management |
| `user` | Standard access to benchmarks, tool eval, history, and personal configuration |

## Admin Promotion

The admin role is assigned in three ways:

1. **First user**: The first account registered is automatically promoted to admin
2. **ADMIN_EMAIL env var**: Set `ADMIN_EMAIL` in `.env` to auto-promote that email on startup
3. **Manual promotion**: An existing admin changes another user's role via the API

## User List

View all registered users with their metadata:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/admin/users
```

Returns:

```json
{
  "users": [
    {
      "id": "abc123",
      "email": "admin@example.com",
      "role": "admin",
      "created_at": "2026-01-15 10:00:00",
      "last_login": "2026-02-17 14:30:00",
      "benchmark_count": 42,
      "key_count": 3
    }
  ]
}
```

## Change User Role

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "admin"}' \
  http://localhost:8501/api/admin/users/{user_id}/role
```

!!! warning
    You cannot change your own role. This prevents accidentally locking yourself out.

## Delete User

Deleting a user removes all their data (config, keys, benchmark runs, tool eval runs, schedules).

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/admin/users/{user_id}
```

!!! warning
    You cannot delete your own account. Audit log entries for the deleted user are preserved but the `user_id` field is set to NULL.

## Rate Limits

### Set Per-User Rate Limits

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "benchmarks_per_hour": 20,
    "max_concurrent": 1,
    "max_runs_per_benchmark": 10
  }' \
  http://localhost:8501/api/admin/users/{user_id}/rate-limit
```

### Get Per-User Rate Limits

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/admin/users/{user_id}/rate-limit
```

Default limits (when none are set):

| Limit | Default |
|-------|---------|
| Benchmarks per hour | 20 |
| Max concurrent | 1 |
| Max runs per benchmark | 10 |

## Usage Statistics

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/admin/stats
```

Returns:

```json
{
  "benchmarks_24h": 15,
  "benchmarks_7d": 89,
  "benchmarks_30d": 342,
  "total_users": 5,
  "top_users": [
    { "username": "user@example.com", "cnt": 120 }
  ],
  "keys_by_provider": [
    { "provider": "openai", "user_count": 4 }
  ]
}
```

## Jobs Management

The Admin Dashboard includes a Jobs tab showing all active and queued jobs across all users. Admins can view and cancel any running job.

### List Active Jobs

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/admin/jobs
```

### Cancel a Job

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/admin/jobs/{job_id}/cancel
```

The admin page auto-refreshes the jobs list every 15 seconds and shows:

- Active Jobs tab with running and queued jobs
- Users tab with user management
- Audit Log tab with filterable event history

## System Health

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/admin/system
```

Returns:

```json
{
  "db_size_mb": 12.5,
  "results_size_mb": 3.2,
  "results_count": 89,
  "benchmark_active": false,
  "active_jobs": [],
  "total_active": 0,
  "total_queued": 0,
  "connected_ws_clients": 2,
  "process_uptime_s": 86400
}
```

The system health panel in the Admin Dashboard displays DB size, results file count, uptime, active/queued job counts, and connected WebSocket clients.

## Application Logs

Admins can access application logs via the API. Authentication uses either an admin JWT or a static `LOG_ACCESS_TOKEN` query parameter.

```bash
# Using admin JWT
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/admin/logs?lines=50"

# Using static token
curl "http://localhost:8501/api/admin/logs?token=YOUR_LOG_TOKEN&lines=50"

# Filter by level
curl "http://localhost:8501/api/admin/logs?token=YOUR_LOG_TOKEN&level=ERROR&lines=20"

# Search by keyword
curl "http://localhost:8501/api/admin/logs?token=YOUR_LOG_TOKEN&search=benchmark&lines=50"
```

Logs are stored in an in-memory ring buffer (2000 entries max) and reset on container restart.

## Audit Log

The audit log records all significant actions (logins, benchmarks, admin changes):

```bash
# Full log (paginated)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/admin/audit?limit=50&offset=0"

# Filter by user
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/admin/audit?user=admin@example.com"

# Filter by action
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/admin/audit?action=benchmark_start"

# Filter by time
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/admin/audit?since=2026-02-01"
```

### Audit Actions

| Action | Description |
|--------|-------------|
| `user_register` | New account created |
| `user_login` | Successful login |
| `benchmark_start` | Benchmark run started |
| `benchmark_complete` | Benchmark run completed |
| `benchmark_cancel` | Benchmark run cancelled |
| `admin_user_update` | Admin changed a user's role |
| `admin_user_delete` | Admin deleted a user |
| `admin_rate_limit` | Admin changed rate limits |

Audit log entries older than 90 days are automatically cleaned up on server startup.
