# Journey: Run Auto-Optimize (OPRO-style)

## Tier
high

## Preconditions
- User is logged in
- At least one test suite exists with tools and test cases
- At least one provider API key is configured
- A meta model (optimization model) is available

## Steps

### 1. Configure Optimization
- **Sees**: Configuration form with: Test Suite selector, Base System Prompt textarea, Optimization Model dropdown, Max Iterations (1-20, default 5), Population Size (2-20, default 5)
- **Does**: Selects suite, enters base prompt, selects optimization model, adjusts iterations/population

### 2. Start Optimization
- **Does**: Clicks "Start Auto-Optimize" (disabled until suite + model selected)
- **Backend**: `POST /api/tool-eval/prompt-tune/auto-optimize` — submits job with suite_id, base_prompt, optimization_model, max_iterations, population_size
- **Sees**: UI transitions from config form to progress state

### 3. Monitor Progress
- **Sees**: Progress card with: pulse dot, detail text ("Evaluating variant 3 of 5..."), ETA, percentage, progress bar, iteration indicator ("Iteration N/MAX"), Cancel button
- **Sees**: Live Rankings card: ranked list of variants sorted by score (highest first), each showing: rank, prompt text (truncated), iteration number, score (colored: >=80% lime, 50-79% yellow, <50% red)
- **WebSocket**: `auto_optimize_start` — job begins. `prompt_generated` / `auto_optimize_variant` — new variant added. `auto_optimize_progress` / `job_progress` — progress update

### 4. View Results
- **Sees**: UI transitions to complete state. Best Prompt card (lime green border): best score, prompt text (expandable), action buttons: "Use This Prompt", "Save to Library"/"View in Library", "New Run". All Variants card: ranked list with score and "Use" button per variant
- **WebSocket**: `auto_optimize_complete` / `tune_complete` / `job_completed` — job done

### 5. Apply Best Prompt
- **Does**: Clicks "Use This Prompt"
- **Sees**: Toast "Best prompt applied to shared context"
- **Backend**: None (updates shared context system prompt + config)

### 6. Save to Library (auto or manual)
- **Sees**: Best prompt auto-saved on completion. Can also click "Save to Library" manually
- **Backend**: `POST /api/prompt-versions` — saves as new prompt version with source="auto_optimize"
- **Sees**: Button changes to "View in Library ->"

### 7. Use Any Variant (optional)
- **Does**: Clicks "Use" on any variant in the All Variants list
- **Sees**: Toast "Prompt applied to shared context"

### 8. New Run (optional)
- **Does**: Clicks "New Run"
- **Sees**: UI resets to config form for fresh optimization

## Success Criteria
- Optimization model generates diverse prompt variants
- Each variant evaluated against all test cases
- Iterative improvement: later iterations should score equal or better
- Live rankings update in real-time as variants are evaluated
- Best prompt clearly highlighted with score
- Best prompt auto-saved to Prompt Library
- Any variant can be applied to shared context
- 3-state UI transitions cleanly: config -> running -> complete

## Error Scenarios

### No Suite Available
- **Trigger**: No test suites created
- **Sees**: Suite dropdown empty
- **Recovery**: Create a test suite first

### No Optimization Model
- **Trigger**: No models available
- **Sees**: Model dropdown empty
- **Recovery**: Configure providers and API keys

### Missing Required Fields
- **Trigger**: Suite or model not selected
- **Sees**: Start button disabled, validation error displayed
- **Recovery**: Fill all required fields

### Job Fails
- **Trigger**: Optimization model API error
- **Sees**: Toast "Auto-optimize failed" + error message
- **Recovery**: Check API key for optimization model

### User Cancels
- **Trigger**: Clicks Cancel during run
- **Sees**: Toast "Run cancelled", partial results may be visible
- **Recovery**: Click "New Run" to start over

## Maps to E2E Tests
- `e2e/tests/tool-eval/sprint11-i2-auto-optimize.spec.js` — Full auto-optimize run
