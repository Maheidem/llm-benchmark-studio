# Journey: Opt In/Out of Public Leaderboard

## Tier
low

## Preconditions
- User is logged in

## Steps

### 1. Load Leaderboard Settings
- **Sees**: "Contribute to Public Leaderboard" card with toggle switch, "What's included" section (green checks: Model name, Tool accuracy %, Param accuracy %, Throughput; red X: Test case prompts, API keys), "View Public Leaderboard" link
- **Backend**: `GET /api/leaderboard/opt-in` — gets current opt-in status

### 2. Toggle Opt-In
- **Does**: Clicks toggle switch
- **Sees**: Toggle updates immediately (optimistic)
- **Backend**: `PUT /api/leaderboard/opt-in` — saves new preference
- **Sees**: "Saved!" message (lime green, disappears after 2 seconds)

### 3. View Public Leaderboard (optional)
- **Does**: Clicks "View Public Leaderboard" link
- **Sees**: Opens public leaderboard page in new tab (accessible without auth)

## Success Criteria
- Toggle reflects current opt-in status
- Optimistic update: switch moves immediately before API response
- If opt-in: anonymized results included in public leaderboard
- Privacy-preserving: only metrics shared, never prompts or API keys
- Public leaderboard accessible at /leaderboard without auth

## Error Scenarios

### Save Fails
- **Trigger**: Network error
- **Sees**: Toggle reverts, "Failed to save" message
- **Recovery**: Toggle again to retry

## Maps to E2E Tests
- `e2e/tests/tool-eval/sprint11-2d-leaderboard.spec.js` — Opt-in toggle + public leaderboard
