# WebSocket Protocol

!!! note "Upcoming Feature"
    WebSocket support is currently in development (Phase 10A). This page documents the planned protocol.

## Overview

WebSocket connections will provide real-time status updates for all running processes. This replaces the current approach where status is only available through the SSE connection that initiated the process.

## Connection

```
ws://localhost:8501/ws
```

Authentication is performed via a query parameter or initial message containing the JWT token.

## Planned Message Types

### Server-to-Client

| Type | Description |
|------|-------------|
| `process_started` | A new process has begun |
| `process_progress` | Progress update for a running process |
| `process_completed` | Process finished successfully |
| `process_failed` | Process encountered an error |
| `process_cancelled` | Process was cancelled |
| `process_list` | Full list of active processes (on connect) |

### Client-to-Server

| Type | Description |
|------|-------------|
| `cancel_process` | Request cancellation of a process |
| `subscribe` | Subscribe to updates for a specific process |

## Message Format

All messages use JSON:

```json
{
  "type": "process_progress",
  "process_id": "abc123",
  "process_type": "benchmark",
  "progress": {
    "current": 5,
    "total": 12,
    "model": "GPT-4o",
    "detail": "Run 2/3, Context 5K"
  },
  "timestamp": "2026-02-17T14:30:00Z"
}
```

## Multi-Tab Synchronization

All browser tabs connected via WebSocket receive the same status updates. This enables:

- Starting a benchmark in one tab and monitoring it in another
- Closing and reopening the browser without losing process visibility
- Multiple users seeing their own processes simultaneously

## Current State: SSE

Until WebSocket support is available, real-time results are delivered via Server-Sent Events (SSE) on the endpoint that initiated the process (e.g., `POST /api/benchmark`). The SSE connection must remain open for the duration of the process.
