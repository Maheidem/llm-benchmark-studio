# Judge System

The Judge System uses one LLM to evaluate another LLM's tool calling performance. It provides qualitative analysis beyond the numerical scoring of the eval engine.

## Overview

After running a tool evaluation, the Judge reviews each test case result and provides:

- A quality score (0-10)
- A verdict (pass/fail/partial)
- Tool selection assessment
- Parameter assessment
- Detailed reasoning

The Judge also generates cross-case analysis reports with overall grades and recommendations.

## Judge Modes

### Live Inline

The Judge evaluates each result concurrently as the eval runs. Verdicts appear in real-time alongside eval results.

- Best for: Interactive evaluation sessions
- Trade-off: Adds latency to the overall eval run

### Post-Eval

The Judge reviews all results after the eval completes. This runs a separate pass over all results.

- Best for: Batch analysis, when you want eval results first
- Trade-off: Additional time after eval finishes

## Configuring the Judge

When running a tool eval, enable the Judge:

1. Toggle **Enable Judge** in the eval configuration
2. Select a **Judge Mode** (live inline or post-eval)
3. Choose a **Judge Model** (any model in your configuration)
4. Optionally add **Custom Instructions** to guide the Judge's evaluation criteria

### Custom Instructions

You can provide custom instructions to tailor the Judge's evaluation:

```
Focus on parameter completeness. A tool call should be considered
a failure if any required parameter is missing, even if the tool
selection is correct. Pay special attention to date formats.
```

## Judge Verdicts

For each test case, the Judge produces:

| Field | Description |
|-------|-------------|
| `quality_score` | 0-10 rating |
| `verdict` | pass, fail, or partial |
| `summary` | One-line summary |
| `reasoning` | Detailed explanation |
| `tool_selection_assessment` | Evaluation of tool choice |
| `param_assessment` | Evaluation of parameter accuracy |

## Cross-Case Reports

After evaluating all cases for a model, the Judge generates a report:

- Overall grade (A-F)
- Overall score (0-100)
- Strengths and weaknesses
- Specific recommendations
- Pattern analysis across test cases

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

## Judge Compare

Compare two tool eval runs side by side using the Judge:

1. Navigate to Tool Eval History
2. Select two eval runs to compare
3. Click **Judge Compare**
4. The Judge analyzes differences between the two runs

This is useful for comparing:

- The same model with different parameters
- Different models on the same test suite
- Before/after prompt changes

## Best Practices

- Use a capable model as the Judge (e.g., Claude Sonnet 4.5, GPT-4o)
- Avoid using the same model as both the test subject and the Judge
- Custom instructions help focus the Judge on what matters for your use case
- Post-eval mode is more cost-effective for large eval runs
