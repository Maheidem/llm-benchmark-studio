/**
 * @critical Zai Provider Setup + Single Model Benchmark
 *
 * Full user journey:
 *   0. If Zai already exists → delete a model, verify gone from benchmark,
 *      delete the provider, verify gone from benchmark.
 *   1. Create Zai provider, set API key, fetch models, add 3, set 200K context.
 *   2. Run benchmark on GLM-4.5-Air, verify results.
 *
 * This test registers its own user (no dependency on other test files).
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');
const { confirmDangerModal } = require('../../helpers/modals');

const TEST_EMAIL = uniqueEmail('e2e-zai');

test.describe('@critical Zai Provider Setup + Benchmark', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(120_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register and login
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── CLEANUP: If Zai already exists, tear it down first ───

  test('Step 0a: If Zai exists, delete a model and verify removal from benchmark', async () => {
    // Navigate to Settings > Providers
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('link', { name: 'Providers' }).click();
    await expect(page).toHaveURL(/\/settings\/providers/);

    // Check if Zai provider exists
    const zaiBadge = page.locator('.badge', { hasText: 'Zai' });
    const zaiExists = await zaiBadge.isVisible().catch(() => false);

    if (!zaiExists) {
      // Nothing to clean up — skip
      test.skip();
      return;
    }

    // Zai exists — find the card
    const zaiCard = page.locator('.card').filter({ has: zaiBadge });

    // Pick the first model's × (delete) button to remove it
    const firstModel = zaiCard
      .locator('div.px-5')
      .filter({ has: page.locator('.font-mono.text-zinc-700') })
      .first();
    const modelName = await firstModel.locator('.text-zinc-200').first().textContent();

    // Click the × delete button on the first model
    await firstModel.locator('button:has-text("×")').click();

    // Confirm the "Remove Model" danger modal
    await confirmDangerModal(page);

    // Wait for the page to refresh and verify model is gone from providers
    await expect(
      zaiCard.getByText(modelName.trim(), { exact: true }),
    ).not.toBeVisible({ timeout: TIMEOUT.modal });

    // Now verify it's gone from the Benchmark page too
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });

    // The deleted model should NOT be in the model grid
    await expect(
      page.locator('.model-card').filter({ hasText: modelName.trim() }),
    ).toHaveCount(0);
  });

  test('Step 0b: If Zai exists, delete the provider and verify removal from benchmark', async () => {
    // Navigate to Settings > Providers
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('link', { name: 'Providers' }).click();
    await expect(page).toHaveURL(/\/settings\/providers/);

    // Check if Zai provider still exists
    const zaiBadge = page.locator('.badge', { hasText: 'Zai' });
    const zaiExists = await zaiBadge.isVisible().catch(() => false);

    if (!zaiExists) {
      test.skip();
      return;
    }

    // Find Zai card and click "Del"
    const zaiCard = page.locator('.card').filter({ has: zaiBadge });
    await zaiCard.getByRole('button', { name: 'Del' }).click();

    // Confirm the "Delete Provider" danger modal
    await confirmDangerModal(page);

    // Verify Zai badge is gone from providers page
    await expect(zaiBadge).not.toBeVisible({ timeout: TIMEOUT.modal });

    // Navigate to Benchmark and verify Zai group is completely gone
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });

    // No Zai provider group should exist
    await expect(
      page.locator('.provider-group').filter({ hasText: 'Zai' }),
    ).toHaveCount(0);
  });

  // ─── SETUP: Create Zai from scratch (using ProviderSetup component) ───

  test('Step 1: Create Zai provider', async () => {
    const ps = new ProviderSetup(page);
    await ps.navigateToProviders();
    await ps.createProvider('Zai', 'https://api.z.ai/api/coding/paas/v4/', 'zai');
  });

  test('Step 2: Set Zai API key', async () => {
    const ps = new ProviderSetup(page);
    await ps.setApiKey('Zai', require('../../helpers/constants').ZAI_API_KEY);
  });

  test('Step 3: Fetch and add 3 models', async () => {
    const ps = new ProviderSetup(page);
    await ps.fetchAndAddModels('Zai', ['GLM-4.7', 'GLM-4.5-Air', 'GLM-5']);
  });

  test('Step 4: Set 200K context window on all 3 models', async () => {
    const ps = new ProviderSetup(page);
    const targetModels = ['GLM-4.7', 'GLM-4.5-Air', 'GLM-5'];
    for (const modelName of targetModels) {
      await ps.setContextWindow('Zai', modelName, '200K');
    }
  });

  // ─── VERIFY: Run benchmark ───

  test('Step 5: Run benchmark on GLM-4.5-Air', async () => {
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });

    await page.getByRole('button', { name: 'Select None' }).click();

    const modelCard = page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();

    await expect(modelCard).toHaveClass(/selected/);
    await expect(page.getByText('1 selected')).toBeVisible();

    await page.getByRole('button', { name: 'RUN BENCHMARK' }).click();

    await expect(page.locator('.stat-card').first()).toBeVisible({
      timeout: TIMEOUT.benchmark,
    });

    const statCards = page.locator('.stat-card');
    await expect(statCards).toHaveCount(4);
    await expect(
      page.locator('.stat-card').filter({ hasText: 'All nominal' }),
    ).toBeVisible();
  });
});
