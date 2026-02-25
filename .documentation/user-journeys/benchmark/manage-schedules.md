# Journey: Manage Scheduled Benchmarks

## Tier
medium

## Preconditions
- User is logged in
- At least one provider API key is configured
- config.yaml has providers with models

## Steps

### 1. Load Schedules Page
- **Sees**: "Schedules" header, "New Schedule" button, table of existing schedules (or empty state)
- **Does**: Waits for page to load
- **Backend**: `GET /api/schedules` — lists all user's schedules

### 2. Create New Schedule
- **Sees**: "New Schedule" button (lime green)
- **Does**: Clicks "New Schedule"
- **Sees**: Modal with: Name input, Prompt textarea, Interval dropdown (1h/6h/12h/24h/1w), Max Tokens input, Temperature input, Model selection tree (grouped by provider with All/Clear toggles)
- **Does**: Fills name, prompt, selects interval, configures tokens/temp, selects models
- **Does**: Clicks "Create Schedule"
- **Backend**: `POST /api/schedules` — creates schedule
- **Sees**: Toast "Schedule created", modal closes, table refreshes with new schedule

### 3. Toggle Schedule Status
- **Sees**: Toggle switch per schedule row (enabled/disabled)
- **Does**: Clicks toggle
- **Backend**: `PUT /api/schedules/{id}` with `{enabled: true/false}`
- **Sees**: Toast "Schedule enabled" or "Schedule paused"

### 4. Trigger Manual Run
- **Sees**: "Run Now" button per schedule row
- **Does**: Clicks "Run Now"
- **Backend**: `POST /api/schedules/{id}/trigger` — runs benchmark immediately in background
- **Sees**: Toast "Schedule triggered - benchmark starting"

### 5. Delete Schedule
- **Sees**: "Del" button per schedule row
- **Does**: Clicks "Del", confirms in dialog
- **Backend**: `DELETE /api/schedules/{id}`
- **Sees**: Toast "Schedule deleted", row removed

## Success Criteria
- Schedule appears in table with correct name, interval, model count
- Last Run and Next Run timestamps update correctly
- Toggle enables/disables schedule (backend respects the flag)
- Manual trigger starts a real benchmark run
- Background scheduler picks up due schedules every 60 seconds
- Results saved with metadata `{source: "schedule", schedule_id, schedule_name}`

## Error Scenarios

### Schedule Creation Fails
- **Trigger**: Missing required fields or API error
- **Sees**: "Failed to save schedule" toast
- **Recovery**: Fix validation errors and retry

### No Models Available
- **Trigger**: No providers configured
- **Sees**: Empty model selection tree
- **Recovery**: Configure providers and API keys in Settings first

### Trigger Fails
- **Trigger**: API key expired or provider down
- **Sees**: Benchmark run fails (visible in History)
- **Recovery**: Check API keys, schedule still updates next_run timestamp

## Maps to E2E Tests
- `e2e/tests/schedules/schedule-crud.spec.js` — Create, toggle, delete schedules
