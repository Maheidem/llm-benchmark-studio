# Process Tracker

!!! note "Upcoming Feature"
    The Process Tracker is currently in development (Phase 10A). This page documents the planned functionality.

## Overview

The Process Tracker provides persistent server-side tracking for all running processes. It replaces the current SSE-based approach where process state is lost when you navigate away or close the browser.

## Problem Statement

Currently, all process state lives in-memory tied to SSE connections. If you close your browser tab during a benchmark run, you lose all visibility into the running process. There is no way to reconnect or see the status.

## Planned Features

### Persistent Process Tracking

All process types are tracked server-side in the database:

1. **Benchmarks** -- Speed benchmark runs
2. **Tool Evals** -- Tool calling evaluation runs
3. **Judge** -- LLM-as-Judge evaluations
4. **Judge Compare** -- Side-by-side Judge comparisons
5. **Param Tuner** -- Parameter grid search
6. **Prompt Tuner** -- Prompt variation testing
7. **Scheduled Benchmarks** -- Automated recurring runs

### Process Lifecycle

```
pending --> running --> completing --> done/failed/cancelled
                                      + interrupted (on server restart)
```

### Notification Widget

A Gmail-style notification widget in the top navigation bar:

- **Badge**: Shows count of active processes
- **Dropdown**: Lists active processes with progress details
- **History**: Shows recent completed processes (~last 10)
- **Reconnect**: Status view with button to reconnect to live streaming
- **Multi-tab sync**: All browser tabs stay synchronized

### Concurrency

- Multiple concurrent operations per user (configurable limit)
- Queue system when at maximum concurrent processes
- No more "already running" errors -- processes queue instead
- Admin-configurable per-user concurrency limits

### WebSocket Transport

- Real-time status updates via WebSocket (replaces SSE for status)
- All tabs connect via WebSocket for instant updates
- Automatic reconnection on connection loss

### Admin Features

- View all users' running processes
- Cancel any user's process
- Set per-user concurrency limits
- Monitor system-wide process load

### Server Restart Handling

- On restart, any `running` or `pending` processes are marked as `interrupted`
- Configurable timeout (default: 2 hours) auto-marks long-running processes as `failed`

## Impact on Existing Features

The Process Tracker enhances but does not change the core functionality of benchmarks, tool evals, and other features. The primary changes are:

- Processes persist across browser sessions
- Real-time updates via WebSocket instead of SSE (for status)
- Concurrent operations instead of one-at-a-time per user
- Better visibility into what is running and what has completed
