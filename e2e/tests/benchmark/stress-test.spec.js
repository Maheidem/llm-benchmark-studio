/**
 * @critical Benchmark Stress Test + Notifications + History Validation
 *
 * Full user journey:
 *   1. Create Zai provider, set API key, fetch models, add GLM-4.5-Air, set 200K ctx.
 *   2. Configure 3 context tiers (0, 5K, 10K) for stress test mode.
 *   3. Run benchmark, verify progress UI + notifications + refresh persistence.
 *   4. Wait for completion, validate stress results, done notification, history page.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * 3 total iterations: 1 model x 3 tiers x 1 run.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-stress');

test.describe('@critical Stress Test + Notifications + History', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(180_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── SETUP ────────────────────────────────────────────────────────────

  test('Step 1: Setup - Create Zai provider + key + model', async () => {
    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);
  });

  // ─── TIER SELECTION ───────────────────────────────────────────────────

  test('Step 2: Select model and configure tiers', async () => {
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    await expect(page.getByText('Select Models')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Deselect all, then pick GLM-4.5-Air
    await page.getByRole('button', { name: 'Select None' }).click();
    const modelCard = page
      .locator('.model-card')
      .filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    // Add 5K and 10K tiers (0 is selected by default)
    await page.getByRole('button', { name: '5K', exact: true }).click();
    await page.getByRole('button', { name: '10K', exact: true }).click();

    // Verify stress test mode activated
    await expect(page.getByText('STRESS TEST MODE')).toBeVisible();
  });

  // ─── RUN BENCHMARK ────────────────────────────────────────────────────

  test('Step 3: Run benchmark and verify progress', async () => {
    await page.getByRole('button', { name: 'RUN BENCHMARK' }).click();

    // Progress UI should appear
    await expect(page.locator('.pulse-dot')).toBeVisible({ timeout: TIMEOUT.nav });

    // Progress counter shows X/3 format (first match — main counter, not per-provider)
    await expect(page.getByText(/\d+\/3/).first()).toBeVisible({ timeout: TIMEOUT.fetch });

    // Running provider label
    await expect(page.getByText(/Running\s+1\s+provider/i)).toBeVisible();

    // ETA should appear once a few results come in
    await expect(page.getByText(/~\d+[smh]\s+left/).first()).toBeVisible({ timeout: TIMEOUT.stress });
  });

  // ─── RESULTS STREAM LIVE ──────────────────────────────────────────────

  test('Step 3b: Verify results stream live during execution', async () => {
    // Results should appear while benchmark is still running
    // Wait for at least one result row in the results table
    await expect(page.locator('.results-table tbody tr').first()).toBeVisible({
      timeout: TIMEOUT.stress,
    });

    // Benchmark should still be running (pulse-dot visible)
    await expect(page.locator('.pulse-dot')).toBeVisible();

    // At least one stat card should appear with live data
    await expect(page.locator('.stat-card').first()).toBeVisible({
      timeout: TIMEOUT.stress,
    });
  });

  // ─── NOTIFICATION: During run ─────────────────────────────────────────

  test('Step 4: Open notification during run', async () => {
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Running benchmark with progress %
    const runningItem = page
      .locator('.notif-item')
      .filter({ hasText: /Running/ });
    await expect(runningItem).toBeVisible({ timeout: TIMEOUT.modal });
    await expect(runningItem).toContainText(/\d+%/);

    // Close dropdown
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).not.toBeVisible();
  });

  // ─── NOTIFICATION: Persistence across refresh ─────────────────────────

  test('Step 5: Refresh page + verify notification persistence', async () => {
    await page.reload();
    await page.waitForLoadState('networkidle');

    // Wait for app hydration - bell should be interactive again
    await expect(page.locator('.notif-bell')).toBeVisible({ timeout: TIMEOUT.nav });

    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Benchmark notification still present (hydrated from GET /api/jobs)
    const runningItem = page
      .locator('.notif-item')
      .filter({ hasText: /Running/ });
    await expect(runningItem).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── NOTIFICATION: Click to return ────────────────────────────────────

  test('Step 6: Click notification to return to benchmark', async () => {
    const runningItem = page
      .locator('.notif-item')
      .filter({ hasText: /Running/ });
    await runningItem.click();

    // Verify we're on the benchmark page with full running state
    await expect(page).toHaveURL(/\/benchmark/);
    await expect(page.locator('.pulse-dot')).toBeVisible({ timeout: TIMEOUT.nav });
    await expect(page.getByText(/Benchmark running/i)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();
  });

  // ─── BUG: Progress doesn't resume after refresh ─────────────────────

  test('Step 6b: Progress counter resumes updating after refresh', async () => {
    // After refresh + notification click, the progress counter should
    // resume tracking (e.g. 1/3, 2/3) — not stay stuck at 0/0
    await expect(page.getByText(/[1-3]\/3/).first()).toBeVisible({
      timeout: 20_000,
    });
  });

  // ─── WAIT FOR COMPLETION ──────────────────────────────────────────────

  test('Step 7: Wait for benchmark to complete', async () => {
    // Benchmark is still running — wait for results to appear (up to 120s)
    await expect(page.locator('.stat-card').first()).toBeVisible({
      timeout: TIMEOUT.stress,
    });

    // Running state should be gone
    await expect(page.locator('.pulse-dot')).not.toBeVisible({
      timeout: TIMEOUT.modal,
    });
    await expect(
      page.getByRole('button', { name: 'Cancel' }),
    ).not.toBeVisible();
  });

  // ─── VALIDATE STRESS RESULTS ──────────────────────────────────────────

  test('Step 8: Validate stress test results', async () => {
    // 4 stat cards in stress mode
    await expect(page.locator('.stat-card')).toHaveCount(4);

    // Stress-specific cards
    await expect(
      page.locator('.stat-card').filter({ hasText: 'Best @ 0K' }),
    ).toBeVisible();
    await expect(
      page.locator('.stat-card').filter({ hasText: 'Best @ 10K' }),
    ).toBeVisible();
    await expect(
      page.locator('.stat-card').filter({ hasText: 'Tiers Tested' }),
    ).toBeVisible();
    await expect(
      page.locator('.stat-card').filter({ hasText: /All nominal|Failures/ }),
    ).toBeVisible();

    // Context column in results table (stress mode only)
    await expect(
      page.locator('.results-table th').filter({ hasText: 'Context' }),
    ).toBeVisible();

    // 2 charts visible (throughput + TTFT)
    const charts = page.locator('canvas');
    await expect(charts.first()).toBeVisible();
    expect(await charts.count()).toBeGreaterThanOrEqual(2);

    // Export CSV button
    await expect(
      page.getByRole('button', { name: 'Export CSV' }),
    ).toBeVisible();

    // 3 result rows (1 per tier)
    await expect(page.locator('.results-table tbody tr')).toHaveCount(3);
  });

  // ─── NOTIFICATION: Done state ─────────────────────────────────────────

  test('Step 9: Verify notification shows "Done"', async () => {
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    const doneBadge = page.locator('.notif-status-badge.done');
    await expect(doneBadge).toBeVisible({ timeout: TIMEOUT.modal });
    await expect(doneBadge).toHaveText('Done');

    // Close dropdown
    await page.locator('.notif-bell').click();
  });

  // ─── HISTORY: Verify the run ──────────────────────────────────────────

  test('Step 10: Verify history page has the run', async () => {
    await page.getByRole('link', { name: 'History' }).click();
    await page.waitForURL('**/history', { timeout: TIMEOUT.nav });

    await expect(page.getByText('Benchmark History')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Winner badge
    const winnerBadge = page
      .locator('.badge')
      .filter({ hasText: 'P1: GLM-4.5-Air' });
    await expect(winnerBadge).toBeVisible({ timeout: TIMEOUT.nav });

    // CTX tier badge
    const ctxBadge = page
      .locator('.badge')
      .filter({ hasText: /CTX\s+0\s*\/\s*5K\s*\/\s*10K/ });
    await expect(ctxBadge).toBeVisible();

    // Table has Context column with Base, 5K, 10K rows
    const historyCard = page
      .locator('.card.fade-in')
      .filter({ has: winnerBadge });
    const contextCells = historyCard
      .locator('td')
      .filter({ hasText: /^(Base|5K|10K)$/ });
    await expect(contextCells).toHaveCount(3);
  });
});
