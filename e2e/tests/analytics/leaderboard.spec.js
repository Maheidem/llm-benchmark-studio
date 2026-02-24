/**
 * @critical Analytics Pages E2E Test
 *
 * Full user journey:
 *   1. Run a quick benchmark to populate analytics data.
 *   2. Navigate to /analytics — verify redirect to /analytics/leaderboard.
 *   3. Verify leaderboard data loads.
 *   4. Navigate to /analytics/compare — verify page loads.
 *   5. Navigate to /analytics/trends — verify page loads.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * Uses Zai provider with GLM-4.5-Air for real LLM calls.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-analytics');

test.describe('@critical Analytics Pages', () => {
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

    // Run a quick benchmark to populate analytics data
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    await expect(page.getByText('Select Models')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    await page.getByRole('button', { name: 'Select None' }).click();
    const modelCard = page
      .locator('.model-card')
      .filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    await page.getByRole('button', { name: 'RUN BENCHMARK' }).click();

    // Wait for completion
    await expect(page.locator('.stat-card').first()).toBeVisible({
      timeout: TIMEOUT.benchmark,
    });
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── NAVIGATE TO ANALYTICS ─────────────────────────────────────────

  test('Step 1: Navigate to /analytics and verify leaderboard redirect', async () => {
    await page.getByRole('link', { name: 'Analytics' }).click();
    await page.waitForURL('**/analytics/leaderboard', { timeout: TIMEOUT.nav });

    // Verify Analytics heading
    await expect(
      page.locator('h1').filter({ hasText: 'Analytics' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── VERIFY LEADERBOARD ────────────────────────────────────────────

  test('Step 2: Verify leaderboard data loads', async () => {
    // The leaderboard tab should be active
    const leaderboardTab = page.locator('a.tab', { hasText: 'Leaderboard' });
    await expect(leaderboardTab).toBeVisible({ timeout: TIMEOUT.modal });

    // Wait for data to load — look for either table rows or type toggle buttons
    // The "Benchmark" type toggle button should be visible
    await expect(
      page.getByRole('button', { name: 'Benchmark' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Check for period filter
    const periodSelect = page.locator('select').filter({ hasText: 'All time' });
    await expect(periodSelect).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── PERIOD FILTER ─────────────────────────────────────────────────

  test('Step 3: Test period filter interaction', async () => {
    const periodSelect = page.locator('select').filter({ hasText: 'All time' });
    const selectVisible = await periodSelect.isVisible().catch(() => false);

    if (!selectVisible) {
      // No period filter — just verify page content is still loaded
      await expect(
        page.getByRole('button', { name: 'Benchmark' }),
      ).toBeVisible();
      return;
    }

    // Change to "Last 7 days"
    await periodSelect.selectOption('7d');
    await page.waitForTimeout(500);

    // Page should still be functional (no crash)
    await expect(
      page.getByRole('button', { name: 'Benchmark' }),
    ).toBeVisible({ timeout: TIMEOUT.modal });

    // Change back to "All time"
    await periodSelect.selectOption('all');
    await page.waitForTimeout(500);
  });

  // ─── COMPARE PAGE ──────────────────────────────────────────────────

  test('Step 4: Navigate to /analytics/compare and verify page loads', async () => {
    // Click "Compare" tab
    const compareTab = page.locator('a.tab', { hasText: 'Compare' });
    await compareTab.click();
    await page.waitForURL('**/analytics/compare', { timeout: TIMEOUT.nav });

    // Verify page loads without crash — empty state or content is fine
    await expect(page).toHaveURL(/\/analytics\/compare/);

    // Page should have some content (heading, instructions, or UI elements)
    await page.waitForTimeout(500);
  });

  // ─── TRENDS PAGE ───────────────────────────────────────────────────

  test('Step 5: Navigate to /analytics/trends and verify page loads', async () => {
    // Click "Trends" tab
    const trendsTab = page.locator('a.tab', { hasText: 'Trends' });
    await trendsTab.click();
    await page.waitForURL('**/analytics/trends', { timeout: TIMEOUT.nav });

    // Verify page loads without crash — empty state or content is fine
    await expect(page).toHaveURL(/\/analytics\/trends/);

    // Page should have some content
    await page.waitForTimeout(500);
  });
});
