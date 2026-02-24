/**
 * @smoke Tool Eval Suite CRUD Test Suite
 *
 * Deterministic browser tests for suite lifecycle operations:
 * create suite, edit name, add tool, add/edit/delete test case,
 * verify suite in list, delete suite.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * No LLM calls required — pure UI CRUD.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { confirmDangerModal } = require('../../helpers/modals');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-suite-crud');

const TOOL_JSON = JSON.stringify({
  type: 'function',
  function: {
    name: 'get_weather',
    description: 'Get current weather for a city',
    parameters: {
      type: 'object',
      properties: {
        city: { type: 'string', description: 'City name' },
      },
      required: ['city'],
    },
  },
}, null, 2);

test.describe('@smoke Tool Eval Suite CRUD', () => {
  test.describe.configure({ mode: 'serial' });

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register a fresh user
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Navigate to Tool Eval > Suites
    await page.locator('a.tab', { hasText: 'Tool Eval' }).click();
    await page.waitForURL('**/tool-eval**', { timeout: TIMEOUT.nav });
    await page.locator('.te-subtab', { hasText: 'Suites' }).click();
    await page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── STEP 1: CREATE SUITE ──────────────────────────────────────────────

  test('Step 1: Click "New Suite" → redirects to editor URL', async () => {
    await page.locator('button.run-btn', { hasText: 'New Suite' }).click();
    await page.waitForURL(/\/tool-eval\/suites\/[a-zA-Z0-9]+/, { timeout: TIMEOUT.nav });
    await expect(page).toHaveURL(/\/tool-eval\/suites\/[a-zA-Z0-9]+/);
  });

  // ─── STEP 2: EDIT SUITE NAME ──────────────────────────────────────────

  test('Step 2: Edit suite name and auto-save on blur', async () => {
    const nameInput = page.locator('input[placeholder="Suite Name"]');
    await nameInput.waitFor({ state: 'visible', timeout: TIMEOUT.nav });
    await nameInput.fill('E2E Test Suite');
    // Trigger blur to auto-save by pressing Tab
    await nameInput.press('Tab');
    // Allow time for the save request to complete
    await page.waitForTimeout(1000);
    // Verify the name persisted in the input
    await expect(nameInput).toHaveValue('E2E Test Suite');
  });

  // ─── STEP 3: ADD TOOL ─────────────────────────────────────────────────

  test('Step 3: Add tool via inline editor', async () => {
    // Click "+ Add Tool" button in the tools panel
    await page.locator('button', { hasText: '+ Add Tool' }).click();

    // Wait for the inline textarea to appear
    const textarea = page.locator('textarea').first();
    await textarea.waitFor({ state: 'visible', timeout: TIMEOUT.modal });

    // Clear default template and paste our tool JSON
    await textarea.fill(TOOL_JSON);

    // Click "Save Tool" button
    await page.locator('button.modal-btn-confirm', { hasText: 'Save Tool' }).click();

    // Verify the tool name appears in the tools list
    await expect(page.locator('span.font-mono', { hasText: 'get_weather' })).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── STEP 4: ADD TEST CASE ────────────────────────────────────────────

  test('Step 4: Add test case with prompt and expected tool', async () => {
    // Click "+ Add Test Case" button in the test cases panel
    await page.locator('button', { hasText: '+ Add Test Case' }).click();

    // Wait for the test case form to appear
    const promptTextarea = page.locator('textarea[placeholder="Enter the user prompt..."]');
    await promptTextarea.waitFor({ state: 'visible', timeout: TIMEOUT.modal });

    // Fill in the prompt
    await promptTextarea.fill("What's the weather in Paris?");

    // Fill in the expected tool name
    const expectedToolInput = page.locator('input[placeholder="tool_name (comma-separated for alternatives)"]');
    await expectedToolInput.fill('get_weather');

    // Click Save button in the test case form
    // The TestCaseForm has a .modal-btn-confirm with text "Save"
    const saveBtn = page.locator('.modal-btn-confirm', { hasText: 'Save' }).last();
    await saveBtn.click();

    // Verify the test case appears in the list with the prompt text
    await expect(page.getByText('weather in Paris').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── STEP 5: EDIT TEST CASE ───────────────────────────────────────────

  test('Step 5: Edit test case prompt', async () => {
    // Find the Edit button on the test case row
    // TestCasesList renders an Edit button per row
    const editBtn = page.locator('button', { hasText: 'Edit' }).last();
    await editBtn.click();

    // Wait for the edit form to appear and modify the prompt
    const promptTextarea = page.locator('textarea[placeholder="Enter the user prompt..."]');
    await promptTextarea.waitFor({ state: 'visible', timeout: TIMEOUT.modal });
    await promptTextarea.fill("What's the weather in London?");

    // Click Save
    const saveBtn = page.locator('.modal-btn-confirm', { hasText: 'Save' }).last();
    await saveBtn.click();

    // Verify the updated text is visible
    await expect(page.getByText('weather in London').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── STEP 6: DELETE TEST CASE ─────────────────────────────────────────

  test('Step 6: Delete test case via danger modal', async () => {
    // Click Delete on the test case row
    const deleteBtn = page
      .locator('button', { hasText: 'Delete' })
      .last();
    await deleteBtn.click();

    // Confirm deletion via danger modal
    await confirmDangerModal(page);

    // Verify "No test cases defined yet." message appears (0 cases left)
    await expect(page.locator('text=No test cases defined yet.')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── STEP 7: NAVIGATE BACK & VERIFY SUITE IN LIST ────────────────────

  test('Step 7: Navigate back to Suites list and verify suite row', async () => {
    // Click "Back to Suites" button
    await page.locator('button', { hasText: 'Back to Suites' }).click();
    await page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });

    // Verify "E2E Test Suite" appears in the suite table
    const suiteRow = page.locator('button', { hasText: 'E2E Test Suite' });
    await expect(suiteRow).toBeVisible({ timeout: TIMEOUT.nav });

    // Verify tool count = 1 and case count = 0 in the same row
    const row = suiteRow.locator('xpath=ancestor::tr');
    await expect(row.locator('td').nth(1)).toHaveText('1');
    await expect(row.locator('td').nth(2)).toHaveText('0');
  });

  // ─── STEP 8: DELETE SUITE ─────────────────────────────────────────────

  test('Step 8: Delete suite from Suites list via danger modal', async () => {
    // Find the row containing "E2E Test Suite" and click its Delete button
    const row = page
      .locator('button', { hasText: 'E2E Test Suite' })
      .locator('xpath=ancestor::tr');
    await row.locator('button', { hasText: 'Delete' }).click();

    // Confirm deletion via danger modal
    await confirmDangerModal(page);

    // Verify the suite is no longer in the table
    await expect(page.locator('button', { hasText: 'E2E Test Suite' })).not.toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });
});
