# Scheduling

LLM Benchmark Studio supports automated, recurring benchmark runs. Schedules are managed per-user and execute in a background task on the server.

## How It Works

1. Create a schedule with a name, models, prompt, and interval
2. The background scheduler checks for due schedules every 60 seconds
3. When a schedule is due, it runs the specified benchmarks via the JobRegistry
4. Results are saved to benchmark history with metadata indicating the source
5. Real-time status updates for triggered schedules are broadcast via WebSocket

## Creating a Schedule

### Via the UI

1. Navigate to the **Scheduling** screen
2. Click **New Schedule**
3. Configure:
    - **Name**: A descriptive name for the schedule
    - **Models**: Select which models to benchmark
    - **Prompt**: The benchmark prompt
    - **Max Tokens**: Output token limit (default: 512)
    - **Temperature**: Sampling temperature (default: 0.7)
    - **Interval**: Hours between runs
4. Click **Create**

### Via the API

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Nightly GPT-4o benchmark",
    "models": ["gpt-4o", "gpt-4o-mini"],
    "prompt": "Explain recursion in programming with a Python example.",
    "max_tokens": 512,
    "temperature": 0.7,
    "interval_hours": 24
  }' \
  http://localhost:8501/api/schedules
```

## Managing Schedules

### List Schedules

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/schedules
```

### Update a Schedule

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' \
  http://localhost:8501/api/schedules/{schedule_id}
```

### Delete a Schedule

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/schedules/{schedule_id}
```

### Trigger Immediately

Run a schedule immediately without waiting for the next scheduled time:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/schedules/{schedule_id}/trigger
```

The trigger endpoint runs the benchmark as a background asyncio task and returns immediately. After execution, the schedule's `last_run` and `next_run` timestamps are updated.

## Schedule Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Schedule name |
| `prompt` | string | Benchmark prompt |
| `models_json` | list | Model IDs to benchmark |
| `max_tokens` | int | Max output tokens |
| `temperature` | float | Sampling temperature |
| `interval_hours` | int | Hours between runs |
| `enabled` | boolean | Whether the schedule is active |
| `last_run` | datetime | When it last ran |
| `next_run` | datetime | When it will next run |

## How Results Are Stored

Scheduled benchmark results are saved to the same `benchmark_runs` table as manual benchmarks. The metadata field identifies them as scheduled:

```json
{
  "source": "schedule",
  "schedule_id": "abc123",
  "schedule_name": "Nightly GPT-4o benchmark"
}
```

Results appear in the History screen alongside manual runs.

## Integration with Job Tracking

Scheduled benchmarks are tracked by the JobRegistry like any other job type (`scheduled_benchmark`). This means:

- Scheduled runs appear in the notification widget when active
- Progress is visible via WebSocket events
- Active scheduled runs count toward the user's concurrency limit
- If the server restarts during a scheduled run, the job is marked as `interrupted`

## Notes

- Scheduled runs execute single-run per model (no multi-run averaging)
- Warmup runs are not performed for scheduled benchmarks
- Context tier is fixed at `[0]` (no context scaling)
- The scheduler uses per-user API keys, so each user's keys are used for their own schedules
- If the server restarts, the scheduler resumes checking on the next 60-second cycle
