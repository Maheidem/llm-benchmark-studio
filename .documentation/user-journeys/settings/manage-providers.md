# Journey: Manage Providers and Models

## Tier
medium

## Preconditions
- User is logged in

## Steps

### 1. Load Providers Page
- **Sees**: Provider cards (display name, key, api_base, models list), "+ Add Provider" button
- **Backend**: `GET /api/config` — loads current configuration

### 2. Add Provider (optional)
- **Does**: Clicks "+ Add Provider"
- **Sees**: Form with: provider_key (required), display_name, api_base, api_key_env, model_id_prefix
- **Does**: Fills fields, submits
- **Backend**: `POST /api/config/provider` — adds provider to config

### 3. Discover Models (optional)
- **Does**: Clicks "Fetch Models" on a provider card
- **Backend**: `GET /api/models/discover?provider_key={key}` — discovers models from provider API
- **Sees**: Dialog listing discovered models with checkboxes
- **Does**: Selects models, clicks Import
- **Backend**: `POST /api/config/model` — adds each selected model
- **Sees**: Toast "Added N model(s)", config refreshes

### 4. Remove Model (optional)
- **Does**: Clicks "Remove Model" on a model in the provider card, confirms
- **Backend**: `DELETE /api/config/model` — removes model

### 5. Delete Provider (optional)
- **Does**: Clicks "Delete Provider", confirms
- **Backend**: `DELETE /api/config/provider` — removes provider and all its models

## Success Criteria
- All providers displayed with their models
- New providers added to config.yaml
- Model discovery works for supported providers (LM Studio, etc.)
- Discovered models can be selectively imported
- Delete cascades (provider deletion removes all its models)

## Error Scenarios

### Provider Key Required
- **Trigger**: Submit without provider_key
- **Sees**: Validation error
- **Recovery**: Enter provider key

### Discovery Fails
- **Trigger**: Provider API unreachable
- **Sees**: "Failed to fetch models: {error}"
- **Recovery**: Check provider is running and API key is set

### No Models Found
- **Trigger**: Provider has no discoverable models
- **Sees**: "No models found"
- **Recovery**: Ensure models are loaded/available on the provider

## Maps to E2E Tests
- `e2e/tests/settings/provider-crud.spec.js` — Create, fetch models, verify, delete
