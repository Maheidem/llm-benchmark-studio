# Journey: Configure Judge Settings

## Tier
medium

## Preconditions
- User is logged in

## Steps

### 1. Navigate to Judge Settings
- **Sees**: Settings page > Judge tab. "Judge Model Configuration" card with all settings fields
- **Backend**: `GET /api/config` — loads available models. `GET /api/settings/judge` — loads current judge settings

### 2. Set Default Judge Model
- **Sees**: Model dropdown with all available models (formatted: "Model Name (provider)")
- **Does**: Selects a model

### 3. Configure Judge Behavior
- **Sees**: Multiple settings fields:
  - Default Judge Provider Key (text input, e.g., "openai", "anthropic")
  - Default Mode dropdown: "Post-evaluation" or "Live inline"
  - Score Override Policy dropdown: "Always Allow" / "Require Confirmation" / "Never"
  - Auto Judge After Eval checkbox
  - Concurrency input (1-20, default 4)
  - Custom Instructions Template textarea (appended to all judge prompts)
- **Does**: Configures fields as desired

### 4. Auto-Save
- **Sees**: Changes auto-save with 500ms debounce
- **Backend**: `PUT /api/settings/judge` — saves modified settings (partial update, only non-null fields)
- **Sees**: "Saved" message (lime green, disappears after 3 seconds)

## Success Criteria
- All settings load correctly from server on mount
- Changes auto-save after 500ms of no changes (debounced)
- "Saved" confirmation appears in lime green
- Settings persist across sessions
- Default judge model used for auto-judge and new judge runs
- Custom instructions template applied to all judge prompts

## Error Scenarios

### Failed to Load Settings
- **Trigger**: Network error on mount
- **Sees**: Toast "Failed to load settings"
- **Recovery**: Refresh page

### Failed to Save
- **Trigger**: Network error during save
- **Sees**: "Failed to save" message in coral red
- **Recovery**: Retry (auto-save will try again on next change)

### No Models Available
- **Trigger**: No providers configured
- **Sees**: Model selector shows only "-- Select a model --"
- **Recovery**: Configure providers and API keys in Settings first

## Maps to E2E Tests
- `e2e/tests/settings/judge-tuning.spec.js` — Judge panel controls
- `e2e/tests/tool-eval/auto-judge.spec.js` — Auto-judge threshold
