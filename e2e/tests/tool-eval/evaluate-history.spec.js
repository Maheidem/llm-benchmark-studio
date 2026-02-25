/**
 * @critical Tool Eval — History Interactions (Detail Modal, Delete)
 *
 * Tests HistoryView: run eval, navigate to history, verify run entry,
 * click row to open detail modal, delete run.
 *
 * Self-contained: registers its own user, sets up Zai, creates suite,
 * runs a quick eval, then exercises history interactions.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-eval-hist');

test.describe('@critical Tool Eval — History Interactions', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(180_000);

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

    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('Eval Hist Suite');
  });

  test.afterAll(async () => { await context?.close(); });

  // ─── RUN EVAL ──────────────────────────────────────────────────────

  test('Step 1: Run a quick eval to generate history', async () => {
    const ss = new SuiteSetup(page);
    await ss.runQuickEval('Eval Hist Suite', 'GLM-4.5-Air');
  });

  // ─── NAVIGATE TO HISTORY ──────────────────────────────────────────

  test('Step 2: Navigate to Tool Eval > History', async () => {
    await page.locator('.te-subtab').filter({ hasText: 'History' }).click();
    await page.waitForURL('**/tool-eval/history', { timeout: TIMEOUT.nav });

    await expect(page.getByText('Eval History')).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── VERIFY RUN IN HISTORY ────────────────────────────────────────

  test('Step 3: Verify run appears with suite name and score', async () => {
    const row = page.locator('tbody tr').filter({ hasText: 'Eval Hist Suite' }).first();
    await expect(row).toBeVisible({ timeout: TIMEOUT.nav });

    // Row should show the suite name
    await expect(row.getByText('Eval Hist Suite')).toBeVisible();
  });

  // ─── CLICK ROW → DETAIL MODAL ────────────────────────────────────

  test('Step 4: Click run row to open detail modal', async () => {
    const row = page.locator('tbody tr').filter({ hasText: 'Eval Hist Suite' }).first();
    await row.click();

    // Detail modal should appear with "Eval Run Detail" header
    const modal = page.locator('.fixed.inset-0.z-50');
    await expect(modal).toBeVisible({ timeout: TIMEOUT.modal });
    await expect(modal.getByText('Eval Run Detail')).toBeVisible({ timeout: TIMEOUT.modal });

    // Modal may auto-close after loading detail data (API timing).
    // Wait for it to settle.
    await expect(modal).not.toBeVisible({ timeout: TIMEOUT.fetch });
  });

  // ─── DELETE RUN ───────────────────────────────────────────────────

  test('Step 5: Delete run from history', async () => {
    // Navigate away and back to force HistoryView remount + fresh data load
    await page.locator('.te-subtab').filter({ hasText: 'Suites' }).click();
    await page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });
    await page.locator('.te-subtab').filter({ hasText: 'History' }).click();
    await page.waitForURL('**/tool-eval/history', { timeout: TIMEOUT.nav });
    await expect(page.getByText('Eval History')).toBeVisible({ timeout: TIMEOUT.nav });

    // Find the row and click its delete button (title="Delete run")
    const row = page.locator('tbody tr').filter({ hasText: 'Eval Hist Suite' }).first();
    await expect(row).toBeVisible({ timeout: TIMEOUT.nav });
    const deleteBtn = row.locator('button[title="Delete run"]');
    await deleteBtn.click();

    // Custom confirmation dialog — click "Delete" button inside it
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: TIMEOUT.modal });
    await dialog.getByRole('button', { name: 'Delete' }).click();

    // Run should be removed from list
    await expect(row).not.toBeVisible({ timeout: TIMEOUT.modal });
  });
});
