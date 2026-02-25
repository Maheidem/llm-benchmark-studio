# Journey: Import a Test Suite

## Tier
high

## Preconditions
- User is logged in
- User has a suite file (JSON, BFCL format) or MCP server configured

## Steps

### 1. Navigate to Suites Tab
- **Sees**: Suite list, import buttons: "Import Suite", "Import BFCL", "Import from MCP"
- **Backend**: `GET /api/tool-suites` — loads existing suites

### 2a. Import Standard JSON
- **Does**: Clicks "Import Suite" → file picker opens
- **Does**: Selects JSON file
- **Backend**: `POST /api/tool-eval/import` — parses and creates suite
- **Sees**: Suite created, auto-navigates to editor. Can also click "Example JSON" to download template first (`GET /api/tool-eval/import/example`)

### 2b. Import BFCL Format
- **Does**: Clicks "Import BFCL" → file picker opens
- **Does**: Selects BFCL JSON file
- **Backend**: `POST /api/tool-eval/import/bfcl` — parses Berkeley Function Calling Leaderboard format
- **Sees**: Suite created from BFCL data

### 2c. Import from MCP Server
- **Does**: Clicks "Import from MCP" → discovery modal opens
- **Does**: Enters MCP server connection details
- **Backend**: `POST /api/mcp/discover` — discovers tools from MCP server
- **Sees**: List of discovered tools with checkboxes
- **Does**: Selects tools to import, clicks Import
- **Backend**: `POST /api/mcp/import` — creates suite from selected MCP tools
- **Sees**: Suite created with MCP tools

### 3. Review Imported Suite
- **Sees**: Editor view with imported tools and test cases
- **Does**: Can modify any imported data (edit tools, add/edit/delete test cases)

## Success Criteria
- Standard JSON import creates suite with correct tools and test cases
- BFCL format correctly maps to internal format
- MCP discovery finds available tools on the server
- Imported suite is immediately editable
- All three import methods produce valid, usable suites

## Error Scenarios

### Invalid JSON Format
- **Trigger**: File is not valid JSON or missing required structure
- **Sees**: Validation error toast
- **Recovery**: Fix JSON format (download example template for reference)

### Missing Required Fields
- **Trigger**: JSON missing "name" field or tools missing function definitions
- **Sees**: Error message indicating what's missing
- **Recovery**: Add required fields to file

### MCP Server Unreachable
- **Trigger**: MCP server not running or connection details wrong
- **Sees**: "Failed to discover tools" error
- **Recovery**: Verify MCP server is running and connection details are correct

### No Tools Discovered (MCP)
- **Trigger**: MCP server has no tools registered
- **Sees**: "No tools found" message
- **Recovery**: Register tools on MCP server first

## Maps to E2E Tests
- `e2e/tests/tool-eval/suite-import.spec.js` — Import JSON, export
- `e2e/tests/tool-eval/bfcl-import.spec.js` — BFCL format import
