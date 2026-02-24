/**
 * @smoke Provider CRUD Test Suite
 *
 * Deterministic browser tests for provider create, read, update, delete flows.
 * Covers: create Zai provider, set key, fetch models, verify on benchmark page,
 *         delete a model, delete the provider.
 *
 * IMPORTANT: All test data uses the Zai provider and its models ONLY.
 * No fake/mock providers — Zai is the only authorized test provider.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, ZAI_API_KEY, TIMEOUT } = require('../../helpers/constants');
const { confirmDangerModal } = require('../../helpers/modals');

const TEST_EMAIL = uniqueEmail('e2e-prov-crud');

test.describe('@smoke Provider CRUD', () => {
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
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── CLEANUP: If Zai already exists, tear it down first ──────────

  test('Step 0: Cleanup — delete Zai if it already exists', async () => {
    // Navigate to Settings > Providers
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('link', { name: 'Providers' }).click();
    await expect(page).toHaveURL(/\/settings\/providers/);

    // Check if Zai provider exists
    const zaiBadge = page.locator('.badge', { hasText: 'Zai' });
    const exists = await zaiBadge.isVisible().catch(() => false);

    if (!exists) {
      test.skip();
      return;
    }

    // Zai exists — delete the provider
    const card = page.locator('.card').filter({ has: zaiBadge });
    await card.locator('button').filter({ hasText: /^Del$/ }).click();
    await confirmDangerModal(page);
    await expect(zaiBadge).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── CREATE PROVIDER ──────────────────────────────────────────────────

  test('Step 1: Navigate to Settings > Providers and create Zai', async () => {
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('link', { name: 'Providers' }).click();
    await expect(page).toHaveURL(/\/settings\/providers/);

    const ps = new ProviderSetup(page);
    await ps.createProvider('Zai', 'https://api.z.ai/api/coding/paas/v4/', 'zai');
  });

  test('Step 2: Verify Zai badge visible in provider list', async () => {
    await expect(
      page.locator('.badge', { hasText: 'Zai' }),
    ).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── SET API KEY + FETCH MODELS ─────────────────────────────────────

  test('Step 3: Set Zai API key and fetch models', async () => {
    const ps = new ProviderSetup(page);
    await ps.setApiKey('Zai', ZAI_API_KEY);
    await ps.fetchAndAddModels('Zai', ['GLM-4.5-Air', 'GLM-4.7']);
    await ps.setContextWindow('Zai', 'GLM-4.5-Air', '200K');
    await ps.setContextWindow('Zai', 'GLM-4.7', '200K');
  });

  // ─── VERIFY ON BENCHMARK PAGE ─────────────────────────────────────────

  test('Step 4: Verify Zai appears on Benchmark page with models', async () => {
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });

    // Zai provider group should appear on the benchmark page
    await expect(
      page.locator('.provider-group').filter({ hasText: 'Zai' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Both models should be visible
    await expect(
      page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' }),
    ).toBeVisible();
    await expect(
      page.locator('.model-card').filter({ hasText: 'GLM-4.7' }),
    ).toBeVisible();
  });

  // ─── DELETE A MODEL ────────────────────────────────────────────────────

  test('Step 5: Delete GLM-4.7 model and verify removal from benchmark', async () => {
    // Navigate back to Settings > Providers
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('link', { name: 'Providers' }).click();
    await expect(page).toHaveURL(/\/settings\/providers/);

    const zaiBadge = page.locator('.badge', { hasText: 'Zai' });
    const card = page.locator('.card').filter({ has: zaiBadge });

    // Find the GLM-4.7 model section and click its × delete button
    const modelSection = card.locator('div.px-5').filter({ hasText: 'GLM-4.7' });
    await modelSection.locator('button:has-text("×")').click();

    // Confirm the "Remove Model" danger modal
    await confirmDangerModal(page);

    // Verify GLM-4.7 is gone from the provider card
    await expect(
      card.getByText('GLM-4.7', { exact: true }),
    ).not.toBeVisible({ timeout: TIMEOUT.modal });

    // Verify it's gone from the Benchmark page too
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });

    // GLM-4.7 should be gone, GLM-4.5-Air should still exist
    await expect(
      page.locator('.model-card').filter({ hasText: 'GLM-4.7' }),
    ).toHaveCount(0);
    await expect(
      page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' }),
    ).toBeVisible();
  });

  // ─── DELETE PROVIDER ──────────────────────────────────────────────────

  test('Step 6: Delete Zai provider and verify badge gone', async () => {
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('link', { name: 'Providers' }).click();
    await expect(page).toHaveURL(/\/settings\/providers/);

    const zaiBadge = page.locator('.badge', { hasText: 'Zai' });
    const card = page.locator('.card').filter({ has: zaiBadge });

    await card.locator('button').filter({ hasText: /^Del$/ }).click();
    await confirmDangerModal(page);

    // Verify Zai badge is gone from providers page
    await expect(zaiBadge).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  test('Step 7: Verify Zai is gone from Benchmark page', async () => {
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });

    // No Zai provider group should exist
    await expect(
      page.locator('.provider-group').filter({ hasText: 'Zai' }),
    ).toHaveCount(0);
  });
});
