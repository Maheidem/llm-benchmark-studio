# Judge System

The Judge System uses one LLM to evaluate another LLM's tool calling performance. It provides qualitative analysis beyond the numerical scoring of the eval engine.

## Overview

After running a tool evaluation, the Judge reviews each test case result and provides:

- A quality score (1-5)
- A verdict (pass, marginal, or fail)
- Tool selection assessment
- Parameter assessment
- Detailed reasoning

The Judge also generates cross-case analysis reports with overall grades and recommendations.

## Judge Modes

### Live Inline

The Judge evaluates each result concurrently as the eval runs. Verdicts appear in real-time alongside eval results.

- Best for: Interactive evaluation sessions
- Trade-off: Adds latency to the overall eval run
- Concurrency: Controlled via `judge_concurrency` (default 4); auto-capped to 1 when the judge shares an endpoint with eval models

### Post-Eval

The Judge reviews all results after the eval completes. This runs as a separate pass over all results.

- Best for: Batch analysis, when you want eval results first
- Trade-off: Additional time after eval finishes
- Can also be run as a standalone operation on any existing eval run

## Configuring the Judge

### During Eval (Integrated)

When running a tool eval, enable the Judge via the `judge` config:

```json
{
  "suite_id": "...",
  "models": ["gpt-4o"],
  "judge": {
    "enabled": true,
    "mode": "post_eval",
    "judge_model": "anthropic/claude-sonnet-4-5",
    "judge_provider_key": "anthropic",
    "custom_instructions": "Focus on parameter completeness."
  },
  "judge_concurrency": 4
}
```

### Standalone Post-Eval Judge

Run the Judge independently on an existing eval run:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "eval_run_id": "eval-123",
    "judge_model": "gpt-4o",
    "judge_provider_key": "openai",
    "custom_instructions": "",
    "concurrency": 4
  }' \
  http://localhost:8501/api/tool-eval/judge
```

### Custom Instructions

You can provide custom instructions to tailor the Judge's evaluation:

```
Focus on parameter completeness. A tool call should be considered
a failure if any required parameter is missing, even if the tool
selection is correct. Pay special attention to date formats.
```

## Execution via JobRegistry

All judge operations execute through the **JobRegistry**, providing background execution, cancellation, and WebSocket progress updates.

The API returns `{"job_id": "...", "status": "submitted"}`. Progress and results are delivered via WebSocket.

### WebSocket Event Flow (Post-Eval Judge)

```
POST /api/tool-eval/judge  -->  { job_id, status: "submitted" }
                                      |
                             WebSocket events:
                                      |
    judge_start  --->  judge_verdict (per case, concurrent)
                              |
                       job_progress (percentage)
                              |
                       judge_report (per model, cross-case analysis)
                              |
                       judge_complete
```

Key WebSocket event types:

| Event | Description |
|-------|-------------|
| `judge_start` | Judge session started, includes mode, judge_model, cases_to_review |
| `judge_verdict` | Individual verdict for one test case |
| `judge_report` | Cross-case analysis report for one model |
| `judge_complete` | All verdicts and reports finished |
| `job_progress` | Progress percentage (Judge: N/M) |

## Judge Verdicts

For each test case, the Judge produces:

| Field | Type | Description |
|-------|------|-------------|
| `quality_score` | int (1-5) | Overall quality rating |
| `verdict` | string | `pass`, `marginal`, or `fail` |
| `summary` | string | One-line summary (max 100 chars) |
| `reasoning` | string | Detailed 2-3 sentence explanation |
| `tool_selection_assessment` | string | `correct`, `acceptable_alternative`, or `wrong` |
| `param_assessment` | string | `exact`, `close`, `partial`, or `wrong` |

## Cross-Case Reports

After evaluating all cases for a model, the Judge generates a cross-case report:

| Field | Description |
|-------|-------------|
| `overall_grade` | Letter grade (A-F with +/-) |
| `overall_score` | Numeric score (0-100) |
| `strengths` | List of identified strengths |
| `weaknesses` | List of identified weaknesses |
| `cross_case_analysis` | Paragraph of pattern analysis |
| `recommendations` | List of specific improvement recommendations |

## Judge Compare

Compare two tool eval runs side by side using the Judge.

### When to Use

- Comparing the same model with different parameters
- Comparing different models on the same test suite
- Before/after prompt changes
- Validating improvements from param tuning or prompt tuning

### Running a Comparison

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "eval_run_id_a": "eval-run-A",
    "eval_run_id_b": "eval-run-B",
    "judge_model": "gpt-4o",
    "judge_provider_key": "openai",
    "concurrency": 4
  }' \
  http://localhost:8501/api/tool-eval/judge/compare
```

Both eval runs must share at least one common test case. The Judge evaluates only the intersection of test cases.

### Compare WebSocket Events

| Event | Description |
|-------|-------------|
| `compare_start` | Comparison started, includes model names and case count |
| `compare_case` | Per-case comparison result with winner, confidence, reasoning |
| `compare_complete` | Summary with overall_winner, score_a, score_b |
| `job_progress` | Progress percentage (Compare: N/M) |

### Compare Summary

The final summary includes:

```json
{
  "overall_winner": "model_a",
  "score_a": 78,
  "score_b": 65,
  "summary": "Model A outperformed Model B on parameter accuracy...",
  "tie_cases": 1
}
```

## Cancellation

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "abc123"}' \
  http://localhost:8501/api/tool-eval/judge/cancel
```

Cancellation is cooperative and partial results are saved.

## Retry and Error Handling

The Judge uses exponential backoff for transient errors:

- Retries on 502, 503, 500, connection errors, and timeouts
- Up to 3 attempts with delays of 2s, 4s, 8s
- Non-transient errors (auth, 400, 404, rate-limit) propagate immediately
- Judge uses temperature 0.0 for reproducible assessments
- max_tokens set to 2048 for detailed reasoning

## Judge Reports API

```bash
# List judge reports
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/judge/reports

# Get a specific report
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/judge/reports/{report_id}

# Delete a report
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/judge/reports/{report_id}
```

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tool-eval/judge` | Run post-eval judge (returns job_id) |
| `POST` | `/api/tool-eval/judge/compare` | Run comparative judge (returns job_id) |
| `POST` | `/api/tool-eval/judge/cancel` | Cancel running judge |
| `GET` | `/api/tool-eval/judge/reports` | List judge reports |
| `GET` | `/api/tool-eval/judge/reports/{id}` | Get report details |
| `DELETE` | `/api/tool-eval/judge/reports/{id}` | Delete report |

## Best Practices

- Use a capable model as the Judge (e.g., Claude Sonnet 4.5, GPT-4o)
- Avoid using the same model as both the test subject and the Judge
- Custom instructions help focus the Judge on what matters for your use case
- Post-eval mode is more cost-effective for large eval runs
- When the judge model shares an endpoint with eval models (common with local LLMs), concurrency is auto-capped to 1 to prevent overload
- Use comparative judge to validate improvements after param tuning or prompt tuning
- Link judge runs to experiments for unified timeline tracking
