/**
 * @critical Param Tuner E2E Test
 *
 * Full user journey:
 *   1. Create suite with tool + test case via SuiteSetup.
 *   2. Navigate to Param Tuner, select suite, select model.
 *   3. Configure search space (temperature 0.5-1.0, step 0.5 = 2 combos).
 *   4. Start tuning, verify progress UI + notifications + refresh persistence.
 *   5. Wait for completion, validate Best Config, results table, done notification, history.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * Uses Zai provider with GLM-4.5-Air for real LLM calls.
 * 2 combos: temperature 0.5 and 1.0.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT, dismissOnboarding } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-param-tuner');

test.describe('@critical Param Tuner', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(180_000);

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

    // Create suite with tool + test case
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('PT Test Suite');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── NAVIGATE TO PARAM TUNER ──────────────────────────────────────

  test('Step 1: Navigate to Param Tuner config', async () => {
    await page.locator('.te-subtab').filter({ hasText: 'Param Tuner' }).click();
    await page.waitForURL('**/tool-eval/param-tuner', { timeout: TIMEOUT.nav });

    await expect(page.locator('h2').filter({ hasText: 'Param Tuner' })).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── SELECT SUITE ─────────────────────────────────────────────────

  test('Step 2: Select suite', async () => {
    const suiteSelect = page.locator('select').first();
    await suiteSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });

    const option = suiteSelect.locator('option', { hasText: 'PT Test Suite' });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const optionValue = await option.getAttribute('value');
    await suiteSelect.selectOption(optionValue);

    // Wait for model cards to load
    await expect(page.locator('.model-card').first()).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── SELECT MODEL ─────────────────────────────────────────────────

  test('Step 3: Select GLM-4.5-Air', async () => {
    const modelCard = page
      .locator('.model-card')
      .filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);
  });

  // ─── VERIFY SEARCH SPACE BUILDER ──────────────────────────────────

  test('Step 4: Verify Search Space Builder', async () => {
    await expect(page.getByText('Search Space')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // At least one param row exists
    await expect(page.locator('[data-param-name]').first()).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── CONFIGURE TEMPERATURE ────────────────────────────────────────

  test('Step 5: Enable temperature with minimal range', async () => {
    const tempRow = page.locator('[data-param-name="temperature"]');
    await expect(tempRow).toBeVisible({ timeout: TIMEOUT.nav });

    // Check toggle if unchecked
    const toggle = tempRow.locator('input[type="checkbox"]');
    if (!(await toggle.isChecked())) {
      await toggle.check();
    }
    await expect(toggle).toBeChecked();

    // Set Min=0.5, Max=1.0, Step=0.5 via the 3 inputs in the grid
    const gridInputs = tempRow.locator('.grid.grid-cols-3 input');
    const minInput = gridInputs.nth(0);
    const maxInput = gridInputs.nth(1);
    const stepInput = gridInputs.nth(2);

    await minInput.fill('0.5');
    await maxInput.fill('1.0');
    await stepInput.fill('0.5');

    // Uncheck tool_choice if checked (auto-enabled by default)
    const tcRow = page.locator('[data-param-name="tool_choice"]');
    await tcRow.scrollIntoViewIfNeeded();
    const tcToggle = tcRow.locator('input[type="checkbox"]').first();
    if (await tcToggle.isChecked()) {
      await tcToggle.uncheck({ force: true });
    }
  });

  // ─── VERIFY COMBO COUNT ───────────────────────────────────────────

  test('Step 6: Verify combo count', async () => {
    await expect(page.getByText('2 combos').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── START TUNING ─────────────────────────────────────────────────

  test('Step 7: Start tuning', async () => {
    await page.locator('.run-btn').filter({ hasText: 'Start Tuning' }).click();
    await page.waitForURL('**/tool-eval/param-tuner/run', { timeout: TIMEOUT.nav });
  });

  // ─── VERIFY PROGRESS UI ──────────────────────────────────────────

  test('Step 8: Verify progress UI', async () => {
    await expect(page.locator('.pulse-dot')).toBeVisible({
      timeout: TIMEOUT.nav,
    });
    await expect(page.locator('.progress-fill')).toBeAttached({
      timeout: TIMEOUT.nav,
    });

    // ETA appears after first combo completes
    await expect(page.getByText(/~\d+[smh]\s+left/).first()).toBeVisible({ timeout: TIMEOUT.stress });
  });

  // ─── NOTIFICATION: Running state ──────────────────────────────────

  test('Step 9: Notification — running state', async () => {
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Running item with progress %
    const runningItem = page
      .locator('.notif-item')
      .filter({ hasText: /Running/ });
    await expect(runningItem).toBeVisible({ timeout: TIMEOUT.modal });
    await expect(runningItem).toContainText(/\d+%/);

    // Close dropdown
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).not.toBeVisible();
  });

  // ─── NOTIFICATION: Persistence across refresh ─────────────────────

  test('Step 10: Refresh + verify notification persists', async () => {
    await page.reload();
    await page.waitForLoadState('networkidle');
    await dismissOnboarding(page);

    // Wait for app hydration - bell should be interactive again
    await expect(page.locator('.notif-bell')).toBeVisible({ timeout: TIMEOUT.nav });

    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Notification still present (hydrated from GET /api/jobs)
    const runningItem = page
      .locator('.notif-item')
      .filter({ hasText: /Running/ });
    await expect(runningItem).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── NOTIFICATION: Click to navigate to live run page ─────────────

  test('Step 11: Click notification — navigate to live run page', async () => {
    const runningItem = page
      .locator('.notif-item')
      .filter({ hasText: /Running/ });
    await runningItem.click();

    await expect(page).toHaveURL(/\/tool-eval\/param-tuner\/run/);
  });

  // ─── WAIT FOR COMPLETION ──────────────────────────────────────────

  test('Step 12: Wait for completion', async () => {
    // Navigate to param tuner run page directly
    await page.goto('/tool-eval/param-tuner/run');
    await page.waitForURL('**/tool-eval/param-tuner/run', { timeout: TIMEOUT.nav });
    await dismissOnboarding(page);

    // Wait for completion: pulse-dot gone OR "Best Config" text visible
    await expect(
      page.locator('.pulse-dot').or(page.getByText('Best Config')),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Now wait for final state — either pulse-dot disappears or Best Config shows
    await expect(page.getByText('Best Config')).toBeVisible({
      timeout: TIMEOUT.stress,
    });
  });

  // ─── VERIFY BEST CONFIG ──────────────────────────────────────────

  test('Step 13: Verify Best Config card', async () => {
    await expect(page.getByText('Best Config')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── VERIFY RESULTS TABLE ────────────────────────────────────────

  test('Step 14: Verify results table', async () => {
    const rowCount = await page.locator('.results-table tbody tr').count();
    expect(rowCount).toBeGreaterThanOrEqual(1);
  });

  // ─── NOTIFICATION: Done state ─────────────────────────────────────

  test('Step 15: Notification — done state', async () => {
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    const doneBadge = page.locator('.notif-status-badge.done');
    await expect(doneBadge).toBeVisible({ timeout: TIMEOUT.modal });
    await expect(doneBadge).toHaveText('Done');

    // Close dropdown
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).not.toBeVisible();
  });

  // ─── HISTORY: Verify entry ────────────────────────────────────────

  test('Step 16: Navigate to History, verify entry', async () => {
    await page.goto('/tool-eval/param-tuner/history');
    await page.waitForURL('**/tool-eval/param-tuner/history', { timeout: TIMEOUT.nav });
    await dismissOnboarding(page);

    await expect(page.getByText('Param Tuner History')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Card with suite name and completed status
    await expect(page.getByText('PT Test Suite').first()).toBeVisible({
      timeout: TIMEOUT.nav,
    });
    await expect(page.getByText('completed').first()).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });
});
