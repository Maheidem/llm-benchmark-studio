# Journey: Create a Test Suite

## Tier
critical

## Preconditions
- User is logged in

## Steps

### 1. Navigate to Suites Tab
- **Sees**: Suite list table (or empty state), "New Suite" button, import buttons
- **Does**: Clicks "New Suite"
- **Backend**: `GET /api/tool-suites` — loads existing suites. `POST /api/tool-suites` — creates blank suite
- **Sees**: Auto-navigates to suite editor

### 2. Edit Suite Metadata
- **Sees**: Left panel with Suite Name and Description text inputs
- **Does**: Types suite name and description (auto-saves on blur)
- **Backend**: `PUT /api/tool-suites/{suite_id}` — saves name/description

### 3. Add Tools
- **Sees**: Middle panel "Tools" section with "Add Tool" button
- **Does**: Clicks "Add Tool", inline JSON editor opens
- **Does**: Enters tool definition JSON (must have `type: "function"` and `function.name`)
- **Does**: Clicks Save
- **Backend**: `PUT /api/tool-suites/{suite_id}` — saves tools array
- **Sees**: Tool appears in tools list with name badge

### 4. Add Test Cases
- **Sees**: Right panel "Test Cases" section with "Add Case" button
- **Does**: Clicks "Add Case", TestCaseForm opens with fields:
  - User Message (textarea) — the prompt to test
  - "Model should call a tool" checkbox (for irrelevance detection)
  - Expected Tool (text input, comma-separated for alternatives)
  - Expected Parameters (JSON textarea)
  - Category (text input with autocomplete: retrieval, math, calendar, nested, multi-step, filtering, creation)
  - Scoring Mode (select: exact, subset, numeric_tolerance, case_insensitive)
  - Multi-Turn toggle (enables: Max Rounds, Optimal Hops, Valid Prerequisites, Mock Responses, Argument Source)
- **Does**: Fills fields and clicks Save
- **Backend**: `POST /api/tool-suites/{suite_id}/cases` — creates test case
- **Sees**: Test case appears in list

### 5. Review Suite
- **Sees**: Complete suite with tools and test cases listed
- **Does**: Can edit/delete any tool or test case inline

## Success Criteria
- Suite is created with a unique ID
- Name and description auto-save on blur
- Tools validate JSON format (type=function, function.name required)
- Test cases save with all fields (message, expected tool, expected params, scoring mode)
- Suite appears in Suites list on return
- Suite is available for evaluation in the Evaluate tab

## Error Scenarios

### Invalid Tool JSON
- **Trigger**: Malformed JSON or missing required fields
- **Sees**: Inline error message "Invalid JSON" or "Tool must have type=function and function.name"
- **Recovery**: Fix JSON and re-save

### Empty User Message
- **Trigger**: Test case submitted without user message
- **Sees**: Validation error on form
- **Recovery**: Enter a user message

### Suite Name Empty
- **Trigger**: Blur on empty name field
- **Sees**: Error toast, name reverts to previous value
- **Recovery**: Enter a valid name

### Delete Confirmation
- **Trigger**: User clicks delete on tool or test case
- **Sees**: Confirmation dialog
- **Recovery**: Confirm or cancel

## Maps to E2E Tests
- `e2e/tests/tool-eval/suite-crud.spec.js` — Create, edit, add tool/case, delete
- `e2e/tests/tool-eval/editor-advanced.spec.js` — Auto-save, scoring modes, multi-turn
