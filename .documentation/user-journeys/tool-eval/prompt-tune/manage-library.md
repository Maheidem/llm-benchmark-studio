# Journey: Manage Prompt Library

## Tier
medium

## Preconditions
- User is logged in

## Steps

### 1. Load Prompt Library
- **Sees**: Version list (or empty state), "+ Save New" button
- **Does**: Waits for page to load
- **Backend**: `GET /api/prompt-versions` — loads all saved prompt versions

### 2. Save New Prompt (optional)
- **Does**: Clicks "+ Save New"
- **Sees**: Collapsible form with: Prompt Text textarea, Label input (optional)
- **Does**: Enters prompt text, optional label, clicks Save
- **Backend**: `POST /api/prompt-versions` — creates new version
- **Sees**: New version appears in list

### 3. Load Prompt into Tuner (optional)
- **Does**: Clicks "Load" on a version
- **Sees**: Prompt loaded into Prompt Tuner Config's base prompt field
- **Backend**: None (updates shared context)

### 4. Edit Label (optional)
- **Does**: Clicks version label (or "+ label" button)
- **Sees**: Inline edit mode with input + Save/Cancel
- **Does**: Types new label, clicks Save
- **Backend**: `PATCH /api/prompt-versions/{id}` — updates label

### 5. Compare Versions (optional)
- **Does**: Checks diff checkbox on two versions
- **Sees**: Side-by-side diff panel showing both prompts in two columns
- **Does**: Can click "Clear" to dismiss comparison

### 6. Copy Prompt (optional)
- **Does**: Clicks "Copy" button
- **Sees**: Prompt text copied to clipboard, toast confirmation

### 7. Delete Version (optional)
- **Does**: Clicks delete icon
- **Sees**: Version removed from list
- **Backend**: `DELETE /api/prompt-versions/{id}`

## Success Criteria
- All saved prompt versions listed with version number, label, source, date
- Source badges show origin (manual, tuner, auto_optimize)
- Side-by-side comparison works for any two versions
- Load applies prompt to Prompt Tuner config
- Inline label editing saves immediately
- Copy puts full text on clipboard

## Error Scenarios

### Empty Prompt
- **Trigger**: Save with blank prompt text
- **Sees**: Validation error in form
- **Recovery**: Enter prompt text

### Save Failure
- **Trigger**: Network error
- **Sees**: Error toast
- **Recovery**: Retry save

### Empty Library
- **Trigger**: No prompts saved yet
- **Sees**: "No saved prompts yet" with link to Prompt Library
- **Recovery**: Save a prompt manually or run Prompt Tuner (auto-saves best)

## Maps to E2E Tests
- `e2e/tests/tool-eval/prompt-library.spec.js` — Save, edit labels, diff, delete, copy
