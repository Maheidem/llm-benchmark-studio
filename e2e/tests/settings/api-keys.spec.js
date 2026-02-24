/**
 * @smoke API Keys Management Test Suite
 *
 * Deterministic browser tests for API key set, update, and remove flows.
 * Covers: key not set state, set key, update key, remove key.
 *
 * Self-contained: registers its own user, creates its own Zai provider.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, ZAI_API_KEY, TIMEOUT } = require('../../helpers/constants');
const { confirmDangerModal } = require('../../helpers/modals');

const TEST_EMAIL = uniqueEmail('e2e-apikeys');

test.describe('@smoke API Keys Management', () => {
  test.describe.configure({ mode: 'serial' });

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

    // Create Zai provider (no key, no models — just the provider)
    const ps = new ProviderSetup(page);
    await ps.navigateToProviders();
    await ps.createProvider('Zai', 'https://api.z.ai/api/coding/paas/v4/', 'zai');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── VERIFY NOT SET STATE ─────────────────────────────────────────────

  test('Step 1: Navigate to API Keys and verify Zai has no user key', async () => {
    await page.getByRole('link', { name: 'API Keys' }).click();
    await expect(page).toHaveURL(/\/settings\/keys/);

    // Find the Zai row
    const zaiRow = page.locator('.px-5.py-3').filter({
      has: page.locator('.text-zinc-300', { hasText: /^Zai$/ }),
    });
    await expect(zaiRow).toBeVisible({ timeout: TIMEOUT.nav });

    // Should show "NOT SET" or "SHARED" (if global key exists in env), but NOT "YOUR KEY"
    const notSet = zaiRow.getByText('NOT SET');
    const shared = zaiRow.getByText('SHARED');
    await expect(notSet.or(shared)).toBeVisible({ timeout: TIMEOUT.modal });

    // Should show "Set Key" button (not "Update") since no per-user key
    await expect(zaiRow.getByRole('button', { name: 'Set Key' })).toBeVisible();
  });

  // ─── SET KEY ──────────────────────────────────────────────────────────

  test('Step 2: Set API key for Zai and verify YOUR KEY badge', async () => {
    const zaiRow = page.locator('.px-5.py-3').filter({
      has: page.locator('.text-zinc-300', { hasText: /^Zai$/ }),
    });

    // Click "Set Key" button
    await zaiRow.getByRole('button', { name: 'Set Key' }).click();

    // Fill the modal with the API key
    const modal = page.locator('.modal-overlay');
    await modal.waitFor({ state: 'visible', timeout: TIMEOUT.modal });
    await modal.locator('.modal-input').fill(ZAI_API_KEY);
    await modal.locator('.modal-btn-confirm').click();
    await modal.waitFor({ state: 'hidden', timeout: TIMEOUT.modal });

    // Verify "YOUR KEY" badge appears
    await expect(zaiRow.getByText('YOUR KEY')).toBeVisible({ timeout: TIMEOUT.modal });

    // "Set Key" should now read "Update"
    await expect(zaiRow.getByRole('button', { name: 'Update' })).toBeVisible();
  });

  // ─── UPDATE KEY ───────────────────────────────────────────────────────

  test('Step 3: Update API key and verify YOUR KEY still shown', async () => {
    const zaiRow = page.locator('.px-5.py-3').filter({
      has: page.locator('.text-zinc-300', { hasText: /^Zai$/ }),
    });

    // Click "Update" button
    await zaiRow.getByRole('button', { name: 'Update' }).click();

    // Fill the modal with the same API key (simulating an update)
    const modal = page.locator('.modal-overlay');
    await modal.waitFor({ state: 'visible', timeout: TIMEOUT.modal });
    await modal.locator('.modal-input').fill(ZAI_API_KEY);
    await modal.locator('.modal-btn-confirm').click();
    await modal.waitFor({ state: 'hidden', timeout: TIMEOUT.modal });

    // "YOUR KEY" badge should still be visible
    await expect(zaiRow.getByText('YOUR KEY')).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── REMOVE KEY ───────────────────────────────────────────────────────

  test('Step 4: Remove API key and verify user key is gone', async () => {
    const zaiRow = page.locator('.px-5.py-3').filter({
      has: page.locator('.text-zinc-300', { hasText: /^Zai$/ }),
    });

    // Click "Remove" button (visible only when has_user_key is true)
    await zaiRow.getByRole('button', { name: 'Remove' }).click();

    // Confirm the danger modal
    await confirmDangerModal(page);

    // "YOUR KEY" should be gone — badge should revert to "NOT SET" or "SHARED"
    await expect(zaiRow.getByText('YOUR KEY')).not.toBeVisible({ timeout: TIMEOUT.modal });

    // "Remove" button should no longer be visible
    await expect(
      zaiRow.getByRole('button', { name: 'Remove' }),
    ).not.toBeVisible();

    // Button should be back to "Set Key" (not "Update")
    await expect(zaiRow.getByRole('button', { name: 'Set Key' })).toBeVisible();
  });
});
