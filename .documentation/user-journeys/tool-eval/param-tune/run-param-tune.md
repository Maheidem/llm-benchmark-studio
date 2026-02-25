# Journey: Run Parameter Tuning

## Tier
high

## Preconditions
- User is logged in
- At least one test suite exists with tools and test cases
- At least one provider API key is configured

## Steps

### 1. Select Test Suite
- **Sees**: Suite dropdown
- **Does**: Selects a suite to evaluate against

### 2. Select Target Models
- **Sees**: Model selection grid grouped by provider with checkboxes, "All/None" toggles
- **Does**: Selects one or more models to tune

### 3. Configure Search Space
- **Sees**: Search Space Builder with parameter toggles (temperature, top_p, top_k, tool_choice, frequency_penalty, presence_penalty, repetition_penalty, min_p). Each parameter has min/max/step or value list configuration
- **Does**: Enables parameters and sets ranges. Optionally loads a saved preset via Preset Manager
- **Sees**: Compatibility Matrix showing which parameters each selected model supports

### 4. Choose Search Strategy
- **Sees**: Three radio buttons: Grid (exhaustive), Random (sampling), Bayesian (learning optimizer)
- **Does**: Selects strategy
- **Sees**: If Bayesian: Trials input (5-200, default 30) + Timeout input (0-3600s). If Random: Samples input (5-500, default 50)
- **Does**: Configures strategy-specific settings
- **Sees**: Combo estimate showing total evaluations = combos × models

### 5. Start Tuning
- **Does**: Clicks "Start Tuning" (disabled until suite + models + valid search space selected)
- **Backend**: `POST /api/tool-eval/param-tune` — submits job with suite_id, models, search_space, optimization_mode, n_trials
- **Sees**: Auto-navigates to ParamTunerRun page

### 6. Monitor Progress
- **Sees**: Progress card with: pulse dot, status detail text, strategy badge, ETA, percentage, progress bar, trial count (Bayesian/Random)
- **Sees**: For Bayesian: convergence chart (SVG line chart showing best score vs iteration)
- **WebSocket**: `tune_start` — job started. `combo_result` — new combo completed with results. `job_progress` — progress update

### 7. View Live Results
- **Sees**: Best Config highlight card (green border) showing best score, config params as badges, model name, cases passed. Live Results table (sortable by score, model, params)
- **Does**: Clicks table row → combo detail modal showing per-case results (Case ID, Expected Tool, Actual Tool, Score), adjustments (dropped/clamped params with original values)
- **WebSocket**: `tune_complete` — job finished

### 8. Use Results
- **Sees**: Completed run with best configuration highlighted
- **Does**: Can navigate to History to apply best config, save as profile, or run judge analysis

## Success Criteria
- All parameter combinations evaluated against all test cases
- Grid search tests every combination exhaustively
- Random search samples specified number of combinations
- Bayesian optimizer learns from results and converges on best params
- Convergence chart shows improvement over iterations (Bayesian)
- Best config clearly highlighted with score
- Combo detail shows per-case breakdown including dropped/clamped params
- Results persisted in database

## Error Scenarios

### No Valid Combinations
- **Trigger**: Search space produces 0 combinations
- **Sees**: Start button remains disabled
- **Recovery**: Add parameters or widen ranges

### Unsupported Parameters
- **Trigger**: Selected parameter not supported by target model
- **Sees**: Compatibility Matrix shows unsupported (param dropped/clamped during run, shown in results with badge)
- **Recovery**: Check compatibility matrix before starting

### Rate Limit
- **Trigger**: Too many tuning runs
- **Sees**: HTTP 429 error
- **Recovery**: Wait for rate limit window

### Job Fails
- **Trigger**: All API calls fail
- **Sees**: "Tuning failed" error toast
- **Recovery**: Check API keys and model availability

### User Cancels
- **Trigger**: Clicks Cancel during run
- **Sees**: Run stops, partial results preserved
- **Recovery**: Start new tune from Config page

## Maps to E2E Tests
- `e2e/tests/tool-eval/param-tuner.spec.js` — Config + run + results
- `e2e/tests/tool-eval/sprint11-2a-optimization-mode.spec.js` — Grid/Random/Bayesian modes
