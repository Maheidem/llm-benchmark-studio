# Journey: View Evaluation History

## Tier
high

## Preconditions
- User is logged in
- At least one evaluation has been run

## Steps

### 1. Load History Tab
- **Sees**: History table with columns: Date, Suite, Models (badges), Overall Score, Judge Grade, Actions. Refresh button, search box, Export CSV button
- **Does**: Waits for page to load
- **Backend**: `GET /api/tool-eval/history` — loads all eval runs

### 2. Search and Filter
- **Sees**: Search input
- **Does**: Types to filter by date, suite name, or model names
- **Backend**: None (client-side filtering)

### 3. View Run Details
- **Does**: Clicks a row
- **Sees**: Detail modal with: Run summary cards (Suite, Model count, Overall score, Judge grade), Model Scores table (per-model: Tool Selection %, Param Accuracy %, Overall %, Case count), Test Cases table (per-case: Model, Prompt, Expected, Actual, Score)
- **Backend**: `GET /api/tool-eval/history/{eval_id}` — loads full run with results

### 4. Re-Run (optional)
- **Does**: Clicks Re-run button in modal header
- **Sees**: Shared context updated with run's config, navigates to Evaluate tab with settings pre-filled
- **Backend**: Loads run config for pre-fill

### 5. Export CSV (optional)
- **Does**: Clicks "Export CSV" button
- **Backend**: `GET /api/export/tool-eval?format=csv` — downloads CSV file

### 6. Delete Run (optional)
- **Does**: Clicks delete icon, confirms
- **Backend**: `DELETE /api/tool-eval/history/{eval_id}`
- **Sees**: Run removed from list

## Success Criteria
- All past eval runs visible in reverse chronological order
- Detail modal shows complete per-model and per-case results
- Search filters work in real-time
- Re-run pre-fills all original settings
- CSV export includes all result data
- Delete removes run permanently

## Error Scenarios

### History Load Failure
- **Trigger**: Network error
- **Sees**: Error message
- **Recovery**: Click Refresh

### Empty History
- **Trigger**: No evaluations run yet
- **Sees**: Empty state message
- **Recovery**: Run an evaluation first

### Delete Failure
- **Trigger**: Network error
- **Sees**: Error toast
- **Recovery**: Retry delete

## Maps to E2E Tests
- `e2e/tests/tool-eval/evaluate-details.spec.js` — Result detail drill-down
- `e2e/tests/tool-eval/evaluate-history.spec.js` — History interactions (NEW)
