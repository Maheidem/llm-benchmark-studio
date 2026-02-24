/**
 * @regression Tool Eval — Editor Advanced Features E2E Test
 *
 * User journeys for suite editor advanced features:
 *   1. Create a suite and navigate to editor
 *   2. Verify suite name + description auto-save on blur
 *   3. Add test case with scoring mode (subset)
 *   4. Add test case with multi-turn settings
 *   5. Verify JSON validation on expected params (bad JSON → error)
 *   6. Toggle "No tool expected" checkbox
 *   7. Edit an existing test case
 *
 * Self-contained: registers its own user.
 * No LLM calls — purely UI interaction.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-editor-adv');

test.describe('@regression Tool Eval — Editor Advanced', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(90_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Dismiss onboarding if visible
    const skipBtn = page.getByRole('button', { name: 'Skip All' });
    if (await skipBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await skipBtn.click();
      await expect(page.getByText('Welcome to Benchmark Studio!')).not.toBeVisible({
        timeout: TIMEOUT.modal,
      });
    }
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── CREATE SUITE AND NAVIGATE TO EDITOR ────────────────────────────

  test('Step 1: Create suite and land in editor', async () => {
    // Navigate to Tool Eval > Suites
    await page.getByRole('link', { name: 'Tool Eval' }).click();
    await page.waitForURL('**/tool-eval/**', { timeout: TIMEOUT.nav });

    await page.locator('.te-subtab').filter({ hasText: 'Suites' }).click();
    await page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });

    // Click "New Suite"
    await page.locator('button.run-btn').filter({ hasText: 'New Suite' }).click();

    // Should redirect to editor URL /tool-eval/suites/:id
    await page.waitForURL(/\/tool-eval\/suites\/[a-f0-9]+/, { timeout: TIMEOUT.nav });

    // Verify editor loaded — suite name input visible
    await expect(page.locator('input[placeholder="Suite Name"]')).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── SUITE NAME AND DESCRIPTION AUTO-SAVE ──────────────────────────

  test('Step 2: Edit suite name and description with auto-save on blur', async () => {
    const nameInput = page.locator('input[placeholder="Suite Name"]');
    const descInput = page.locator('input[placeholder="Optional description"]');

    // Type a suite name
    await nameInput.fill('Advanced Editor Suite');
    await nameInput.blur();

    // Brief wait for auto-save
    await page.waitForTimeout(1_000);

    // Type a description
    await descInput.fill('Testing editor advanced features');
    await descInput.blur();

    await page.waitForTimeout(1_000);

    // Reload and verify persisted
    await page.reload();
    await page.waitForLoadState('networkidle');

    await expect(page.locator('input[placeholder="Suite Name"]')).toHaveValue('Advanced Editor Suite');
    await expect(page.locator('input[placeholder="Optional description"]')).toHaveValue('Testing editor advanced features');
  });

  // ─── ADD TOOL ───────────────────────────────────────────────────────

  test('Step 3: Add a tool via JSON editor', async () => {
    // Click "+ Add Tool"
    await page.getByText('+ Add Tool').click();

    // Tool JSON editor should appear
    const textarea = page.locator('textarea').first();
    await expect(textarea).toBeVisible({ timeout: TIMEOUT.modal });

    // Fill with get_weather tool JSON
    await textarea.fill(JSON.stringify({
      type: 'function',
      function: {
        name: 'get_weather',
        description: 'Get weather for a city',
        parameters: {
          type: 'object',
          properties: { city: { type: 'string' } },
          required: ['city'],
        },
      },
    }, null, 2));

    // Save
    await page.locator('.modal-btn-confirm').filter({ hasText: 'Save Tool' }).click();

    // Tool should appear in the tools list
    await expect(page.getByText('get_weather')).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── ADD TEST CASE WITH SUBSET SCORING ──────────────────────────────

  test('Step 4: Add test case with subset scoring mode', async () => {
    // Click "+ Add Test Case"
    await page.getByText('+ Add Test Case').click();

    // Fill prompt
    const promptTextarea = page.locator('textarea').filter({ has: page.locator('[placeholder="Enter the user prompt..."]') });
    // Use more robust selector - the prompt textarea in the TestCaseForm
    const testCaseForm = page.locator('.card').filter({ hasText: 'New Test Case' });
    await testCaseForm.locator('textarea').first().fill('What is the weather in Paris and London?');

    // Fill expected tool
    await testCaseForm.locator('input[placeholder="tool_name (comma-separated for alternatives)"]').fill('get_weather');

    // Fill expected params
    await testCaseForm.locator('textarea').last().fill('{"city": "Paris"}');

    // Change scoring mode to Subset
    await testCaseForm.locator('select').selectOption('subset');

    // Save
    await testCaseForm.locator('.modal-btn-confirm').filter({ hasText: 'Save' }).click();

    // Test case should appear in the list
    await expect(page.getByText('What is the weather in Paris and London?')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── ADD TEST CASE WITH MULTI-TURN ──────────────────────────────────

  test('Step 5: Add test case with multi-turn settings', async () => {
    // Click "+ Add Test Case" again
    await page.getByText('+ Add Test Case').click();

    const testCaseForm = page.locator('.card').filter({ hasText: 'New Test Case' });

    // Fill prompt
    await testCaseForm.locator('textarea').first().fill('Book a flight after checking weather');

    // Fill expected tool
    await testCaseForm.locator('input[placeholder="tool_name (comma-separated for alternatives)"]').fill('get_weather');

    // Fill expected params
    await testCaseForm.locator('textarea').nth(1).fill('{"city": "Tokyo"}');

    // Enable multi-turn
    const multiTurnCheckbox = testCaseForm.locator('input[type="checkbox"]').filter({
      has: page.locator('xpath=./following-sibling::*|./parent::*'),
    });
    // Use the label text to find the multi-turn toggle
    await testCaseForm.getByText('Multi-Turn').click();

    // Multi-turn settings should appear
    await expect(testCaseForm.getByText('Max Rounds')).toBeVisible({ timeout: TIMEOUT.modal });
    await expect(testCaseForm.getByText('Optimal Hops')).toBeVisible();

    // Set max rounds to 3
    const maxRoundsInput = testCaseForm.locator('input[type="number"]').first();
    await maxRoundsInput.fill('3');

    // Set optimal hops to 2
    const optimalHopsInput = testCaseForm.locator('input[type="number"]').nth(1);
    await optimalHopsInput.fill('2');

    // Fill prerequisites
    await testCaseForm.locator('input[placeholder="tool_a, tool_b"]').fill('get_weather');

    // Save
    await testCaseForm.locator('.modal-btn-confirm').filter({ hasText: 'Save' }).click();

    // Test case should appear
    await expect(page.getByText('Book a flight after checking weather')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── JSON VALIDATION ERROR ──────────────────────────────────────────

  test('Step 6: Bad JSON in expected params shows validation error', async () => {
    await page.getByText('+ Add Test Case').click();

    const testCaseForm = page.locator('.card').filter({ hasText: 'New Test Case' });

    // Fill prompt
    await testCaseForm.locator('textarea').first().fill('Test bad JSON');

    // Fill expected tool
    await testCaseForm.locator('input[placeholder="tool_name (comma-separated for alternatives)"]').fill('get_weather');

    // Fill INVALID JSON in expected params
    await testCaseForm.locator('textarea').nth(1).fill('{bad json!!!}');

    // Try to save
    await testCaseForm.locator('.modal-btn-confirm').filter({ hasText: 'Save' }).click();

    // Error should appear about invalid JSON
    await expect(testCaseForm.getByText(/Invalid JSON/i)).toBeVisible({ timeout: TIMEOUT.modal });

    // Cancel the form
    await testCaseForm.locator('.modal-btn-cancel').filter({ hasText: 'Cancel' }).click();
  });

  // ─── NO TOOL EXPECTED TOGGLE ────────────────────────────────────────

  test('Step 7: Toggle "No tool expected" disables expected tool input', async () => {
    await page.getByText('+ Add Test Case').click();

    const testCaseForm = page.locator('.card').filter({ hasText: 'New Test Case' });

    // Expected tool input should be enabled initially
    const toolInput = testCaseForm.locator('input[placeholder="tool_name (comma-separated for alternatives)"]');
    await expect(toolInput).toBeEnabled();

    // Check "No tool expected"
    await testCaseForm.getByText('No tool expected').click();

    // Expected tool input should now be disabled
    await expect(toolInput).toBeDisabled();

    // Cancel
    await testCaseForm.locator('.modal-btn-cancel').filter({ hasText: 'Cancel' }).click();
  });
});
