# Journey: View Experiment Timeline

## Tier
low

## Preconditions
- User is logged in
- At least one experiment exists with runs (eval, param tune, prompt tune, or judge)

## Steps

### 1. Navigate to Timeline Tab
- **Sees**: "Experiment Timeline" header. If experiment context exists: timeline loads automatically. If no context: experiment selector dropdown
- **Backend**: `GET /api/experiments` — loads experiment list (if no context)

### 2. Select Experiment (if no context)
- **Sees**: Dropdown listing experiments formatted as "Experiment Name (Suite Name)"
- **Does**: Selects an experiment
- **Backend**: `GET /api/experiments/{id}/timeline` — loads timeline data with baseline score, best score + source, and chronological entries

### 3. View Baseline Info
- **Sees**: Baseline Info card showing: baseline score (color-coded), best score + source (which tuner/judge achieved it)

### 4. Browse Timeline Entries
- **Sees**: Chronological list of entries, each representing a run (eval, param tune, prompt tune, judge) with type, date, key metrics
- **Does**: Clicks an entry to navigate to its relevant page

### 5. Navigate to Run Details
- **Does**: Clicks timeline entry
- **Sees**: Routes to: eval -> Evaluate tab, param_tune -> Param Tuner History, prompt_tune -> Prompt Tuner History, judge -> Judge History

## Success Criteria
- Timeline shows all experiment runs in chronological order
- Baseline and best scores displayed correctly
- Entry types correctly identified (eval, param_tune, prompt_tune, judge)
- Navigation from entry goes to correct detail page
- Auto-loads when experiment context exists from other tabs

## Error Scenarios

### No Experiments
- **Trigger**: No experiments created
- **Sees**: "Select an experiment to view its timeline, or create one from the Evaluate page."
- **Recovery**: Create an experiment from the Evaluate tab

### No Runs in Experiment
- **Trigger**: Experiment exists but has no runs
- **Sees**: "No runs in this experiment yet."
- **Recovery**: Run an evaluation linked to this experiment

### Failed to Load Timeline
- **Trigger**: Network error
- **Sees**: Toast "Failed to load timeline"
- **Recovery**: Refresh or re-select experiment

## Maps to E2E Tests
- `e2e/tests/tool-eval/timeline.spec.js` — Create experiment, pin baseline
- `e2e/tests/tool-eval/timeline-filters.spec.js` — Filter by type, navigate
