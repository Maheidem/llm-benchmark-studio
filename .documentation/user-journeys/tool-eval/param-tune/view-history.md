# Journey: View Param Tune History

## Tier
high

## Preconditions
- User is logged in
- At least one param tuning run has completed

## Steps

### 1. Load History
- **Sees**: Run list cards, each showing: suite name, status badge, strategy badge (Grid/Random/Bayesian), date, combos completed/total, duration, best score (color-coded), best prompt preview
- **Does**: Waits for page to load. Can click "New Tune" to start fresh
- **Backend**: `GET /api/tool-eval/param-tune/history` — loads all runs

### 2. View Run Details
- **Does**: Clicks a run card
- **Sees**: Detail modal with results table (ParamTunerResults), sortable by score/model/params
- **Backend**: `GET /api/tool-eval/param-tune/history/{id}` — loads full run details

### 3. Apply Best Config
- **Does**: Clicks "Apply" button on run card
- **Sees**: Toast "Config applied to shared context"
- **Backend**: None (updates shared context with temperature, tool_choice, provider_params)

### 4. Save as Profile (optional)
- **Does**: Clicks "Save Profile"
- **Sees**: Input modal for profile name
- **Does**: Enters name, clicks Save
- **Backend**: `POST /api/profiles/create` — saves as reusable model profile with source="param_tuner"
- **Sees**: Toast "Profile saved"

### 5. Run Judge Analysis (optional)
- **Does**: Clicks "Judge" button
- **Backend**: `POST /api/tool-eval/judge` — submits judge job with tune_run_id + tune_type="param_tuner"
- **Sees**: Judge job starts in background

### 6. View Correlation (optional, if eval_run_id exists)
- **Does**: Clicks "Score with Judge" in detail modal
- **Sees**: 3-axis visualization (throughput × cost × quality). Clickable points show combo details
- **Backend**: `POST /api/param-tune/correlation/{run_id}/score` — triggers judge scoring. `GET /api/param-tune/correlation/{run_id}` — loads correlation data

### 7. Delete Run (optional)
- **Does**: Clicks delete icon, confirms
- **Backend**: `DELETE /api/tool-eval/param-tune/history/{id}`
- **Sees**: Run removed from list

## Success Criteria
- All past param tune runs visible with key metrics
- Apply best config loads settings into shared context for use in other tabs
- Save as profile creates reusable configuration
- Judge integration shows quality scores alongside throughput/cost
- Correlation visualization plots 3-axis data correctly

## Error Scenarios

### History Load Failure
- **Trigger**: Network error
- **Sees**: Error toast
- **Recovery**: Refresh page

### No Judge Model Configured
- **Trigger**: Clicks Judge but no judge model set in settings
- **Sees**: "Set one in Settings > Judge" message
- **Recovery**: Configure judge model in Settings

### Correlation Scoring Fails
- **Trigger**: Judge API fails
- **Sees**: Error toast
- **Recovery**: Retry or check judge model configuration

## Maps to E2E Tests
- `e2e/tests/tool-eval/param-tuner-advanced.spec.js` — History: Apply, detail modal, delete
