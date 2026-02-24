/**
 * @critical Benchmark Cancel — Start + Cancel from Page
 *
 * Tests the benchmark cancellation flow: start a benchmark, verify running
 * progress UI, verify notification shows running state, cancel from page,
 * verify benchmark stops and notification reflects cancelled/done state.
 *
 * Self-contained: registers its own user, sets up Zai provider.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-bench-cancel');

test.describe('@critical Benchmark Cancel', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(120_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register user + setup Zai
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── START BENCHMARK ────────────────────────────────────────────────

  test('Step 1: Select model and start benchmark', async () => {
    await page.getByRole('link', { name: 'Benchmark' }).click();
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    await expect(page.getByText('Select Models')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Deselect all, then pick GLM-4.5-Air
    await page.getByRole('button', { name: 'Select None' }).click();
    const modelCard = page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    // Run benchmark
    await page.getByRole('button', { name: 'RUN BENCHMARK' }).click();

    // Verify progress UI appears — button changes to "RUNNING..."
    await expect(
      page.getByRole('button', { name: /RUNNING/i }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── VERIFY RUNNING NOTIFICATION ────────────────────────────────────

  test('Step 2: Open notification and verify running state', async () => {
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Verify running notification item
    const runningItem = page.locator('.notif-item').filter({ hasText: /Running/ });
    await expect(runningItem).toBeVisible({ timeout: TIMEOUT.modal });

    // Should show progress percentage
    await expect(runningItem).toContainText(/\d+%/);

    // Close dropdown
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).not.toBeVisible();
  });

  // ─── CANCEL BENCHMARK ──────────────────────────────────────────────

  test('Step 3: Cancel benchmark from page Cancel button', async () => {
    // The benchmark page shows a "Cancel" button below the progress area
    const cancelBtn = page.getByRole('button', { name: 'Cancel', exact: true });
    await expect(cancelBtn).toBeVisible({ timeout: TIMEOUT.modal });
    await cancelBtn.click();

    // After cancel, the "RUNNING..." button should change back to "RUN BENCHMARK"
    await expect(
      page.getByRole('button', { name: 'RUN BENCHMARK' }),
    ).toBeVisible({ timeout: TIMEOUT.benchmark });
  });

  // ─── VERIFY CANCELLED STATE ────────────────────────────────────────

  test('Step 4: Verify notification shows cancelled/done state', async () => {
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // The job should now show in the "Recent" section with Cancelled or Done status
    const statusBadge = page.locator('.notif-status-badge').filter({
      hasText: /Cancelled|Done/i,
    });
    await expect(statusBadge.first()).toBeVisible({ timeout: TIMEOUT.modal });

    // Close dropdown
    await page.locator('.notif-bell').click();
  });

  // ─── VERIFY BENCHMARK PAGE STATE ──────────────────────────────────

  test('Step 5: Verify benchmark page is ready for new run', async () => {
    // The RUN BENCHMARK button should be enabled
    await expect(
      page.getByRole('button', { name: 'RUN BENCHMARK' }),
    ).toBeEnabled({ timeout: TIMEOUT.modal });

    // Model card should still be selected
    const modelCard = page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' });
    await expect(modelCard).toHaveClass(/selected/);
  });
});
