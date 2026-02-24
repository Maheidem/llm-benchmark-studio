/**
 * @smoke Schedule CRUD Test Suite
 *
 * Deterministic browser tests for schedule create, read, toggle, delete flows.
 * Does NOT trigger any benchmark runs — purely tests the CRUD UI.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { confirmDangerModal } = require('../../helpers/modals');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-sched-crud');

test.describe('@smoke Schedule CRUD', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(50_000);

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

    // Setup Zai provider with GLM-4.5-Air (needed for model selection in schedule modal)
    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── NAVIGATE TO SCHEDULES ─────────────────────────────────────────

  test('Step 1: Navigate to /schedules and verify heading', async () => {
    await page.getByRole('link', { name: 'Schedules' }).click();
    await page.waitForURL('**/schedules', { timeout: TIMEOUT.nav });

    await expect(
      page.locator('h1').filter({ hasText: 'Schedules' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── CREATE SCHEDULE ───────────────────────────────────────────────

  test('Step 2: Create a new schedule via modal', async () => {
    // Click "New Schedule"
    await page.getByRole('button', { name: 'New Schedule' }).click();

    // Modal should appear
    const modal = page.locator('.modal-overlay');
    await modal.waitFor({ state: 'visible', timeout: TIMEOUT.modal });

    // Fill schedule name
    const nameInput = modal.locator('input.modal-input[placeholder="e.g. Daily throughput check"]');
    await nameInput.fill('E2E Test Schedule');

    // Leave interval at default (Every day)

    // Select a model: click GLM-4.5-Air model card in the modal
    const modelCard = modal
      .locator('.model-card')
      .filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    // Click "Create Schedule"
    await modal.locator('.modal-btn-confirm').click();

    // Modal should close
    await modal.waitFor({ state: 'hidden', timeout: TIMEOUT.modal });
  });

  // ─── VERIFY SCHEDULE IN TABLE ──────────────────────────────────────

  test('Step 3: Verify schedule appears in table', async () => {
    // Schedule name should appear in the table
    await expect(
      page.getByText('E2E Test Schedule'),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Verify the row has interval info
    await expect(
      page.getByText('Every day'),
    ).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── TOGGLE ENABLE/DISABLE ─────────────────────────────────────────

  test('Step 4: Toggle enable/disable checkbox', async () => {
    // Find the row containing our schedule
    const row = page.locator('tr').filter({ hasText: 'E2E Test Schedule' });
    await expect(row).toBeVisible({ timeout: TIMEOUT.modal });

    // Find the toggle checkbox (sr-only input[type="checkbox"] inside the row)
    const checkbox = row.locator('input[type="checkbox"]');
    await expect(checkbox).toBeAttached();

    // Get initial state
    const initialChecked = await checkbox.isChecked();

    // Toggle it
    await checkbox.click({ force: true });
    await page.waitForTimeout(500);

    // Verify state changed
    const afterChecked = await checkbox.isChecked();
    expect(afterChecked).not.toBe(initialChecked);

    // Toggle back
    await checkbox.click({ force: true });
    await page.waitForTimeout(500);

    // Verify state restored
    const restoredChecked = await checkbox.isChecked();
    expect(restoredChecked).toBe(initialChecked);
  });

  // ─── DELETE SCHEDULE ───────────────────────────────────────────────

  test('Step 5: Delete schedule and verify removal', async () => {
    // Find the row containing our schedule
    const row = page.locator('tr').filter({ hasText: 'E2E Test Schedule' });
    await expect(row).toBeVisible({ timeout: TIMEOUT.modal });

    // Click "Del" button
    await row.getByRole('button', { name: 'Del' }).click();

    // Confirm danger modal
    await confirmDangerModal(page);

    // Verify schedule removed from table
    await expect(
      page.getByText('E2E Test Schedule'),
    ).not.toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── VERIFY EMPTY STATE ────────────────────────────────────────────

  test('Step 6: Verify empty state', async () => {
    await expect(
      page.getByText('No schedules yet'),
    ).toBeVisible({ timeout: TIMEOUT.modal });
  });
});
