# Journey: Run a Benchmark

## Tier
critical

## Preconditions
- User is logged in
- At least one provider API key is configured in Settings
- config.yaml has at least one provider with models

## Steps

### 1. Load Benchmark Page
- **Sees**: Model selection grid grouped by provider, configuration panel (max tokens, temperature, runs, context tiers, prompt), "Run Benchmark" button (disabled)
- **Does**: Waits for page to load
- **Backend**: `GET /api/config` — loads providers and models

### 2. Select Models
- **Sees**: Checkboxes per model, grouped by provider with "Select All"/"Select None" toggles
- **Does**: Checks one or more model checkboxes (or uses provider-level toggle)
- **Backend**: None (client-side state)

### 3. Configure Parameters
- **Sees**: Max Tokens slider (64-4096), Temperature slider (0-2), Runs input (1-10), Context Tiers chips (0, 5K, 50K), Prompt textarea with preset dropdown
- **Does**: Adjusts sliders, selects context tiers, enters or selects prompt
- **Backend**: None (client-side state)

### 4. Start Benchmark
- **Sees**: "Run Benchmark" button (now enabled)
- **Does**: Clicks "Run Benchmark"
- **Backend**: `POST /api/benchmark` — submits job, returns `{job_id, status: "submitted"}`
- **WebSocket**: `benchmark_init` — confirms job started, sends target list and run count

### 5. Monitor Progress
- **Sees**: Progress section with per-provider cards, overall progress bar, ETA, completed/total counter
- **Does**: Watches progress (can click "Cancel" to abort)
- **WebSocket**: `benchmark_progress` — per-run progress (model, run #, context tier). `benchmark_result` — individual result (tokens/sec, TTFT, cost, success/error)

### 6. View Results
- **Sees**: Summary stats (Fastest model, Best TTFT, Total Cost), results chart, results table with per-model rows (Tok/s, TTFT, Duration, Status)
- **Does**: Reviews results. For stress test mode (multiple context tiers): sees "Best @ 0K", "Best @ Max Tier", "Tiers Tested", "Failures"
- **Backend**: Results auto-saved to `benchmark_runs` table during job execution

## Success Criteria
- All selected models complete their runs
- Results table shows tokens/sec, TTFT, duration for each model
- Results are persisted in history (visible on History page)
- WebSocket events stream in real-time (no polling)
- Progress bar reaches 100%

## Error Scenarios

### No Models Selected
- **Trigger**: User clicks Run without selecting any model
- **Sees**: Button remains disabled, tooltip "Select at least one model"
- **Recovery**: Select at least one model

### API Key Missing
- **Trigger**: Selected model's provider has no API key configured
- **Sees**: Individual run fails with "401 Unauthorized" or provider-specific auth error in results table
- **Recovery**: Add API key in Settings > API Keys

### All Runs Fail
- **Trigger**: Every run returns an error (bad keys, models unavailable)
- **Sees**: Error banner: "All benchmarks failed. Check your API keys and model configuration." with clickable error list
- **Recovery**: Review error details, fix API keys or model config

### Rate Limit Exceeded
- **Trigger**: User submits more than 20 benchmarks per hour
- **Sees**: HTTP 429 response, toast error
- **Recovery**: Wait for rate limit window to reset (1 hour)

### Config Load Failure
- **Trigger**: Network error on page load
- **Sees**: "Failed to load configuration. Check your connection and refresh." with Retry button
- **Recovery**: Click Retry or refresh page

## Maps to E2E Tests
- `e2e/tests/benchmark/benchmark-cancel.spec.js` — Start + cancel + progress UI
- `e2e/tests/benchmark/stress-test.spec.js` — Multi-tier stress test with results
- `e2e/tests/auth/zai-provider-setup.spec.js` — Single model benchmark after setup
