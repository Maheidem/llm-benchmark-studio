/**
 * @critical Tool Eval — Evaluate Run E2E Test
 *
 * Full user journey:
 *   1. Create a suite with tool + test case via the UI.
 *   2. Navigate to Evaluate, select suite, select model.
 *   3. Run evaluation, verify progress UI, wait for completion.
 *   4. Verify results table, then check History subtab for the run.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * Uses Zai provider with GLM-4.5-Air for real LLM calls.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-eval-run');

test.describe('@critical Tool Eval — Evaluate Run', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(120_000);

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

    // Setup Zai provider with GLM-4.5-Air
    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── SUITE CREATION ─────────────────────────────────────────────────

  test('Step 1: Create a suite with tool + test case via UI', async () => {
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('Eval Test Suite');
  });

  // ─── NAVIGATE TO EVALUATE ──────────────────────────────────────────

  test('Step 2: Navigate to Tool Eval > Evaluate', async () => {
    await page.locator('.te-subtab').filter({ hasText: 'Evaluate' }).click();
    await page.waitForURL('**/tool-eval/evaluate', { timeout: TIMEOUT.nav });

    await expect(page.getByRole('heading', { name: 'Evaluate' })).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── SELECT SUITE ──────────────────────────────────────────────────

  test('Step 3: Select suite from dropdown', async () => {
    const suiteSelect = page.locator('select').first();
    await suiteSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });

    // Select option containing "Eval Test Suite"
    const option = suiteSelect.locator('option', { hasText: 'Eval Test Suite' });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const optionValue = await option.getAttribute('value');
    await suiteSelect.selectOption(optionValue);
  });

  // ─── SELECT MODEL ──────────────────────────────────────────────────

  test('Step 4: Select GLM-4.5-Air model card', async () => {
    const modelCard = page
      .locator('.model-card')
      .filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);
  });

  // ─── START EVAL ────────────────────────────────────────────────────

  test('Step 5: Click Start Eval and verify progress UI', async () => {
    await page.locator('.run-btn').filter({ hasText: 'Start Eval' }).click();

    // Progress UI should appear
    await expect(page.locator('.pulse-dot')).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── WAIT FOR COMPLETION ───────────────────────────────────────────

  test('Step 6: Wait for completion and verify results', async () => {
    // Wait for results table to appear (up to 120s for LLM call)
    await expect(page.locator('.results-table tbody tr').first()).toBeVisible({
      timeout: TIMEOUT.stress,
    });

    // Verify at least 1 result row exists
    const rowCount = await page.locator('.results-table tbody tr').count();
    expect(rowCount).toBeGreaterThanOrEqual(1);

    // Verify score-related column is present
    await expect(
      page.locator('.results-table th').filter({ hasText: 'Score' }),
    ).toBeVisible();
  });

  // ─── CHECK HISTORY ─────────────────────────────────────────────────

  test('Step 7: Navigate to Tool Eval > History and verify eval run entry', async () => {
    await page.locator('.te-subtab').filter({ hasText: 'History' }).click();
    await page.waitForURL('**/tool-eval/history', { timeout: TIMEOUT.nav });

    await expect(page.getByText('Eval History')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Verify at least one history entry exists (the run we just completed)
    // The history table should have at least one row with our suite name
    await expect(
      page.getByText('Eval Test Suite').first(),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });
});
