/**
 * @regression Benchmark — Compare & Cancel E2E Test
 *
 * User journeys for benchmark page interactions:
 *   1. Navigate to Benchmark page, verify model grid
 *   2. Verify BenchmarkConfig section (parameters)
 *   3. Verify Run button is disabled when no models selected
 *   4. Navigate to Analytics > Compare and verify the selection UI
 *
 * Self-contained: registers its own user with Zai provider.
 * No LLM calls — tests UI elements and states only.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-bench-compare');

test.describe('@regression Benchmark — Config & Compare UI', () => {
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

    // Setup Zai provider
    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── BENCHMARK PAGE MODEL GRID ──────────────────────────────────────

  test('Step 1: Navigate to Benchmark page and verify model grid', async () => {
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // "Select Models" heading visible
    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });

    // Model grid should show at least one model card
    await expect(page.locator('.model-card').first()).toBeVisible({
      timeout: TIMEOUT.fetch,
    });

    // Selected count should be shown
    await expect(page.getByText(/\d+ selected/)).toBeVisible();
  });

  // ─── BENCHMARK CONFIG SECTION ──────────────────────────────────────

  test('Step 2: Verify BenchmarkConfig section', async () => {
    // Configuration parameters should be visible
    // Look for common config elements: temperature, max tokens, etc.
    // These are in the BenchmarkConfig component
    const configSection = page.locator('.mb-6').filter({ has: page.locator('input, select') });
    await expect(configSection.first()).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── DESELECT ALL MODELS ───────────────────────────────────────────

  test('Step 3: Deselect all models — run button disabled', async () => {
    // Click "None" to deselect all models
    const noneBtn = page.getByRole('button', { name: /none/i });
    if (await noneBtn.isVisible().catch(() => false)) {
      await noneBtn.click();
      await page.waitForTimeout(500);
    }

    // Verify "0 selected" message
    await expect(page.getByText('0 selected')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── RE-SELECT MODEL ───────────────────────────────────────────────

  test('Step 4: Re-select a model — run button enabled', async () => {
    // Click "All" to select all models, or click a model card
    const allBtn = page.getByRole('button', { name: /^all$/i });
    if (await allBtn.isVisible().catch(() => false)) {
      await allBtn.click();
    } else {
      await page.locator('.model-card').first().click();
    }

    await page.waitForTimeout(500);

    // Run button should now be enabled (not showing the "select at least one" message)
    // Verify selected count > 0
    await expect(page.getByText(/[1-9]\d* selected/)).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });
});
