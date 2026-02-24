/**
 * @regression Suite Import/Export + SuiteTable Interactions
 *
 * Deterministic browser tests for suite import from JSON, export, and SuiteTable
 * column sorting. Covers the SuitesView and SuiteTable components.
 *
 * Self-contained: registers its own user, sets up Zai provider.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');
const { confirmDangerModal } = require('../../helpers/modals');

const TEST_EMAIL = uniqueEmail('e2e-suite-import');

test.describe('@regression Suite Import/Export + Table', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(120_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register user + setup Zai
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);

    // Create a suite via UI for export testing
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('Export Test Suite');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── NAVIGATE TO SUITES LIST ─────────────────────────────────────────

  test('Step 1: Navigate to Suites list and verify table', async () => {
    await page.getByRole('link', { name: 'Tool Eval' }).click();
    await page.waitForURL('**/tool-eval/**', { timeout: TIMEOUT.nav });

    await page.locator('.te-subtab').filter({ hasText: 'Suites' }).click();
    await page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });

    // Verify heading (Tool Calling Evaluation is the h2 on the Suites view)
    await expect(
      page.getByRole('heading', { name: /Tool Calling Evaluation/i }),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Verify Export Test Suite is in the table (use button role to avoid matching context pill)
    await expect(
      page.getByRole('button', { name: 'Export Test Suite' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Verify buttons exist
    await expect(
      page.locator('button').filter({ hasText: 'New Suite' }),
    ).toBeVisible();
    await expect(
      page.locator('button').filter({ hasText: /Import Suite/i }),
    ).toBeVisible();
  });

  // ─── EXPORT SUITE ─────────────────────────────────────────────────────

  test('Step 2: Export suite as JSON', async () => {
    // Set up download listener
    const downloadPromise = page.waitForEvent('download');

    // Click the "Export" action button (exact match to avoid matching "Export Test Suite" name)
    await page.getByRole('button', { name: 'Export', exact: true }).click();

    // Wait for download
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain('.json');
  });

  // ─── IMPORT SUITE FROM JSON ──────────────────────────────────────────

  test('Step 3: Import a suite from JSON file', async () => {
    const suiteJson = JSON.stringify({
      name: 'Imported Suite',
      tools: [
        {
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
        },
      ],
      test_cases: [
        {
          prompt: "What's the weather in London?",
          expected_tool: 'get_weather',
          expected_params: { city: 'London' },
        },
      ],
    });

    // Set up file chooser listener before clicking import
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.locator('button').filter({ hasText: /Import Suite/i }).click();

    const fileChooser = await fileChooserPromise;
    // Create a temporary file buffer
    await fileChooser.setFiles({
      name: 'test-suite.json',
      mimeType: 'application/json',
      buffer: Buffer.from(suiteJson),
    });

    // After import, we should be redirected to the suite editor
    await page.waitForURL('**/tool-eval/suites/**', { timeout: TIMEOUT.nav });

    // Verify the imported suite name
    const nameInput = page.locator('input[placeholder="Suite Name"]');
    await expect(nameInput).toHaveValue('Imported Suite', { timeout: TIMEOUT.nav });

    // Verify the tool was imported
    await expect(page.getByText('get_weather').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── VERIFY BOTH SUITES IN LIST ──────────────────────────────────────

  test('Step 4: Navigate back to Suites list and verify both suites', async () => {
    await page.locator('.te-subtab').filter({ hasText: 'Suites' }).click();
    await page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });

    // Both suites should be visible in the table (use button role to avoid context pill/toast)
    await expect(
      page.getByRole('button', { name: 'Export Test Suite' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
    await expect(
      page.getByRole('button', { name: 'Imported Suite' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── SUITE TABLE SORTING ─────────────────────────────────────────────

  test('Step 5: Test SuiteTable column sorting', async () => {
    // Click "Name" column header to sort
    const nameHeader = page.locator('th').filter({ hasText: 'Name' });
    await nameHeader.click();

    // Verify sort indicator appears (▲ or ▼)
    await expect(nameHeader).toContainText(/[▲▼]/);

    // Click again to reverse sort
    await nameHeader.click();
    await expect(nameHeader).toContainText(/[▲▼]/);
  });

  // ─── DELETE IMPORTED SUITE ─────────────────────────────────────────────

  test('Step 6: Delete imported suite from Suites list', async () => {
    // Find the row with Imported Suite and click its delete button
    const row = page.locator('tr, div.flex').filter({ hasText: 'Imported Suite' });
    await row.locator('button').filter({ hasText: /Delete/i }).click();

    // Confirm the danger modal
    await confirmDangerModal(page);

    // Verify it's gone from the table
    await expect(
      page.getByRole('button', { name: 'Imported Suite' }),
    ).not.toBeVisible({ timeout: TIMEOUT.modal });

    // Export Test Suite should still exist
    await expect(
      page.getByRole('button', { name: 'Export Test Suite' }),
    ).toBeVisible();
  });

  // ─── DOWNLOAD EXAMPLE JSON ─────────────────────────────────────────────

  test('Step 7: Download example JSON template', async () => {
    const downloadPromise = page.waitForEvent('download');

    await page.locator('button').filter({ hasText: /Example JSON/i }).click();

    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain('.json');
  });
});
