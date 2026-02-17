# Prompt Tuner

The Prompt Tuner tests multiple system prompt variations against a tool eval suite to find the most effective prompt for your tool calling use case.

## How It Works

1. Define multiple **system prompt variations**
2. Each variation is injected as a system message before the test case prompt
3. The full tool eval suite runs for each variation and each model
4. Results are ranked by overall accuracy

## Configuration

Provide a list of prompt variations:

```json
{
  "suite_id": "your-suite-id",
  "models": ["gpt-4o", "anthropic/claude-sonnet-4-5"],
  "prompts": [
    {
      "label": "Baseline (no system prompt)",
      "prompt": ""
    },
    {
      "label": "Strict tool use",
      "prompt": "You are a function calling assistant. Always use the available tools to answer user requests. Never respond with text when a tool can answer the question."
    },
    {
      "label": "JSON focused",
      "prompt": "You are a precise API assistant. When calling tools, ensure all parameters are provided in the exact format specified. Use the correct data types for each parameter."
    }
  ],
  "temperature": 0.0,
  "tool_choice": "required"
}
```

## Running a Prompt Tune

1. Navigate to **Tool Eval** and select a suite
2. Click **Prompt Tuner**
3. Select models to test
4. Add prompt variations (at least 2)
5. Configure temperature and tool_choice
6. Click **Run Tune**

### Estimating Cost

Before starting, use the estimate endpoint:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/tool-eval/prompt-tune/estimate?suite_id=ID&models=2&prompts=3"
```

Total API calls = `num_prompts * num_models * num_test_cases`

## Results

The tuner produces:

- **Per-prompt scores**: Overall accuracy for each prompt variation across all models
- **Per-model breakdown**: How each model performed with each prompt
- **Best prompt**: The variation that achieved the highest accuracy
- **Comparison matrix**: Prompts vs models with accuracy percentages

## Prompt Tune History

```bash
# List prompt tune runs
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/prompt-tune/history

# Get a specific prompt tune run
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/prompt-tune/history/{tune_id}
```

## Best Practices

- Always include a "no system prompt" baseline for comparison
- Test diverse prompt styles: strict, conversational, format-focused
- Use low temperature (0.0) for consistent results
- Use `tool_choice: required` to ensure models attempt tool calls
- Keep test suites small (5-10 cases) for faster iteration during prompt development
- Test the winning prompt with a larger suite for validation
