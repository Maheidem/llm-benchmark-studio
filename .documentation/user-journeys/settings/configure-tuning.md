# Journey: Configure Tuning Defaults

## Tier
low

## Preconditions
- User is logged in

## Steps

### 1. Load Tuning Settings
- **Sees**: Collapsible sections: Parameter Tuner Defaults, Prompt Tuner Defaults, Provider Parameter Support
- **Backend**: `GET /api/settings/phase10` — loads all settings

### 2. Configure Param Tuner Defaults
- **Sees**: Max Combinations input, Temperature range (Min/Max/Step), Top P range (Min/Max/Step)
- **Does**: Adjusts values
- **Backend**: `PUT /api/settings/phase10` — debounced auto-save (500ms)

### 3. Configure Prompt Tuner Defaults
- **Sees**: Mode dropdown (quick/thorough/exhaustive), Max API Calls, Generations, Population Size
- **Does**: Adjusts values
- **Backend**: Same debounced auto-save

### 4. Initialize Parameter Support (optional)
- **Does**: Clicks "Initialize Parameter Support"
- **Backend**: `POST /api/param-support/seed` — generates defaults from user's providers
- **Sees**: Parameter table populates with: Param name, Enabled checkbox, Type, Min, Max, Step, Default, Delete button

### 5. Edit Parameter Support (optional)
- **Does**: Toggles enabled, edits min/max/step/default, adds new params, deletes params
- **Backend**: `PUT /api/settings/phase10` — debounced auto-save (600ms)
- **Sees**: "Saved" or "Failed to save" message

## Success Criteria
- All settings load and display correctly
- Changes auto-save after debounce
- Parameter support initialization populates sensible defaults per provider
- Custom parameters can be added/removed

## Error Scenarios

### Load Failure
- **Trigger**: Network error
- **Sees**: "Failed to load settings"
- **Recovery**: Refresh

### Save Failure
- **Trigger**: Network error during save
- **Sees**: "Failed to save"
- **Recovery**: Changes will retry on next modification

### Seed Failure
- **Trigger**: No providers configured
- **Sees**: "Failed to seed param support"
- **Recovery**: Configure providers first

## Maps to E2E Tests
- `e2e/tests/settings/settings-advanced.spec.js` — Tuning panel controls
- `e2e/tests/settings/judge-tuning.spec.js` — Judge settings controls
