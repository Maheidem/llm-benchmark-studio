# WebSocket Protocol

## Overview

WebSocket connections provide real-time status updates for all background jobs (benchmarks, tool evals, param tuning, prompt tuning, judge operations). The WebSocket receives push notifications for any job started by the authenticated user, regardless of which browser tab initiated it.

## Connection

```
ws://localhost:8501/ws?token=<JWT_ACCESS_TOKEN>
```

Or with TLS:

```
wss://your-domain.com/ws?token=<JWT_ACCESS_TOKEN>
```

### Authentication

Authentication is performed via the `token` query parameter. Both access tokens and CLI tokens are accepted.

```javascript
const token = localStorage.getItem("access_token");
const ws = new WebSocket(`wss://example.com/ws?token=${token}`);
```

**Close codes for auth failures:**

| Code | Reason |
|------|--------|
| 4001 | Missing token, invalid token, expired token, or user not found |
| 4008 | Too many connections (max per user exceeded) |

### Multi-Tab Support

Each user can have up to **5 simultaneous WebSocket connections**. All tabs receive identical messages. This enables:

- Starting a benchmark in one tab and monitoring it in another
- Closing and reopening the browser without losing process visibility
- Multiple dashboard views receiving updates simultaneously

If a 6th connection is attempted, it is rejected with close code `4008`.

### Connection Lifecycle

```
Client                          Server
  |                                |
  |--- WebSocket connect --------->|  (with ?token=JWT)
  |                                |  Validate JWT
  |                                |  Check connection limit
  |<-- accept / close(4001/4008) --|
  |                                |
  |<-- sync (active + recent) -----|  Initial state sync
  |<-- *_init (per running job) ---|  Reconnect init events
  |                                |
  |--- ping ---------------------->|  Client keepalive
  |<-- pong -----------------------|
  |                                |
  |<-- job_created/started/... ----|  Job lifecycle events
  |<-- job_progress ---------------|  Progress updates
  |<-- job_completed/failed -------|  Terminal events
  |                                |
  |--- cancel -------------------->|  Cancel a job
  |<-- job_cancelled --------------|
  |                                |
  |--- (silence for 90s) -------->|
  |<-- close(4002, timeout) -------|  Server-side timeout
```

## Keepalive

The server enforces a **90-second receive timeout**. If no message is received from the client within 90 seconds, the connection is closed with code `4002` ("Receive timeout"). This catches dead connections from unclean proxy disconnects (e.g., Cloudflare closing without sending a close frame).

Clients should send a `ping` message at least every **60 seconds**:

```json
{"type": "ping"}
```

Server responds with:

```json
{"type": "pong"}
```

## Auto-Reconnect

When a connection drops, clients should implement exponential backoff reconnection:

```javascript
let reconnectDelay = 1000; // Start at 1 second
const maxDelay = 30000;    // Cap at 30 seconds

function reconnect() {
  setTimeout(() => {
    const ws = new WebSocket(`wss://example.com/ws?token=${getToken()}`);
    ws.onopen = () => { reconnectDelay = 1000; };
    ws.onclose = () => {
      reconnectDelay = Math.min(reconnectDelay * 2, maxDelay);
      reconnect();
    };
  }, reconnectDelay);
}
```

On reconnect, the server automatically:

1. Sends a `sync` message with current active and recent jobs
2. Re-sends `*_init` events for any currently running jobs (so progress tracking can resume)

## Message Format

All messages are JSON objects with a `type` field.

## Server-to-Client Messages

### sync

Sent immediately after connection. Contains the user's active and recent jobs.

```json
{
  "type": "sync",
  "active_jobs": [
    {
      "id": "abc123",
      "job_type": "benchmark",
      "status": "running",
      "progress_pct": 45,
      "progress_detail": "Benchmark: 3 models, 2 runs each",
      "created_at": "2026-02-20T10:00:00+00:00",
      "started_at": "2026-02-20T10:00:01+00:00"
    }
  ],
  "recent_jobs": [
    {
      "id": "def456",
      "job_type": "tool_eval",
      "status": "done",
      "result_ref": "eval-run-id",
      "completed_at": "2026-02-20T09:55:00+00:00"
    }
  ]
}
```

### job_created

A new job has been registered.

```json
{
  "type": "job_created",
  "job_id": "abc123",
  "job_type": "benchmark",
  "status": "pending",
  "progress_detail": "Benchmark: 3 models, 2 runs each",
  "created_at": "2026-02-20T10:00:00+00:00"
}
```

If the user is at their concurrency limit, `status` will be `"queued"` instead of `"pending"`.

### job_started

Job execution has begun (transitioned from pending/queued to running).

```json
{
  "type": "job_started",
  "job_id": "abc123",
  "job_type": "benchmark"
}
```

### job_progress

Periodic progress update for a running job.

```json
{
  "type": "job_progress",
  "job_id": "abc123",
  "progress_pct": 45,
  "progress_detail": "GPT-4o: Run 2/3, Context 5K"
}
```

### job_completed

Job finished successfully.

```json
{
  "type": "job_completed",
  "job_id": "abc123",
  "result_ref": "benchmark-run-id"
}
```

The `result_ref` is the ID of the created resource (benchmark run, eval run, tune run, or judge report).

### job_failed

Job encountered an error.

```json
{
  "type": "job_failed",
  "job_id": "abc123",
  "error": "API key invalid for provider openai"
}
```

Error messages are truncated to 500 characters. API keys are sanitized from error messages.

### job_cancelled

Job was cancelled by the user or admin.

```json
{
  "type": "job_cancelled",
  "job_id": "abc123"
}
```

### Benchmark-Specific Events

These events are sent in addition to the generic job lifecycle events during benchmark execution.

**benchmark_init** -- Sent when a benchmark job starts (and on reconnect for running benchmarks):

```json
{
  "type": "benchmark_init",
  "job_id": "abc123",
  "reconnect": false,
  "data": {
    "targets": [
      { "provider_key": "openai", "model_id": "gpt-4o" },
      { "provider_key": "anthropic", "model_id": "anthropic/claude-sonnet-4-5" }
    ],
    "runs": 3,
    "context_tiers": [0, 5000],
    "max_tokens": 512
  }
}
```

On reconnect, `reconnect` is `true` and `progress_pct` is included.

**benchmark_result** -- Individual run result:

```json
{
  "type": "benchmark_result",
  "job_id": "abc123",
  "data": {
    "model": "gpt-4o",
    "provider": "OpenAI",
    "tokens_per_second": 142.5,
    "ttft_ms": 234,
    "cost": 0.00125,
    "context_tokens": 5000,
    "output_tokens": 512,
    "success": true
  }
}
```

**benchmark_skipped** -- Context tier too large for model:

```json
{
  "type": "benchmark_skipped",
  "job_id": "abc123",
  "data": {
    "model": "gpt-4o-mini",
    "context_tier": 200000,
    "reason": "Context tier exceeds model window"
  }
}
```

### Tool Eval Events

**tool_eval_init** -- Sent when a tool eval starts:

```json
{
  "type": "tool_eval_init",
  "job_id": "abc123",
  "data": {
    "targets": [
      { "provider_key": "openai", "model_id": "gpt-4o" }
    ],
    "total_cases": 12,
    "suite_name": "Weather API Suite"
  }
}
```

**tool_eval_result** -- Per-model per-case result:

```json
{
  "type": "tool_eval_result",
  "job_id": "abc123",
  "data": {
    "model_id": "gpt-4o",
    "test_case_id": "case-1",
    "actual_tool": "get_weather",
    "actual_params": { "city": "Paris" },
    "tool_selection_score": 1.0,
    "param_accuracy": 1.0,
    "overall_score": 1.0,
    "latency_ms": 345
  }
}
```

### Param Tune Events

**tune_start** -- Sent when param/prompt tuning starts:

```json
{
  "type": "tune_start",
  "job_id": "abc123",
  "tune_id": "tune-run-id",
  "total_combos": 12,
  "models": ["gpt-4o"],
  "suite_name": "Weather API Suite"
}
```

**tune_combo_result** -- Result for one parameter combination:

```json
{
  "type": "tune_combo_result",
  "job_id": "abc123",
  "data": {
    "combo_index": 3,
    "model_id": "gpt-4o",
    "params": { "temperature": 0.3, "top_p": 0.9 },
    "score": 0.85,
    "case_results": [
      {
        "test_case_id": "case-1",
        "tool_selection_score": 1.0,
        "param_accuracy": 0.8,
        "overall_score": 0.9
      }
    ]
  }
}
```

### Prompt Tune Events

**tune_start** -- Same format as param tune.

**prompt_eval_result** -- Result for one prompt variation:

```json
{
  "type": "prompt_eval_result",
  "job_id": "abc123",
  "data": {
    "prompt_index": 2,
    "style": "structured",
    "score": 0.92,
    "prompt_preview": "You are a tool-calling assistant. Follow these rules..."
  }
}
```

### Judge Events

**judge_verdict** -- Verdict for a single test case:

```json
{
  "type": "judge_verdict",
  "job_id": "abc123",
  "data": {
    "test_case_id": "case-1",
    "quality_score": 4,
    "verdict": "pass",
    "summary": "Correct tool with accurate parameters"
  }
}
```

**judge_report** -- Cross-case analysis complete:

```json
{
  "type": "judge_report",
  "job_id": "abc123",
  "data": {
    "overall_grade": "B+",
    "overall_score": 82,
    "strengths": ["Consistent tool selection"],
    "weaknesses": ["Occasional parameter omission"]
  }
}
```

**compare_case** -- Per-case comparison result (judge compare):

```json
{
  "type": "compare_case",
  "job_id": "abc123",
  "data": {
    "case_num": 3,
    "winner": "model_a",
    "confidence": 0.85,
    "reasoning": "Model A provided more complete parameters"
  }
}
```

## Client-to-Server Messages

### ping

Keepalive message. Send at least every 60 seconds.

```json
{"type": "ping"}
```

### cancel

Request cancellation of a running job.

```json
{
  "type": "cancel",
  "job_id": "abc123"
}
```

The server delegates to the JobRegistry, which signals the cancel event to the running task. A `job_cancelled` message is sent when cancellation completes.

## Connection Close Codes

| Code | Reason | Action |
|------|--------|--------|
| 1000 | Normal closure | No action needed |
| 4001 | Missing/invalid/expired token | Re-authenticate and reconnect |
| 4002 | Receive timeout (90s silence) | Reconnect with fresh token |
| 4008 | Too many connections (max 5) | Close another tab or wait |

## Implementation Notes

- The WebSocket endpoint is at `/ws` (not under `/api/`)
- Messages are JSON encoded via `ws.send_json()` / `ws.receive_json()`
- Dead connections are automatically cleaned up when `send_json` fails
- The ConnectionManager tracks connections per user with an async lock for thread safety
- Admin users receive `broadcast_to_admins` messages for system-wide events
- All job status transitions are validated against a state machine (see JobRegistry)
