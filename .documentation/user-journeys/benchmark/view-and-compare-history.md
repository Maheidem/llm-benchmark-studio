# Journey: View and Compare Benchmark History

## Tier
high

## Preconditions
- User is logged in
- At least one benchmark has been run previously

## Steps

### 1. Load History Page
- **Sees**: List of benchmark run cards in reverse chronological order, each showing timestamp, winning model badge, winner's throughput, context tier badges
- **Does**: Waits for page to load
- **Backend**: `GET /api/history` — loads all user's benchmark runs

### 2. Search History
- **Sees**: Search input at top of page
- **Does**: Types model name or date to filter results
- **Backend**: None (client-side filtering)

### 3. View Run Details
- **Sees**: Run card with summary info
- **Does**: Clicks a run card
- **Sees**: Detail modal with config summary (model count, runs, max tokens, temperature), context tiers badges, prompt preview, full results table (Model, Context, Tok/s, TTFT, Duration, Status)
- **Backend**: `GET /api/history/{run_id}` — loads full run details

### 4. Compare Runs (optional)
- **Sees**: Checkboxes on each run card
- **Does**: Selects 2+ runs via checkboxes. Compare bar appears showing count + "Compare" + "Clear" buttons. Clicks "Compare"
- **Sees**: Cross-run comparison table — models as rows, run timestamps as columns, cells color-coded (green=best, red=worst)
- **Backend**: Parallel `GET /api/history/{id}` for each selected run

### 5. Re-Run (optional)
- **Sees**: "Re-Run" button on run card or in detail modal
- **Does**: Clicks "Re-Run"
- **Sees**: Toast "Settings pre-filled from run. Adjust and submit." Auto-navigates to Benchmark tab with all settings pre-filled
- **Backend**: `GET /api/history/{run_id}` — loads run config for pre-fill

### 6. Delete Run (optional)
- **Sees**: Delete button (trash icon) on run card
- **Does**: Clicks delete, confirms in dialog
- **Sees**: Run removed from list, toast "Run deleted"
- **Backend**: `DELETE /api/history/{run_id}`

## Success Criteria
- All past benchmark runs appear in reverse chronological order
- Search filters work instantly (client-side)
- Detail modal shows complete run configuration and results
- Comparison table correctly shows color-coded side-by-side metrics
- Re-run pre-fills all original settings (models, prompt, tokens, temperature, tiers)
- Delete removes run and updates list

## Error Scenarios

### History Load Failure
- **Trigger**: Network error
- **Sees**: "Failed to load history."
- **Recovery**: Click Refresh button

### Comparison Load Failure
- **Trigger**: Network error during comparison fetch
- **Sees**: "Failed to load comparison data"
- **Recovery**: Try comparing again

### Delete Failure
- **Trigger**: Network error during delete
- **Sees**: "Failed to delete run" toast
- **Recovery**: Retry delete

### Empty History
- **Trigger**: User has no benchmark runs
- **Sees**: "No benchmark history yet. Run your first benchmark..."
- **Recovery**: Go to Benchmark tab and run a benchmark

## Maps to E2E Tests
- `e2e/tests/history/benchmark-history.spec.js` — History view, search, delete
- `e2e/tests/benchmark/benchmark-compare.spec.js` — Compare UI
