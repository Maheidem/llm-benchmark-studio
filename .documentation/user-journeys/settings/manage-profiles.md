# Journey: Manage Model Profiles

## Tier
medium

## Preconditions
- User is logged in

## Steps

### 1. Load Profiles Page
- **Sees**: Model-grouped profile cards (or empty state "No profiles yet..."), "+ New Profile" button
- **Backend**: Loads profiles from store

### 2. Create Profile
- **Does**: Clicks "+ New Profile" or "+ Add" under specific model
- **Sees**: Modal with: Model dropdown (required), Name (required), Description (optional), System Prompt (optional textarea), Parameters (key-value pair list with + Add/remove), "Set as default" checkbox
- **Does**: Fills fields, adds parameters, clicks "Save"
- **Backend**: Creates profile with source="manual"
- **Sees**: Profile appears in model's group

### 3. Edit Profile (optional)
- **Does**: Clicks "Edit" on profile row
- **Sees**: Modal pre-filled with all fields
- **Does**: Modifies fields, saves
- **Backend**: Updates profile

### 4. Set Default (optional)
- **Does**: Clicks "Set Default" on a profile
- **Sees**: Badge changes to "DEFAULT", previous default cleared
- **Backend**: Updates default flag

### 5. Delete Profile (optional)
- **Does**: Clicks delete, confirms
- **Backend**: Deletes profile
- **Sees**: Profile removed from list

## Success Criteria
- Profiles grouped by model with origin badges (manual, param_tuner, prompt_tuner, import)
- Create/edit modal validates required fields
- Parameters stored as JSON, displayed as key=value
- One default profile per model
- Profiles usable in Evaluate view's profile picker

## Error Scenarios

### Missing Required Fields
- **Trigger**: Save without model ID or name
- **Sees**: Validation error
- **Recovery**: Fill required fields

### Save/Delete Failure
- **Trigger**: Network error
- **Sees**: Error toast
- **Recovery**: Retry

## Maps to E2E Tests
- `e2e/tests/settings/profiles.spec.js` — Profiles CRUD (NEW)
