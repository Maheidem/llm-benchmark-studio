/**
 * @critical Param Tuner — History Interactions (Apply, Detail Modal, Delete)
 *
 * Tests ParamTunerHistory view: click run card to open detail modal,
 * verify results table, Apply best config, delete run from history.
 *
 * Self-contained: registers its own user, sets up Zai, creates suite,
 * runs a param tuning session (2 combos), then exercises history interactions.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-pt-adv');

test.describe('@critical Param Tuner — History Interactions', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(180_000);

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

    // Create suite
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('PT Adv Suite');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── RUN PARAM TUNING (MINIMAL) ──────────────────────────────────────

  test('Step 1: Run param tuning with 2 temperature combos', async () => {
    // Navigate to Param Tuner config
    await page.locator('.te-subtab').filter({ hasText: 'Param Tuner' }).click();
    await page.waitForURL('**/tool-eval/param-tuner', { timeout: TIMEOUT.nav });

    // Select suite
    const suiteSelect = page.locator('select').first();
    await suiteSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });
    const option = suiteSelect.locator('option', { hasText: 'PT Adv Suite' });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const optionValue = await option.getAttribute('value');
    await suiteSelect.selectOption(optionValue);

    // Select GLM-4.5-Air
    const modelCard = page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.waitFor({ state: 'visible', timeout: TIMEOUT.nav });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    // Wait for search space to load
    await expect(
      page.locator('.section-label', { hasText: /Search Space/i }),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Enable temperature param with min=0.5, max=1.0, step=0.5 → 2 combos
    const tempRow = page.locator('[data-param-name="temperature"]');
    await expect(tempRow).toBeVisible({ timeout: TIMEOUT.nav });

    const toggle = tempRow.locator('input[type="checkbox"]');
    if (!(await toggle.isChecked())) {
      await toggle.click();
    }

    const minInput = tempRow.locator('input[type="number"]').nth(0);
    const maxInput = tempRow.locator('input[type="number"]').nth(1);
    const stepInput = tempRow.locator('input[type="number"]').nth(2);

    await minInput.fill('0.5');
    await maxInput.fill('1.0');
    await stepInput.fill('0.5');

    // Start tuning
    await page.locator('.run-btn').filter({ hasText: 'Start Tuning' }).click();

    // Wait for progress to start
    await expect(page.locator('.pulse-dot')).toBeVisible({ timeout: TIMEOUT.nav });

    // Wait for run to actually complete (pulse-dot disappears)
    await expect(page.locator('.pulse-dot')).not.toBeVisible({ timeout: TIMEOUT.stress });
  });

  // ─── 2ND RUN: VERIFY STALE RESULTS CLEARED ────────────────────────────

  test('Step 1b: Start 2nd run and verify results cleared', async () => {
    // Navigate back to config
    await page.locator('.te-subtab').filter({ hasText: 'Param Tuner' }).click();
    await page.waitForURL('**/tool-eval/param-tuner', { timeout: TIMEOUT.nav });

    // Re-select suite (same one)
    const suiteSelect = page.locator('select').first();
    await suiteSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });
    const option = suiteSelect.locator('option', { hasText: 'PT Adv Suite' });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const optionValue = await option.getAttribute('value');
    await suiteSelect.selectOption(optionValue);

    // Re-select GLM-4.5-Air
    const modelCard = page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.waitFor({ state: 'visible', timeout: TIMEOUT.nav });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    // Re-enable temperature
    const tempRow = page.locator('[data-param-name="temperature"]');
    await expect(tempRow).toBeVisible({ timeout: TIMEOUT.nav });
    const toggle = tempRow.locator('input[type="checkbox"]');
    if (!(await toggle.isChecked())) {
      await toggle.click();
    }
    const minInput = tempRow.locator('input[type="number"]').nth(0);
    const maxInput = tempRow.locator('input[type="number"]').nth(1);
    const stepInput = tempRow.locator('input[type="number"]').nth(2);
    await minInput.fill('0.5');
    await maxInput.fill('1.0');
    await stepInput.fill('0.5');

    // Start 2nd tuning run
    await page.locator('.run-btn').filter({ hasText: 'Start Tuning' }).click();
    await page.waitForURL('**/tool-eval/param-tuner/run', { timeout: TIMEOUT.nav });

    // CRITICAL ASSERTION: Results table should be EMPTY initially
    // The previous run's results should NOT be visible
    const resultsTable = page.locator('.results-table tbody tr');
    const resultCount = await resultsTable.count();
    expect(resultCount).toBe(0);

    // Progress should show running state, not previous results
    await expect(page.locator('.pulse-dot')).toBeVisible({ timeout: TIMEOUT.nav });

    // Wait for run to complete before proceeding to history tests
    await expect(page.locator('.pulse-dot')).not.toBeVisible({ timeout: TIMEOUT.stress });
  });

  // ─── NAVIGATE TO HISTORY ─────────────────────────────────────────────

  test('Step 2: Navigate to Param Tuner History', async () => {
    await page.locator('.te-subtab').filter({ hasText: 'Param Tuner' }).click();
    await page.waitForURL('**/tool-eval/param-tuner', { timeout: TIMEOUT.nav });

    // Click History sub-link or navigate directly
    await page.goto('/tool-eval/param-tuner/history');
    await page.waitForURL('**/tool-eval/param-tuner/history', { timeout: TIMEOUT.nav });

    // Verify heading
    await expect(
      page.getByRole('heading', { name: /Param Tuner History/i }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── VERIFY RUN CARD ──────────────────────────────────────────────────

  test('Step 3: Verify run card with suite name and completed status', async () => {
    // Run card should show the suite name
    const runCard = page.locator('.card').filter({ hasText: 'PT Adv Suite' });
    await expect(runCard).toBeVisible({ timeout: TIMEOUT.nav });

    // Status might still be "running" briefly — wait for "completed" with timeout
    await expect(runCard).toContainText(/completed/i, { timeout: TIMEOUT.stress });
  });

  // ─── CLICK CARD → DETAIL MODAL ─────────────────────────────────────

  test('Step 4: Click run card to open detail modal', async () => {
    const runCard = page.locator('.card').filter({ hasText: 'PT Adv Suite' });
    await runCard.click();

    // Detail modal should open (fixed overlay z-50)
    const modal = page.locator('.fixed.inset-0.z-50');
    await expect(modal).toBeVisible({ timeout: TIMEOUT.modal });

    // Should show results table inside modal
    await expect(modal.locator('table, .results-table').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── VERIFY RESULTS TABLE IN MODAL ─────────────────────────────────

  test('Step 5: Verify results table with sortable columns', async () => {
    const modal = page.locator('.fixed.inset-0.z-50');

    // Should have at least one data row (2 combos)
    const rows = modal.locator('tbody tr');
    const rowCount = await rows.count();
    expect(rowCount).toBeGreaterThanOrEqual(1);

    // Should show temperature values
    await expect(modal.getByText(/0\.5|1\.0/).first()).toBeVisible();

    // Should show score percentage
    await expect(modal.getByText(/%/).first()).toBeVisible();
  });

  // ─── CLOSE MODAL ──────────────────────────────────────────────────

  test('Step 6: Close detail modal', async () => {
    const modal = page.locator('.fixed.inset-0.z-50');

    // Click close button
    const closeBtn = modal.locator('button').filter({ hasText: /×|Close/i }).first();
    await closeBtn.click();

    // Modal should be hidden
    await expect(modal).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── APPLY BEST CONFIG ─────────────────────────────────────────────

  test('Step 7: Click Apply to apply best config', async () => {
    // Click Apply button on the run card (use .stop to prevent card click)
    const runCard = page.locator('.card').filter({ hasText: 'PT Adv Suite' });
    const applyBtn = runCard.locator('button').filter({ hasText: /Apply/i });
    await applyBtn.click();

    // Should show success toast: "Best config applied to shared context"
    await expect(
      page.locator('.toast-success').first(),
    ).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── DELETE RUN ──────────────────────────────────────────────────────

  test('Step 8: Delete run from history', async () => {
    const runCard = page.locator('.card').filter({ hasText: 'PT Adv Suite' });

    // ParamTunerHistory uses window.confirm() — set up dialog handler
    page.on('dialog', (dialog) => dialog.accept());

    // Click delete button (SVG trash icon with title="Delete run")
    const deleteBtn = runCard.locator('button[title="Delete run"]');
    await deleteBtn.click();

    // Run card should be gone
    await expect(runCard).not.toBeVisible({ timeout: TIMEOUT.modal });
  });
});
