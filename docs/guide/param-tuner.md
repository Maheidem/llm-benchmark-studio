# Param Tuner

The Parameter Tuner performs a grid search across parameter combinations to find the optimal settings for tool calling accuracy. Think of it as GridSearchCV for LLM tool calling.

## How It Works

1. Define a **search space** with parameter ranges
2. The tuner generates all combinations (Cartesian product)
3. Each combination runs the full tool eval suite
4. Results are ranked by overall accuracy

## Search Space Configuration

Define parameter ranges as numeric ranges or categorical values:

```json
{
  "temperature": { "min": 0.0, "max": 1.0, "step": 0.2 },
  "tool_choice": ["auto", "required"],
  "top_p": { "min": 0.5, "max": 1.0, "step": 0.25 }
}
```

This produces combinations like:

| temperature | tool_choice | top_p |
|-------------|-------------|-------|
| 0.0 | auto | 0.5 |
| 0.0 | auto | 0.75 |
| 0.0 | auto | 1.0 |
| 0.0 | required | 0.5 |
| ... | ... | ... |

### Numeric Ranges

```json
{
  "param_name": { "min": 0.0, "max": 1.0, "step": 0.1 }
}
```

Generates: `[0.0, 0.1, 0.2, ..., 1.0]`

### Categorical Values

```json
{
  "tool_choice": ["auto", "required"]
}
```

## Running a Tune

1. Navigate to **Tool Eval** and select a suite
2. Click **Param Tuner**
3. Select models to tune
4. Define the search space
5. Click **Run Tune**

Results stream via SSE, showing each combination's scores as they complete.

### Estimating Run Count

Before starting, review the total combinations:

```
total_combinations = product_of_all_param_values * num_models
total_api_calls = total_combinations * num_test_cases
```

!!! warning "Cost Awareness"
    A search space with many parameters and fine steps can produce thousands of combinations. Each combination runs the full test suite against each model. Monitor costs carefully.

## Results

The tuner produces:

- **Per-combination scores**: Overall accuracy for each parameter set
- **Best combination**: The parameters that achieved the highest accuracy
- **Comparison table**: Side-by-side results for all combinations
- **Provider-specific adjustments**: Notes on parameter clamping or conflicts

Results are saved to history and can be reviewed later.

## Tune History

```bash
# List tune runs
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/param-tune/history

# Get a specific tune run
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/param-tune/history/{tune_id}
```

## Provider Parameter Validation

The tuner respects provider-specific parameter rules from the [Provider Parameter Registry](../api/config-schema.md):

- Parameters are clamped to valid ranges
- Unsupported parameters are automatically removed
- Conflicts are resolved (e.g., Anthropic's temperature + top_p restriction)
- Adjustments are reported in the results

## Best Practices

- Start with a coarse grid (large steps) to identify promising regions
- Then refine with a fine grid around the best-performing area
- Use `temperature` and `tool_choice` as primary axes -- they have the most impact
- Keep the number of test cases manageable (5-10) for faster iteration
