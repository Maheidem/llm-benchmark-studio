# Journey: View Prompt Tune History

## Tier
medium

## Preconditions
- User is logged in
- At least one prompt tuning run has completed

## Steps

### 1. Load History
- **Sees**: Run list cards showing: suite name, status badge, mode badge (Quick/Evolutionary), date, meta model, duration, best score (colored), best prompt preview (first 100 chars)
- **Does**: Waits for page to load
- **Backend**: `GET /api/tool-eval/prompt-tune/history` — loads all runs

### 2. View Run Details
- **Does**: Clicks a run card
- **Sees**: Detail modal with generation timeline, best prompt with origin info (generation #, prompt #, style)
- **Backend**: `GET /api/tool-eval/prompt-tune/history/{id}` — loads full run

### 3. Apply Best Prompt
- **Does**: Clicks "Apply" button
- **Sees**: Toast confirming prompt applied to shared context
- **Backend**: None (updates shared context system prompt)

### 4. Save as Profile (optional)
- **Does**: Clicks "Save Profile" → enters name → saves
- **Backend**: Creates model profile with source="prompt_tuner"

### 5. Run Judge (optional)
- **Does**: Clicks "Judge" button
- **Backend**: Submits judge job with tune_type="prompt_tuner"

### 6. Delete Run (optional)
- **Does**: Clicks delete, confirms
- **Backend**: `DELETE /api/tool-eval/prompt-tune/history/{id}`

## Success Criteria
- All past prompt tune runs visible with mode and best score
- Detail modal shows full generation timeline
- Apply sets system prompt in shared context
- Profile saved with source attribution

## Error Scenarios

### History Load Failure
- **Trigger**: Network error
- **Sees**: Error toast
- **Recovery**: Refresh

### No Judge Model
- **Trigger**: Judge button clicked without judge model configured
- **Sees**: "Set one in Settings > Judge"
- **Recovery**: Configure in Settings

## Maps to E2E Tests
- `e2e/tests/tool-eval/prompt-tuner-advanced.spec.js` — History: Apply, detail modal, delete
