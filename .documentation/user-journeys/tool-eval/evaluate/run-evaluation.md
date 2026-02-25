# Journey: Run a Tool Evaluation

## Tier
critical

## Preconditions
- User is logged in
- At least one test suite exists with tools and test cases
- At least one provider API key is configured

## Steps

### 1. Select Suite
- **Sees**: Suite dropdown showing available suites with (N tools, M cases) counts
- **Does**: Selects a suite
- **Backend**: `GET /api/tool-suites` — loads suites list

### 2. Select Models
- **Sees**: Model selection grid grouped by provider with checkboxes, "All"/"None" toggles per provider
- **Does**: Checks one or more models

### 3. Configure Evaluation Settings
- **Sees**: Settings card with: Temperature (0-2), Tool Choice (required/auto/none), Auto-run Judge checkbox with threshold slider
- **Does**: Sets temperature, tool choice mode. Optionally enables auto-judge
- **Sees**: If tool_choice=required AND suite has irrelevance test cases (should_call_tool=false): warning banner suggesting "auto" mode

### 4. Set System Prompts (optional)
- **Sees**: Per-model system prompt textarea (overrides suite default)
- **Does**: Optionally customizes system prompt per model

### 5. Assign Profiles (optional)
- **Sees**: Per-model profile dropdown (if profiles exist)
- **Does**: Optionally selects saved parameter profiles for models

### 6. Start Evaluation
- **Does**: Clicks "Start Eval"
- **Backend**: `POST /api/tool-eval` — submits eval job, returns `{job_id}`
- **WebSocket**: `tool_eval_init` — confirms start with suite_name, targets, total_cases

### 7. Monitor Progress
- **Sees**: Pulse indicator, progress label ("Testing gpt-5.2... — 23s left"), progress bar with percentage, case counter (current/total)
- **Does**: Watches progress (can click "Cancel" to abort)
- **WebSocket**: `tool_eval_progress` — per-case progress (current, total, model). `tool_eval_result` — individual test case result (model, expected tool, actual tool, score)

### 8. View Live Results
- **Sees**: Live results table updating in real-time: Model, Prompt, Expected Tool, Actual Tool, Hops, Score. Irrelevance cases marked with "IRREL" badge
- **WebSocket**: `tool_eval_summary` — per-model summary (tool_selection_score, param_accuracy_score, overall_score)

### 9. View Final Summary
- **Sees**: Summary results table (EvalResultsTable) with per-model scores
- **Does**: Clicks model row → detail modal showing per-case breakdown
- **WebSocket**: `tool_eval_complete` — eval finished with eval_id and summary
- **Sees**: If auto-judge enabled and score below threshold: judge job starts automatically. Banner appears when judge completes with "View Report →" link

## Success Criteria
- All selected models evaluated against all test cases in suite
- Live results table updates in real-time via WebSocket
- Per-model summary shows tool selection %, param accuracy %, overall %
- Irrelevance detection works (models should abstain on should_call_tool=false cases)
- Multi-turn test cases chain correctly (tool calls → mock responses → next tool call)
- Results persisted to database (visible in History tab)
- Auto-judge triggers if enabled and score below threshold

## Error Scenarios

### No Suite Selected
- **Trigger**: User clicks Start without selecting suite
- **Sees**: Toast error
- **Recovery**: Select a suite first

### No Models Selected
- **Trigger**: User clicks Start without selecting models
- **Sees**: Toast error
- **Recovery**: Select at least one model

### Eval Already Running
- **Trigger**: User tries to start while another eval is in progress
- **Sees**: HTTP 409 error
- **Recovery**: Wait for current eval to finish or cancel it

### Provider Error During Eval
- **Trigger**: API key invalid, model unavailable, timeout
- **Sees**: Individual test case shows error in results table (score=0)
- **Recovery**: Check API keys, results for failed cases still saved

### User Cancels
- **Trigger**: User clicks Cancel during eval
- **Sees**: "Eval cancelled" toast, partial results preserved
- **Recovery**: Start a new eval

## Maps to E2E Tests
- `e2e/tests/tool-eval/evaluate-run.spec.js` — Full eval run with results
- `e2e/tests/tool-eval/irrelevance-detection.spec.js` — Irrelevance badges and warnings
