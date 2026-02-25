# Journey: View and Manage Judge Reports

## Tier
high

## Preconditions
- User is logged in
- At least one judge assessment has been run (auto-triggered after eval, or manually triggered)

## Steps

### 1. Load Judge Reports
- **Sees**: "Judge Reports" header, Refresh button, Compare button, reports table (Date, Mode, Judge Model, Grade, Score, Version, Status, Actions)
- **Does**: Waits for page to load
- **Backend**: `GET /api/tool-eval/judge/reports` — loads all reports sorted by date descending

### 2. View Report Detail
- **Does**: Clicks a report row
- **Sees**: Detail modal with: Version History (if versioned — version chain with dates, click to load any version). JudgeReportView showing: verdicts per test case, cross-case analysis, strengths/weaknesses, recommendations, overall grade (A-F colored) and score (0-100)
- **Backend**: `GET /api/tool-eval/judge/reports/{id}` — loads full report. `GET /api/tool-eval/judge/reports/{id}/versions` — loads version chain

### 3. Compare Two Reports
- **Does**: Clicks "Compare" button (toggle mode), checkboxes appear
- **Does**: Selects exactly 2 reports
- **Sees**: "Compare (2/2)" button enabled (lime green)
- **Does**: Clicks "Compare (2/2)"
- **Sees**: Navigates to JudgeCompare page with side-by-side report view — differences in grades, scores, per-case verdicts highlighted
- **Backend**: `GET /api/tool-eval/judge/reports/{a}` and `GET /api/tool-eval/judge/reports/{b}` — loaded in parallel

### 4. Re-Run with Different Settings
- **Does**: Clicks re-run icon on a report row
- **Sees**: "Re-run Judge" modal with: Judge Model dropdown (with "Use parent model" option), Custom Instructions textarea (pre-filled from parent)
- **Does**: Optionally changes model or instructions, clicks "Re-run"
- **Backend**: `POST /api/tool-eval/judge/rerun` — submits re-run with parent_report_id, returns `{job_id, version: N}`
- **Sees**: Toast "Re-run submitted!", new version created linked to root report
- **WebSocket**: `judge_start`, `judge_verdict`, `judge_report`, `judge_complete` — real-time progress

### 5. Monitor Running Judge
- **Sees**: Running indicator card at top of page (pulse dot, progress bar, percentage, detail text)
- **WebSocket**: `job_progress` — progress updates. `job_completed` / `job_failed` — terminal states

### 6. Delete Report
- **Does**: Clicks delete icon on report row, confirms in dialog
- **Backend**: `DELETE /api/tool-eval/judge/reports/{id}` — cascade deletes versions
- **Sees**: Report removed from list, toast "Report deleted"

## Success Criteria
- All judge reports visible with grade (A-F), score (0-100), status
- Grade color-coded: A=lime, B=blue, C=yellow, D=orange, F=red
- Detail modal shows complete verdict analysis
- Version history chains correctly (re-runs linked to root)
- Compare view highlights differences between two reports
- Re-run creates new version (not duplicate)
- Running indicator shows real-time progress

## Error Scenarios

### No Reports
- **Trigger**: No judge assessments run yet
- **Sees**: "No judge reports yet. Run a judge assessment from the eval history."
- **Recovery**: Run an evaluation, then judge it

### Failed to Load Report
- **Trigger**: Network error
- **Sees**: Toast "Failed to load reports"
- **Recovery**: Click Refresh

### Failed to Load Version
- **Trigger**: Network error when clicking version in version history
- **Sees**: Toast "Failed to load version"
- **Recovery**: Retry click

### Compare Missing IDs
- **Trigger**: Navigate to compare page without two report IDs
- **Sees**: Toast "Two report IDs required for comparison"
- **Recovery**: Select reports from Judge History

### Re-run Fails
- **Trigger**: Judge model unavailable or API key missing
- **Sees**: Job fails, toast "Judge failed"
- **Recovery**: Check judge model configuration in Settings

## Maps to E2E Tests
- `e2e/tests/tool-eval/judge-reports.spec.js` — Reports list, compare mode
- `e2e/tests/tool-eval/judge-details.spec.js` — Detail modal, verdicts
