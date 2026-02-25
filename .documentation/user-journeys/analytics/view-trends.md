# Journey: View Performance Trends

## Tier
medium

## Preconditions
- User is logged in
- Multiple benchmark runs exist over time for trend data

## Steps

### 1. Load Trends View
- **Sees**: Model selector dropdown (multi-select, up to 60 models), Period filter (7d, 30d, 90d, All time)
- **Backend**: `GET /api/analytics/leaderboard?type=benchmark&period=all` — loads available models

### 2. Select Models
- **Does**: Opens model dropdown, toggles checkboxes for desired models
- **Sees**: Charts auto-load when 1+ model selected
- **Backend**: `GET /api/analytics/trends?models={m1,m2}&metric=tps&period=30d` — loads TPS trend data

### 3. Change Period (optional)
- **Does**: Selects different period
- **Sees**: Charts refresh with new time range
- **Backend**: Same endpoint with updated period parameter

### 4. View Charts
- **Sees**: TrendsCharts component showing TPS and TTFT time-series per selected model

## Success Criteria
- Model selector lists all models that have benchmark data
- Time-series charts show data points over time for selected models
- Multiple models can be overlaid on same chart
- Period filter correctly limits time range

## Error Scenarios

### No Models Found
- **Trigger**: No benchmarks run yet
- **Sees**: "No models found"
- **Recovery**: Run benchmarks first

### No Trend Data
- **Trigger**: Models selected but no data in time range
- **Sees**: Empty charts
- **Recovery**: Select broader time period

### Failed to Load Trends
- **Trigger**: Network error
- **Sees**: "Failed to load trends"
- **Recovery**: Retry

## Maps to E2E Tests
- `e2e/tests/analytics/analytics-full.spec.js` — Trends tab navigation
- `e2e/tests/analytics/analytics-compare-trends.spec.js` — Trends with data (NEW)
