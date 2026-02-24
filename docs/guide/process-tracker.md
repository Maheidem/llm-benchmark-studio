# Process Tracker

## Overview

The Process Tracker provides persistent server-side tracking for all background jobs. Jobs run as asyncio tasks, persist their state to SQLite, and broadcast real-time status updates to connected clients via WebSocket. If you close your browser or navigate away, you can reconnect and see the current status of any running job.

The system is implemented primarily in two modules:

- `job_registry.py` -- The `JobRegistry` singleton that manages job lifecycle, concurrency, and persistence
- `job_handlers.py` -- Handler functions for each job type (one handler per type)

## Job Types

The registry supports seven job types, each with its own handler:

| Job Type | Handler | Description |
|----------|---------|-------------|
| `benchmark` | `benchmark_handler` | Speed benchmark runs across multiple LLM providers |
| `tool_eval` | `tool_eval_handler` | Tool-calling evaluation runs against test suites |
| `param_tune` | `param_tune_handler` | Parameter grid search for optimal configurations |
| `prompt_tune` | `prompt_tune_handler` | Prompt variation testing (quick or evolutionary) |
| `judge` | `judge_handler` | LLM-as-Judge quality evaluation of eval results |
| `judge_compare` | `judge_compare_handler` | Side-by-side comparative judge between two eval runs |
| `scheduled_benchmark` | (via scheduler) | Automated recurring benchmark runs |

Handlers are registered at startup in `app.py` by calling `register_all_handlers()` from `job_handlers.py`.

## Job Statuses

Every job moves through a defined set of statuses with validated transitions:

| Status | Description |
|--------|-------------|
| `pending` | Created, about to start (under concurrency limit) |
| `queued` | Waiting for a concurrency slot to open |
| `running` | Actively executing |
| `done` | Completed successfully |
| `failed` | Completed with an error (including timeout) |
| `cancelled` | Cancelled by user or admin |
| `interrupted` | Terminated due to server restart or orphaned state |

### State Transitions

```
pending -----> running -----> done
   |              |---------> failed
   |              |---------> cancelled
   |              |---------> interrupted
   |
   +-----------> queued -----> running (when a slot opens)
   |                |--------> cancelled
   |
   +-----------> cancelled
```

Invalid transitions are logged as warnings but do not block execution.

## Job Lifecycle

### Submit

When a job is submitted via `registry.submit()`:

1. A unique `job_id` (hex UUID) is generated
2. The registry checks the user's concurrency limit
3. If under the limit, the job starts immediately (status: `pending` then `running`)
4. If at the limit, the job is placed in a queue (status: `queued`)
5. The job is persisted to the `jobs` table in SQLite
6. A `job_created` WebSocket event is broadcast to the user

### Execution

While running:

1. The handler receives a `cancel_event` (asyncio.Event) and a `progress_cb` callback
2. The handler calls `progress_cb(pct, detail)` to report progress (0-100%)
3. Progress updates are persisted to the database and broadcast via WebSocket
4. The handler returns a `result_ref` string on success (e.g., a benchmark_run ID)

### Completion

When a job finishes:

- **Success**: Status set to `done`, `result_ref` stored, `job_completed` event broadcast
- **Error**: Status set to `failed`, `error_msg` stored (truncated to 500 chars), `job_failed` event broadcast
- **Cancelled**: Status set to `cancelled`, `job_cancelled` event broadcast
- **Interrupted**: Status set to `interrupted` (server shutdown or asyncio task cancelled)

After any terminal state, the user's concurrency slot is released and the queue is checked for the next eligible job.

### Cancel

Jobs can be cancelled through multiple paths:

- **REST API**: `POST /api/jobs/{job_id}/cancel`
- **WebSocket**: Send `{"type": "cancel", "job_id": "..."}` over the WebSocket connection
- **Admin**: `POST /api/admin/jobs/{job_id}/cancel`

For pending/queued jobs, cancellation is immediate. For running jobs, the cancel event is signaled and the handler checks it at safe points.

## Concurrency Control

Each user has a configurable maximum number of concurrent jobs (default: 1). This limit is stored in the `rate_limits` table and can be adjusted by admins.

- When a user submits a job and is at their limit, the job is queued
- When a running job finishes, the registry automatically starts the next queued job for that user
- A `_slot_lock` (asyncio.Lock) prevents race conditions in slot accounting

## Queue Processing

The queue is processed in FIFO order per user:

1. When a job completes (any terminal state), `_process_queue(user_id)` is called
2. The registry checks if the user has available slots
3. If so, the oldest queued job is retrieved from the database and started
4. This repeats until either the limit is reached or no queued jobs remain

## Timeout and Watchdog

- Each job has a configurable timeout (default: 7200 seconds / 2 hours)
- A watchdog task runs every 60 seconds, checking for jobs that have exceeded their timeout
- Timed-out jobs are marked as `failed` with the error message "Timeout exceeded"
- The corresponding asyncio task is cancelled

## Server Restart Recovery

On application startup, `_startup_recovery()` runs:

- All jobs with status `running`, `pending`, or `queued` are marked as `interrupted`
- This prevents ghost jobs from blocking concurrency slots after a restart
- The count of affected jobs is logged as a warning

## WebSocket Integration

The JobRegistry broadcasts events to users via the `ConnectionManager` (set during startup with `set_ws_manager()`). All events include the `job_id` field.

### Core Job Events

| Event Type | When | Key Fields |
|------------|------|------------|
| `job_created` | Job submitted | `job_type`, `status`, `progress_detail`, `created_at` |
| `job_started` | Job begins executing | `job_type` |
| `job_progress` | Handler reports progress | `progress_pct`, `progress_detail` |
| `job_completed` | Job finished successfully | `result_ref` |
| `job_failed` | Job errored or timed out | `error` |
| `job_cancelled` | Job was cancelled | -- |

### Job-Type-Specific Events

Handlers send additional events for granular progress:

**Benchmarks**: `benchmark_init`, `benchmark_progress`, `benchmark_result`

**Tool Eval**: `tool_eval_init`, `tool_eval_progress`, `tool_eval_result`, `tool_eval_summary`, `tool_eval_complete`

**Param Tune**: `tune_start`, `combo_result`, `tune_complete`

**Prompt Tune**: `tune_start`, `generation_start`, `prompt_generated`, `prompt_eval_start`, `prompt_eval_result`, `generation_complete`, `tune_complete`

**Judge**: `judge_start`, `judge_verdict`, `judge_report`, `judge_complete`

**Judge Compare**: `compare_start`, `compare_case`, `compare_complete`

### Reconnection

When a WebSocket client reconnects (e.g., after a page refresh):

1. The server sends a `sync` message with active and recent jobs
2. For each running job, a reconstructed init event is sent so the client can resume progress tracking
3. Reconnect init events include `reconnect: true` and the current `progress_pct`

## Database Schema

The `jobs` table stores all job state:

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress_pct INTEGER DEFAULT 0,
    progress_detail TEXT DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    result_ref TEXT,
    result_type TEXT,
    error_msg TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    timeout_at TEXT,
    timeout_seconds INTEGER NOT NULL DEFAULT 7200
);
```

Indexes exist on `(user_id, status)`, `(user_id, created_at DESC)`, `(status)`, and `(status, timeout_at)` for efficient queries.

## REST API Endpoints

### User Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs` | List current user's jobs. Query params: `?status=running,queued&limit=20` |
| `GET` | `/api/jobs/{job_id}` | Get a single job's details |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a specific job |

### Admin Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/admin/jobs` | List all active jobs across all users (includes user email) |
| `POST` | `/api/admin/jobs/{job_id}/cancel` | Cancel any user's job |

### Example: List Active Jobs

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/jobs?status=running,queued"
```

Response:

```json
{
  "jobs": [
    {
      "id": "a1b2c3d4...",
      "job_type": "benchmark",
      "status": "running",
      "progress_pct": 45,
      "progress_detail": "GPT-4o, Run 2/3",
      "created_at": "2026-02-22T10:30:00",
      "started_at": "2026-02-22T10:30:01",
      "timeout_at": "2026-02-22T12:30:01"
    }
  ]
}
```

### Example: Cancel a Job

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/jobs/a1b2c3d4.../cancel"
```

Response:

```json
{"status": "ok", "message": "Cancellation requested"}
```

## Notification Widget

The frontend includes a notification widget in the top navigation bar:

- **Badge**: Displays the count of active (pending/queued/running) jobs
- **Dropdown**: Lists active jobs with progress bars and status details
- **History**: Shows the most recent completed jobs (approximately the last 10)
- **Cancel**: Each running job can be cancelled directly from the dropdown
- **Multi-tab sync**: All browser tabs receive the same WebSocket events and stay synchronized

## Orphan Cleanup

The system includes safeguards for orphaned state:

- **Ghost jobs**: If a running job has no in-memory cancel event (e.g., after a partial restart), it is detected and marked as `interrupted`
- **Orphaned tune runs**: When a job is terminal but its linked param_tune or prompt_tune run still shows `running`, the cancel endpoint automatically cleans up the linked run
- **Startup recovery**: On server start, all non-terminal jobs are marked `interrupted`
