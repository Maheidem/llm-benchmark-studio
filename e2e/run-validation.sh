#!/bin/bash
# Run the 16-scenario platform validation E2E tests.
#
# Prerequisites:
#   - App running at http://localhost:8501
#   - Playwright installed: cd e2e && npm install
#
# Usage:
#   ./e2e/run-validation.sh
#   ./e2e/run-validation.sh --headed     # watch in browser
#   BASE_URL=http://staging:8502 ./e2e/run-validation.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$SCRIPT_DIR"

# Ensure screenshots directory exists
mkdir -p screenshots

echo "=== Platform Validation E2E Tests ==="
echo "Base URL: ${BASE_URL:-http://localhost:8501}"
echo ""

# Run only the platform-validation test file
npx playwright test tests/platform-validation.spec.js --reporter=list "$@"

echo ""
echo "Done. Screenshots saved to: e2e/screenshots/"
ls -la screenshots/*.png 2>/dev/null || echo "(no screenshots generated — tests may have been skipped)"
