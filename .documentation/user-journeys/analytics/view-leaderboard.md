# Journey: View Model Leaderboard

## Tier
medium

## Preconditions
- User is logged in
- At least one benchmark or tool eval has been run

## Steps

### 1. Load Leaderboard
- **Sees**: Type toggle (Benchmark / Tool Eval), Period filter (7d, 30d, 90d, All time), ranked table
- **Does**: Waits for page to load (defaults to Benchmark type, 30d period)
- **Backend**: `GET /api/analytics/leaderboard?type=benchmark&period=30d` — loads ranked model data

### 2. Switch Type (optional)
- **Does**: Clicks "Tool Eval" toggle
- **Sees**: Table refreshes with tool eval metrics (avg tool %, param %, overall %, total evals)
- **Backend**: `GET /api/analytics/leaderboard?type=tool_eval&period=30d`

### 3. Change Period (optional)
- **Does**: Selects different period from dropdown
- **Sees**: Table refreshes with filtered data
- **Backend**: `GET /api/analytics/leaderboard?type={type}&period={period}`

### 4. Sort Columns
- **Does**: Clicks column header
- **Sees**: Table re-sorts ascending/descending on clicked column

## Success Criteria
- Benchmark leaderboard shows: model, provider, avg TPS, avg TTFT, avg cost, total runs, last run date
- Tool eval leaderboard shows: model, provider, avg tool %, param %, overall %, total evals, last eval date
- Sorting works on all columns
- Period filter correctly limits time range

## Error Scenarios

### No Data for Period
- **Trigger**: No runs in selected time range
- **Sees**: Empty table
- **Recovery**: Select broader time period

### Invalid Period/Type
- **Trigger**: API parameter validation
- **Sees**: HTTP 400 error
- **Recovery**: Use valid filter values

## Maps to E2E Tests
- `e2e/tests/analytics/leaderboard.spec.js` — Leaderboard with benchmark data
- `e2e/tests/analytics/analytics-full.spec.js` — Type toggle, period filter
- `e2e/tests/tool-eval/sprint11-2d-leaderboard.spec.js` — Public leaderboard, opt-in
