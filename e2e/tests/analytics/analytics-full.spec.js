/**
 * @regression Analytics — Full Page E2E Test
 *
 * User journeys for the analytics page:
 *   1. Navigate to Analytics > Leaderboard (default)
 *   2. Verify leaderboard type toggle (Benchmark / Tool Eval)
 *   3. Verify period dropdown filter
 *   4. Navigate to Compare tab and verify run selection UI
 *   5. Navigate to Trends tab and verify model selector
 *
 * Self-contained: registers its own user.
 * No LLM calls — tests navigation and UI elements.
 * Note: Analytics shows data from existing benchmark/eval runs.
 * With no data, it shows empty states — which we verify.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-analytics');

test.describe('@regression Analytics — Full Page', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(60_000);

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
    }
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── NAVIGATE TO ANALYTICS ──────────────────────────────────────────

  test('Step 1: Navigate to Analytics page', async () => {
    await page.getByRole('link', { name: 'Analytics' }).click();
    await page.waitForURL('**/analytics**', { timeout: TIMEOUT.nav });

    await expect(page.locator('h1').filter({ hasText: 'Analytics' })).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── LEADERBOARD TAB ───────────────────────────────────────────────

  test('Step 2: Verify Leaderboard tab with type toggle', async () => {
    // Should default to Leaderboard tab
    await expect(page.getByRole('link', { name: 'Leaderboard' })).toBeVisible();

    // Type toggle buttons should be present
    const benchmarkBtn = page.locator('button').filter({ hasText: 'Benchmark' });
    const toolEvalBtn = page.locator('button').filter({ hasText: 'Tool Eval' });
    await expect(benchmarkBtn).toBeVisible({ timeout: TIMEOUT.nav });
    await expect(toolEvalBtn).toBeVisible();

    // Benchmark should be active by default (has lime style)
    // Click Tool Eval to switch
    await toolEvalBtn.click();
    await page.waitForTimeout(1_000);

    // Switch back to Benchmark
    await benchmarkBtn.click();
    await page.waitForTimeout(1_000);
  });

  // ─── PERIOD FILTER ──────────────────────────────────────────────────

  test('Step 3: Verify period dropdown filter', async () => {
    const periodSelect = page.locator('select').filter({ hasText: 'All time' });
    await expect(periodSelect).toBeVisible();

    // Verify options
    await expect(periodSelect.locator('option')).toHaveCount(4);

    // Change period
    await periodSelect.selectOption('7d');
    await page.waitForTimeout(1_000);

    // Change back
    await periodSelect.selectOption('all');
  });

  // ─── COMPARE TAB ───────────────────────────────────────────────────

  test('Step 4: Navigate to Compare tab', async () => {
    await page.getByRole('link', { name: 'Compare' }).click();
    await page.waitForURL('**/analytics/compare', { timeout: TIMEOUT.nav });

    // Compare label should be visible
    await expect(page.getByText('Select 2-4 runs to compare')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Compare button should be visible but disabled (no runs selected)
    const compareBtn = page.locator('button').filter({ hasText: 'Compare' }).first();
    await expect(compareBtn).toBeVisible();

    // Should show either run list or empty state
    const noRuns = page.getByText('No benchmark runs found.');
    const runList = page.locator('label').filter({ has: page.locator('input[type="checkbox"]') });
    await expect(noRuns.or(runList.first())).toBeVisible({ timeout: TIMEOUT.fetch });
  });

  // ─── TRENDS TAB ────────────────────────────────────────────────────

  test('Step 5: Navigate to Trends tab', async () => {
    await page.getByRole('link', { name: 'Trends' }).click();
    await page.waitForURL('**/analytics/trends', { timeout: TIMEOUT.nav });

    // Model selector button should be visible
    await expect(page.getByText('Select Models')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Period dropdown should be visible
    const periodSelect = page.locator('select').filter({ hasText: 'All time' });
    await expect(periodSelect).toBeVisible();

    // Empty state message when no models selected
    await expect(page.getByText(/Select one or more models/)).toBeVisible();

    // Click model selector to open dropdown
    await page.getByText('Select Models').click();

    // Dropdown should open
    // Either shows "Loading models..." or "No models found." or checkboxes
    const loading = page.getByText('Loading models...');
    const noModels = page.getByText('No models found.');
    const checkbox = page.locator('input[type="checkbox"]').first();
    await expect(loading.or(noModels).or(checkbox)).toBeVisible({
      timeout: TIMEOUT.fetch,
    });
  });
});
