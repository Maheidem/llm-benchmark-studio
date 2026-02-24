# Analytics & History

LLM Benchmark Studio provides analytics features for comparing models, tracking performance over time, and exporting data.

## Benchmark History

All benchmark runs are saved per-user in the database. The History screen shows:

- Run timestamp
- Prompt used
- Models benchmarked
- Context tiers
- Per-model results (tokens/sec, TTFT, cost)

### Viewing a Run

Click any run to see full details including individual iteration results, aggregated statistics, and charts.

### Deleting a Run

Runs can be deleted individually from the history view or via API:

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/history/{run_id}
```

## Leaderboard

The leaderboard ranks models across all your benchmark or tool eval runs. It supports two leaderboard types and four time periods.

```bash
# Benchmark leaderboard (default, ranked by avg tokens/sec)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/analytics/leaderboard?type=benchmark&period=all"

# Tool eval leaderboard (ranked by avg overall score)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/analytics/leaderboard?type=tool_eval&period=30d"
```

**Benchmark leaderboard** returns per-model:

- `avg_tps`: Average tokens per second
- `avg_ttft_ms`: Average time to first token (ms)
- `avg_cost`: Average cost per run
- `total_runs`: Number of runs included

**Tool eval leaderboard** returns per-model:

- `avg_tool_pct`: Average tool selection accuracy (%)
- `avg_param_pct`: Average parameter accuracy (%)
- `avg_overall_pct`: Average overall score (%)
- `total_evals`: Number of evals included

**Period filter**: `7d`, `30d`, `90d`, `all`

## Trend Analysis

Track model performance over time by selecting one or more models:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/analytics/trends?models=GPT-4o,Claude+Sonnet+4.5&metric=tps&period=30d"
```

Parameters:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `models` | Yes | Comma-separated model display names |
| `metric` | No | `tps` (default) or `ttft` |
| `period` | No | `7d`, `30d`, `90d`, or `all` (default) |

Returns time-series data points per model, displayed as line charts in the Trends tab.

## Run Comparison

Compare 2-4 specific benchmark runs side by side:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/analytics/compare?runs=run_id_1,run_id_2"
```

Select runs from your history (up to 4). Returns per-run model results including avg tokens/sec, avg TTFT, context tokens, and cost. The Compare tab displays this data as side-by-side bar charts.

## Tool Eval History

Tool eval runs are saved separately:

```bash
# List eval runs
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/history

# Get a specific eval run with full results
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/history/{eval_id}
```

Each eval run includes:

- Suite name and ID
- Models tested
- Per-case results with scores
- Per-model summaries (tool accuracy, param accuracy, overall)
- Judge verdicts (if Judge was enabled)

## Export

### CSV Export

Export benchmark history as CSV:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/export/history > benchmarks.csv
```

Export tool eval runs as CSV:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/export/tool-eval > evals.csv
```

Export leaderboard as CSV:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/export/leaderboard > leaderboard.csv
```

### JSON Export

Export a specific benchmark run:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/export/run/{run_id} > run.json
```

Export a specific eval run (includes raw request/response data):

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/export/eval/{eval_id} > eval.json
```

### Settings Backup

Export your complete configuration (providers, models, prompts):

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/export/settings > settings.json
```

Restore from backup:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @settings.json \
  http://localhost:8501/api/import/settings
```
