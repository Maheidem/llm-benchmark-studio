/**
 * @regression Settings — Model Profiles CRUD
 *
 * Tests the Profiles settings panel:
 *   1. Navigate to Settings > Profiles
 *   2. Verify empty state
 *   3. Create a profile
 *   4. Verify profile appears in list
 *   5. Delete profile
 *
 * Self-contained. Uses Zai for model list.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-profiles');

test.describe('@regression Settings — Model Profiles', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(60_000);

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

  // ─── NAVIGATE TO PROFILES ─────────────────────────────────────────

  test('Step 1: Navigate to Settings > Profiles', async () => {
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.waitForURL('**/settings/**', { timeout: TIMEOUT.nav });

    await page.getByRole('link', { name: 'Profiles' }).click();
    await page.waitForURL('**/settings/profiles', { timeout: TIMEOUT.nav });
  });

  // ─── VERIFY EMPTY STATE ───────────────────────────────────────────

  test('Step 2: Verify empty state or existing profiles', async () => {
    // Either "No profiles" message or existing profile cards
    const empty = page.getByText(/No profiles/i);
    const profileCard = page.locator('.card').first();
    await expect(empty.or(profileCard)).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── CREATE PROFILE ───────────────────────────────────────────────

  test('Step 3: Create a new profile', async () => {
    // Click "+ New Profile" button
    const newBtn = page.getByRole('button', { name: /New Profile|\+ New/i });
    await newBtn.click();

    // Modal should appear
    const modal = page.locator('.fixed.inset-0.z-50, .modal-overlay');
    await expect(modal).toBeVisible({ timeout: TIMEOUT.modal });

    // Select any available model from dropdown (first non-disabled option)
    const modelSelect = modal.locator('select').first();
    const realOption = modelSelect.locator('option:not([disabled])').first();
    await expect(realOption).toBeAttached({ timeout: TIMEOUT.fetch });
    const modelVal = await realOption.getAttribute('value');
    await modelSelect.selectOption(modelVal);

    // Fill name
    await modal.getByPlaceholder('e.g. High Accuracy').fill('E2E Test Profile');

    // Fill description
    await modal.getByPlaceholder('Optional description').fill('Profile created by E2E test');

    // Save
    await modal.getByRole('button', { name: 'Save' }).click();

    // Modal should close
    await expect(modal).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── VERIFY PROFILE EXISTS ────────────────────────────────────────

  test('Step 4: Verify profile appears in list', async () => {
    await expect(
      page.getByText('E2E Test Profile').first()
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── DELETE PROFILE ───────────────────────────────────────────────

  test('Step 5: Delete profile', async () => {
    // Find the profile row and click its "Delete" button
    const profileRow = page.locator('div.group').filter({ hasText: 'E2E Test Profile' }).first();
    await profileRow.getByRole('button', { name: 'Delete' }).click();

    // Custom confirmation dialog appears — click "Delete" button inside it
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: TIMEOUT.modal });
    await dialog.getByRole('button', { name: 'Delete' }).click();

    // Profile row should be removed
    await expect(profileRow).not.toBeVisible({ timeout: TIMEOUT.modal });
  });
});
