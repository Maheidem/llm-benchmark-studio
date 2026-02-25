# Journey: Compare Benchmark Runs

## Tier
medium

## Preconditions
- User is logged in
- At least 2 benchmark runs exist

## Steps

### 1. Load Compare View
- **Sees**: Run selection list (max 20 recent runs), checkboxes per run, "Compare" button (disabled)
- **Backend**: `GET /api/history` — loads recent benchmark runs

### 2. Select Runs
- **Does**: Checks 2-4 runs (each showing tooltip with timestamp, prompt snippet, model count)
- **Sees**: "Compare" button becomes enabled

### 3. View Comparison
- **Does**: Clicks "Compare"
- **Sees**: CompareCharts component with side-by-side metrics
- **Backend**: `GET /api/analytics/compare?runs={id1,id2,...}` — loads comparison data. Fallback: individual `GET /api/history/{id}` calls if primary fails

## Success Criteria
- Can select 2-4 runs for comparison
- Comparison shows side-by-side metrics (TPS, TTFT, cost) per model
- Charts render correctly with multiple run data
- Fallback to individual fetches works if bulk endpoint fails

## Error Scenarios

### Too Few/Many Selections
- **Trigger**: Less than 2 or more than 4 runs checked
- **Sees**: Toast message about selection limits
- **Recovery**: Adjust selection count

### No Runs Available
- **Trigger**: No benchmark runs exist
- **Sees**: "No benchmark runs found"
- **Recovery**: Run benchmarks first

### Comparison Load Fails
- **Trigger**: Network error
- **Sees**: "Failed to load comparison data"
- **Recovery**: Retry

## Maps to E2E Tests
- `e2e/tests/analytics/analytics-full.spec.js` — Compare tab navigation
- `e2e/tests/analytics/analytics-compare-trends.spec.js` — Compare with data (NEW)
