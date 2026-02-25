# Journey: Run Prompt Tuning

## Tier
high

## Preconditions
- User is logged in
- At least one test suite exists with tools and test cases
- At least one provider API key is configured

## Steps

### 1. Select Test Suite
- **Sees**: Suite dropdown
- **Does**: Selects a suite

### 2. Choose Tuning Mode
- **Sees**: Two mode cards: Quick ("Generate N prompts, evaluate once") and Evolutionary ("Multiple generations with selection, best survive and mutate")
- **Does**: Clicks preferred mode card

### 3. Select Meta Model
- **Sees**: Model dropdown for meta model (the model that GENERATES/MUTATES prompts)
- **Does**: Selects a model (e.g., "claude-opus-4-6 (anthropic)")

### 4. Select Target Models
- **Sees**: Model selection grid (models to EVALUATE prompts ON)
- **Does**: Selects one or more target models

### 5. Configure Base Prompt
- **Sees**: Base System Prompt textarea (default provided), "Save to Library" button, "Load from Library" dropdown
- **Does**: Either types custom prompt, or clicks "Load from Library" → selects a saved version
- **Sees**: If loading from library: scrollable dropdown with saved versions showing version #, label, source badge, date

### 6. Configure Tuning Parameters
- **Sees**: Population Size (2-20), Generations (1-10, evolutionary only), Selection Ratio (0.1-0.9, evolutionary only)
- **Does**: Adjusts parameters
- **Sees**: Estimate updates: "X API calls, ~Ys" (warning if >100 API calls)
- **Backend**: `GET /api/tool-eval/prompt-tune/estimate` — debounced estimate on config change

### 7. Start Tuning
- **Does**: Clicks "Start Prompt Tuning"
- **Backend**: `POST /api/tool-eval/prompt-tune` — submits job with suite_id, mode, meta_model, target_models, base_prompt, config
- **Sees**: Auto-navigates to PromptTunerRun page

### 8. Monitor Progress
- **Sees**: Progress card (pulse dot, status detail, ETA, percentage, progress bar), Generation Timeline showing prompts tested per generation
- **WebSocket**: `tune_start`, `generation_start`, `prompt_generated`, `prompt_eval_start`, `prompt_eval_result`, `generation_complete`

### 9. View Best Prompt
- **Sees**: Best Prompt highlight card (green border) with: best score (%), prompt text (expandable), action buttons: "Apply to Context", "Save to Library"/"View in Library", "Copy"
- **WebSocket**: `tune_complete` — run finished. Best prompt auto-saved to library
- **Sees**: Toast "Best prompt auto-saved to Prompt Library"

## Success Criteria
- Meta model generates diverse prompt variants
- Each variant evaluated against all test cases on all target models
- Quick mode: single generation of N prompts
- Evolutionary mode: multi-generation with selection and mutation
- Generation timeline shows progress through generations
- Best prompt clearly displayed with score
- Best prompt auto-saved to Prompt Library on completion
- "Apply to Context" sets system prompt for use in other tabs

## Error Scenarios

### No Suite Selected
- **Trigger**: Missing suite selection
- **Sees**: Start button disabled
- **Recovery**: Select a suite

### No Meta Model Selected
- **Trigger**: Missing meta model
- **Sees**: Start button disabled
- **Recovery**: Select a meta model

### API Call Estimate Warning
- **Trigger**: Config would require >100 API calls
- **Sees**: Warning text in estimate
- **Recovery**: Reduce population size or generations

### Tuning Fails
- **Trigger**: Meta model or target model API errors
- **Sees**: "Tuning failed" error toast
- **Recovery**: Check API keys and model availability

### User Cancels
- **Trigger**: Clicks Cancel during run
- **Sees**: Run stops, partial results available
- **Recovery**: Start new tune

## Maps to E2E Tests
- `e2e/tests/tool-eval/prompt-tuner.spec.js` — Config + run + best prompt
