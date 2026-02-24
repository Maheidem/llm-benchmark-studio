# Prompt Tuner

The Prompt Tuner uses a meta-model to generate and evaluate system prompt variations against a tool eval suite. It supports two modes: Quick (single generation) and Evolutionary (multi-generation with selection pressure).

## How It Works

1. A **meta-model** generates multiple system prompt variations
2. Each variation is injected as a system message before the test case prompt
3. The full tool eval suite runs for each variation and each target model
4. Results are ranked by overall accuracy
5. In evolutionary mode, top performers survive to seed the next generation

## Modes

### Quick Mode

Single generation of prompt variations. Best for rapid exploration.

- One meta-model call to generate `population_size` prompts
- Each prompt evaluated against all models and test cases
- Results ranked, best prompt identified

### Evolutionary Mode

Multi-generation optimization with selection pressure. Best for finding high-quality prompts.

- Generation 1: Same as Quick mode
- Selection: Top performers (based on `selection_ratio`) survive
- Subsequent generations: Meta-model mutates surviving prompts
- Supports bold mutations (significantly different approach) and conservative mutations (small refinements)

## Configuration

### Request Body

```json
{
  "suite_id": "your-suite-id",
  "mode": "quick",
  "target_models": ["gpt-4o", "anthropic/claude-sonnet-4-5"],
  "target_targets": [
    {"provider_key": "openai", "model_id": "gpt-4o"},
    {"provider_key": "anthropic", "model_id": "anthropic/claude-sonnet-4-5"}
  ],
  "meta_model": "gpt-4o",
  "meta_provider_key": "openai",
  "base_prompt": "You are a helpful assistant that uses tools to answer questions.",
  "config": {
    "population_size": 5,
    "generations": 3,
    "selection_ratio": 0.4,
    "temperature": 0.0,
    "tool_choice": "required"
  },
  "experiment_id": "optional-experiment-id"
}
```

### Config Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `population_size` | 5 | 3-20 | Number of prompts per generation |
| `generations` | 1 (quick) / 3 (evo) | 1-10 | Number of generations (forced to 1 in quick mode) |
| `selection_ratio` | 0.4 | 0.2-0.8 | Fraction of prompts that survive each generation |
| `temperature` | 0.0 | 0.0-2.0 | Temperature for eval calls (not meta-model) |
| `tool_choice` | required | auto/required/none | Tool choice for eval calls |

### Meta-Model Requirements

The meta-model generates prompt variations. Choose a capable model:

- Must support text generation (any LLM in your config)
- The meta-model is called with temperature 0.9 and max_tokens 4096
- If the model supports structured output (JSON schema), it is automatically used
- If a model rejects structured output, the tuner retries without it
- Transient errors (502, 503, timeout) are retried with exponential backoff

## Running a Prompt Tune

### Execution via JobRegistry

All prompt tune runs execute through the **JobRegistry**, providing background execution, cancellation, and WebSocket progress updates.

### Steps

1. Navigate to **Tool Eval** and select a suite
2. Click **Prompt Tuner**
3. Select **target models** to test against
4. Select a **meta-model** for prompt generation
5. Optionally set a base prompt to improve upon
6. Configure mode (Quick or Evolutionary) and parameters
7. Optionally link to an **Experiment** for tracking
8. Click **Run Tune**

The API returns `{"job_id": "...", "status": "submitted"}`.

### Estimating Cost

Before starting, use the estimate endpoint:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8501/api/tool-eval/prompt-tune/estimate?suite_id=ID&mode=quick&population_size=5&num_models=2"
```

Returns:

```json
{
  "total_prompt_generations": 5,
  "total_eval_calls": 50,
  "total_api_calls": 51,
  "estimated_duration_s": 105,
  "warning": null
}
```

Total API calls = `generations * 1 (meta call) + total_prompts * num_cases * num_models`

### WebSocket Event Flow

```
POST /api/tool-eval/prompt-tune  -->  { job_id, status: "submitted" }
                                           |
                                  WebSocket events:
                                           |
    tune_start  --->  generation_start  --->  prompt_generated (per prompt)
                                                      |
                                               prompt_eval_start (per model)
                                                      |
                                               prompt_eval_result (per model)
                                                      |
                                              generation_complete (survivors)
                                                      |
                                               [next generation or...]
                                                      |
                                               tune_complete
```

Key WebSocket event types:

| Event | Description |
|-------|-------------|
| `tune_start` | Tuning session started, includes mode, total_prompts, total_eval_calls |
| `generation_start` | New generation beginning |
| `prompt_generated` | Meta-model produced a prompt variation (includes text, style, parent_index) |
| `prompt_eval_start` | Starting eval of a prompt on a specific model |
| `prompt_eval_result` | Eval result for one prompt on one model (overall_score, tool_accuracy, param_accuracy) |
| `generation_complete` | Generation finished, includes best_score and survivor indices |
| `generation_error` | Meta-model returned no prompts for this generation |
| `tune_complete` | Tuning finished, includes best_prompt and best_score |

## Results

The tuner produces:

- **Per-prompt scores**: Overall accuracy for each prompt variation across all models
- **Per-model breakdown**: How each model performed with each prompt
- **Best prompt**: The variation that achieved the highest cross-model accuracy
- **Generation history**: In evolutionary mode, tracks improvement across generations
- **Survivor tracking**: Which prompts survived selection in each generation

### Generation Result Structure

Each generation contains:

```json
{
  "generation": 1,
  "prompts": [
    {
      "index": 0,
      "style": "concise",
      "text": "Use the available tools...",
      "parent_index": null,
      "mutation_type": null,
      "scores": {
        "gpt-4o": { "overall": 0.85, "tool_acc": 90.0, "param_acc": 80.0 },
        "claude-sonnet-4-5": { "overall": 0.92, "tool_acc": 100.0, "param_acc": 85.0 }
      },
      "avg_score": 0.885,
      "survived": true
    }
  ],
  "best_index": 0,
  "best_score": 0.885
}
```

## Experiment Integration

When a prompt tune is linked to an experiment:

1. The best prompt's score is compared against the experiment's current best
2. If it improves, the experiment's **best config** is updated with the winning system prompt
3. A `promoted_from: "prompt_tune:{tune_id}"` marker is added
4. The experiment timeline shows prompt tune entries with score and delta from baseline

## Prompt Tune History

```bash
# List prompt tune runs
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/prompt-tune/history

# Get a specific prompt tune run
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/prompt-tune/history/{tune_id}

# Delete a prompt tune run
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8501/api/tool-eval/prompt-tune/history/{tune_id}
```

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tool-eval/prompt-tune` | Start prompt tuning (returns job_id) |
| `POST` | `/api/tool-eval/prompt-tune/cancel` | Cancel running tune |
| `GET` | `/api/tool-eval/prompt-tune/estimate` | Get cost/time estimate |
| `GET` | `/api/tool-eval/prompt-tune/history` | List tune runs |
| `GET` | `/api/tool-eval/prompt-tune/history/{id}` | Get tune run details |
| `DELETE` | `/api/tool-eval/prompt-tune/history/{id}` | Delete tune run |

## Cancellation

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "abc123"}' \
  http://localhost:8501/api/tool-eval/prompt-tune/cancel
```

Cancellation is cooperative: the handler checks a cancel event between eval calls and stops gracefully. Partial results are saved to the database.

## Best Practices

- Always include a "no system prompt" baseline for comparison (set `base_prompt` to an empty string)
- Use a capable meta-model (GPT-4o, Claude Sonnet 4.5) for better prompt generation
- Avoid using the same model as both the meta-model and the target model
- Use low temperature (0.0) for consistent eval results
- Use `tool_choice: required` to ensure models attempt tool calls
- Start with Quick mode to get a sense of what works
- Then switch to Evolutionary mode (3-5 generations) to refine the best approaches
- Keep test suites small (5-10 cases) for faster iteration during prompt development
- Test the winning prompt with a larger suite for validation
- Link tune runs to an experiment to track improvement over time
- The meta-model uses temperature 0.9 for creative diversity in prompt generation
