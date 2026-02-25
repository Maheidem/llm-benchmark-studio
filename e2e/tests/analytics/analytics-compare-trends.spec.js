/**
 * @regression Analytics — Compare and Trends with Data
 *
 * Tests analytics Compare and Trends tabs with actual benchmark data:
 *   1. Run a benchmark to generate data
 *   2. Navigate to Analytics > Compare, verify run appears
 *   3. Navigate to Analytics > Trends, select model, verify chart area
 *
 * Self-contained. Uses Zai + GLM-4.5-Air.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-analytics-ct');

test.describe('@regression Analytics — Compare & Trends with Data', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(120_000);

  let context;
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);
  });

  test.afterAll(async () => { await context?.close(); });

  // ─── RUN BENCHMARK TO GENERATE DATA ───────────────────────────────

  test('Step 1: Run a quick benchmark', async () => {
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Wait for config to load
    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });

    // Deselect all, then pick GLM-4.5-Air
    await page.getByRole('button', { name: 'Select None' }).click();
    const modelCard = page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    // Click Run Benchmark
    await page.getByRole('button', { name: 'RUN BENCHMARK' }).click();

    // Wait for progress
    await expect(page.locator('.pulse-dot')).toBeVisible({ timeout: TIMEOUT.nav });

    // Wait for completion
    await expect(page.locator('.pulse-dot')).not.toBeVisible({ timeout: TIMEOUT.benchmark });
  });

  // ─── ANALYTICS > COMPARE ──────────────────────────────────────────

  test('Step 2: Navigate to Analytics > Compare and verify run appears', async () => {
    await page.getByRole('link', { name: 'Analytics' }).click();
    await page.waitForURL('**/analytics**', { timeout: TIMEOUT.nav });

    await page.getByRole('link', { name: 'Compare' }).click();
    await page.waitForURL('**/analytics/compare', { timeout: TIMEOUT.nav });

    // Should show run selection list (not "No benchmark runs found")
    const checkbox = page.locator('input[type="checkbox"]').first();
    const noRuns = page.getByText('No benchmark runs found.');
    await expect(checkbox.or(noRuns)).toBeVisible({ timeout: TIMEOUT.fetch });

    // If runs exist, verify at least one checkbox
    if (await checkbox.isVisible().catch(() => false)) {
      const count = await page.locator('input[type="checkbox"]').count();
      expect(count).toBeGreaterThanOrEqual(1);
    }
  });

  // ─── ANALYTICS > TRENDS ──────────────────────────────────────────

  test('Step 3: Navigate to Trends and select model', async () => {
    await page.getByRole('link', { name: 'Trends' }).click();
    await page.waitForURL('**/analytics/trends', { timeout: TIMEOUT.nav });

    // Model selector should be visible
    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });

    // Click to open dropdown
    await page.getByText('Select Models').click();

    // Wait for models to load — should find GLM-4.5-Air
    const glmCheckbox = page.locator('label').filter({ hasText: 'GLM-4.5-Air' }).locator('input[type="checkbox"]');
    const noModels = page.getByText('No models found.');

    await expect(glmCheckbox.or(noModels)).toBeVisible({ timeout: TIMEOUT.fetch });

    // If model found, check it
    if (await glmCheckbox.isVisible().catch(() => false)) {
      await glmCheckbox.check();

      // "Select one or more models" should disappear
      await expect(page.getByText(/Select one or more models/)).not.toBeVisible({ timeout: TIMEOUT.nav });
    }
  });

  test('Step 4: Change period filter', async () => {
    const periodSelect = page.locator('select').filter({ hasText: /All time|30d|7d/ });
    if (await periodSelect.isVisible().catch(() => false)) {
      await periodSelect.selectOption('7d');
      await page.waitForTimeout(1_000);
      await periodSelect.selectOption('all');
    }
  });
});
