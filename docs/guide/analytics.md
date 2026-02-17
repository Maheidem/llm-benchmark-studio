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

The leaderboard ranks models across all your benchmark runs:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/analytics/leaderboard
```

Models are ranked by:

- **Speed**: Average tokens per second
- **Latency**: Average time to first token
- **Cost**: Average cost per run
- **Overall**: Composite score

## Trend Analysis

Track model performance over time:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/analytics/trends?model_id=gpt-4o&days=30"
```

Shows how tokens/sec, TTFT, and cost have changed across benchmark runs for a specific model.

## Model Comparison

Compare two or more models side by side:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/analytics/compare?models=gpt-4o,anthropic/claude-sonnet-4-5"
```

Returns comparative metrics, charts, and statistical analysis.

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
