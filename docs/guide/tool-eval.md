# Tool Calling Evaluation

The Tool Calling Evaluation framework tests whether LLMs correctly use function calling (tool use). You define tool suites with test cases and run models against them to measure accuracy.

## Core Concepts

### Tool Suite

A collection of tools and test cases -- like a test suite for function calling.

- **Tools**: Defined in OpenAI function calling JSON schema format
- **Test Cases**: Each has a prompt, an expected tool, and optionally expected parameters

### Test Case

A single evaluation scenario:

```json
{
  "prompt": "What's the weather in Paris?",
  "expected_tool": "get_weather",
  "expected_params": { "city": "Paris" },
  "param_scoring": "exact"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `prompt` | string | The user message sent to the model |
| `expected_tool` | string or list | The tool name(s) the model should call |
| `expected_params` | object or null | Expected parameter values (null = not scored) |
| `param_scoring` | string | Scoring mode: `exact` (default) |

The `expected_tool` field supports multiple acceptable answers:

```json
{
  "expected_tool": ["search", "web_search"],
  "prompt": "Find information about quantum computing"
}
```

### tool_choice Parameter

Controls whether the model must call a tool:

| Value | Behavior |
|-------|----------|
| `required` | Model MUST call a tool (recommended for testing) |
| `auto` | Model can respond with text instead of calling a tool |
| `none` | Model cannot use tools (control test) |

If a provider does not support `required`, the engine automatically falls back to `auto`.

## Scoring

### Tool Selection Score (0.0 or 1.0)

- Did the model call the expected tool?
- Case-insensitive comparison
- Supports multiple acceptable tools
- If `expected_tool` is null, scores 1.0 only if the model also called nothing

### Parameter Accuracy (0.0 - 1.0 or null)

- Per-key comparison: `correct_params / total_expected_params`
- Strings are compared case-insensitively
- Numbers use float comparison
- If `expected_params` is null, returns null (not scored)
- If `expected_params` is `{}`, returns 1.0

### Overall Score (weighted)

```
overall = 0.6 * tool_score + 0.4 * param_score
```

If parameters are not scored (null): `overall = tool_score`

## Creating a Tool Suite

### Via the UI

1. Navigate to **Tool Eval**
2. Click **New Suite**
3. Enter a name and description
4. Add tool definitions in JSON format
5. Add test cases with prompts and expected results

### Via JSON Import

Import a complete suite from a JSON file:

```json
{
  "name": "Weather API Suite",
  "description": "Tests weather tool calling",
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
          "type": "object",
          "properties": {
            "city": { "type": "string", "description": "City name" },
            "units": { "type": "string", "enum": ["celsius", "fahrenheit"] }
          },
          "required": ["city"]
        }
      }
    }
  ],
  "test_cases": [
    {
      "prompt": "What's the weather in Paris?",
      "expected_tool": "get_weather",
      "expected_params": { "city": "Paris" }
    },
    {
      "prompt": "Temperature in Tokyo in Fahrenheit",
      "expected_tool": "get_weather",
      "expected_params": { "city": "Tokyo", "units": "fahrenheit" }
    }
  ]
}
```

Import via API:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @suite.json \
  http://localhost:8501/api/tool-eval/import
```

### Via MCP Server

Import tools from an MCP (Model Context Protocol) server:

1. Click **Import from MCP**
2. Enter the MCP server SSE endpoint URL
3. The system connects, discovers tools, and creates a new suite
4. Add test cases manually for the discovered tools

## Running an Eval

1. Select a tool suite
2. Choose one or more models
3. Set temperature (default: 0.0 for deterministic results)
4. Set tool_choice (`required` recommended)
5. Click **Run Eval**

Results stream via SSE, showing per-test-case scores as they complete. After all cases finish, per-model summaries are computed and the run is saved to history.

## Multi-Turn Evaluation

Multi-turn test cases simulate tool calling chains where the model must call prerequisite tools before reaching the final expected tool.

Configure a multi-turn test case:

```json
{
  "prompt": "Book a flight from NYC to London for next Friday",
  "expected_tool": "book_flight",
  "expected_params": { "origin": "NYC", "destination": "London" },
  "multi_turn": true,
  "max_rounds": 5,
  "optimal_hops": 2,
  "valid_prerequisites": ["search_flights", "get_availability"],
  "mock_responses": {
    "search_flights": { "flights": [{"id": "FL123", "price": 450}] },
    "get_availability": { "available": true, "seats": 12 }
  }
}
```

### Multi-Turn Scoring

| Metric | Description |
|--------|-------------|
| Completion | Did the model reach the expected final tool? (weighted tool + param score) |
| Efficiency | `optimal_hops / actual_hops` (capped at 1.0) |
| Redundancy Penalty | -10% per consecutive identical tool call |
| Detour Penalty | -10% per call not in valid_prerequisites |
| Overall | `completion * efficiency - redundancy - detour` (clamped 0-1) |

## Execution Flow

```
prompt + tools + tool_choice
        |
        v
litellm.acompletion() (non-streaming)
        |
        v
response.choices[0].message.tool_calls
        |
        v
score_tool_selection() + score_params()
        |
        v
compute_overall_score()
```

## Common Issues

**100% Tool Selection, 0% Parameter Accuracy**: The model picks the right tool but hallucinates parameter values. Ensure your `expected_params` match realistic model output patterns.

**0% everything with `auto`**: The model responds with text instead of calling tools. Switch to `required` for tool_choice.

**MCP suites failing**: Auto-generated prompts from MCP tool descriptions may be too vague. Use `required` for tool_choice and write more specific prompts.
