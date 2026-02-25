# Journey: Manage API Keys

## Tier
critical

## Preconditions
- User is logged in

## Steps

### 1. Load API Keys Page
- **Sees**: "My API Keys" header, note "Your keys are encrypted and only used for your benchmarks", "+ Custom Key" button, provider list with status badges
- **Backend**: `GET /api/keys` — loads all provider keys with status (YOUR KEY / SHARED / NOT SET / STANDALONE)

### 2. Set Provider Key
- **Does**: Clicks "Set Key" (or "Update") on a provider
- **Sees**: Modal with password input field
- **Does**: Enters API key (masked), clicks "Save Key"
- **Backend**: `PUT /api/keys` — encrypts and stores key with `{provider_key, value}`
- **Sees**: Toast "Key saved", status badge changes to "YOUR KEY" (lime)

### 3. Add Custom Provider Key (optional)
- **Does**: Clicks "+ Custom Key"
- **Sees**: Multi-field modal: Provider Key (required), Key Name (optional), API Key Value (required, password field)
- **Does**: Fills fields, clicks "Save Key"
- **Backend**: `PUT /api/keys` — stores with `{provider_key, value, key_name}`
- **Sees**: New provider appears in list with "STANDALONE" badge

### 4. Remove Key (optional)
- **Does**: Clicks "Remove" on provider with user key
- **Sees**: Confirmation dialog
- **Does**: Confirms
- **Backend**: `DELETE /api/keys` — removes user key, falls back to shared key
- **Sees**: Status changes to "SHARED" (if global key exists) or "NOT SET" (if no global key)

## Success Criteria
- All configured providers shown with correct status badges
- Keys stored encrypted (Fernet) — never visible in plaintext after save
- Setting a key immediately enables that provider for benchmarks
- Removing user key falls back to shared/global key
- Custom keys create standalone provider entries

## Error Scenarios

### Save Fails
- **Trigger**: Network error
- **Sees**: "Failed to save key" toast
- **Recovery**: Retry

### Remove Fails
- **Trigger**: Network error
- **Sees**: "Failed to remove key" toast
- **Recovery**: Retry

### Load Fails
- **Trigger**: Network error on mount
- **Sees**: "Failed to load API keys"
- **Recovery**: Refresh page

## Maps to E2E Tests
- `e2e/tests/settings/api-keys.spec.js` — Set, update, remove API keys
