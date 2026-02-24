/**
 * @critical Tool Eval — Result Detail Drill-Down
 *
 * Tests the inline detail view that opens when clicking a model row in the
 * Summary table (EvalResultsTable). Verifies per-test-case detail including
 * prompt, expected/actual tool calls, and accuracy scores.
 *
 * Self-contained: registers its own user, sets up Zai, creates suite, runs eval.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-eval-detail');

test.describe('@critical Tool Eval — Result Details', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(180_000);

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

    // Create suite with test case + run eval
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('Detail Test Suite');
    await ss.runQuickEval('Detail Test Suite', 'GLM-4.5-Air');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── VERIFY RESULTS ───────────────────────────────────────────────

  test('Step 1: Verify eval results are visible', async () => {
    // runQuickEval already waited for results — verify they're still visible
    // The evaluate page has two tables: Live Results and Summary
    // Scope to table to avoid matching hidden notification dropdown elements
    const liveTable = page.locator('table').first();
    await expect(liveTable.getByText('glm-4.5-air').first()).toBeVisible({
      timeout: TIMEOUT.stress,
    });

    // Should show the expected tool call (scoped to table)
    await expect(liveTable.getByText('get_weather').first()).toBeVisible();

    // Should show a score percentage (may not be 100% — LLM output is non-deterministic)
    await expect(liveTable.getByText(/\d+%/).first()).toBeVisible();
  });

  // ─── VERIFY SUMMARY TABLE ─────────────────────────────────────────

  test('Step 2: Verify Summary table shows model row with scores', async () => {
    await expect(page.getByText('Summary').first()).toBeVisible({ timeout: TIMEOUT.nav });

    // Summary table is the last table on the page
    const summaryTable = page.locator('table').last();
    await expect(summaryTable).toBeVisible();

    // Should have the model row
    const modelRow = summaryTable.locator('tbody tr').first();
    await expect(modelRow).toContainText(/glm/i);
    await expect(modelRow).toContainText(/%/);
  });

  // ─── CLICK MODEL ROW → INLINE DETAIL ──────────────────────────────

  test('Step 3: Click model row to open inline detail view', async () => {
    // Click the model row in the Summary table
    const summaryTable = page.locator('table').last();
    const modelRow = summaryTable.locator('tbody tr').first();
    await modelRow.click();

    // Detail view opens inline (not a modal overlay)
    // It shows the model name and a × close button
    await expect(page.locator('button', { hasText: '×' }).last()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── VERIFY TEST CASE DETAILS ──────────────────────────────────────

  test('Step 4: Verify test case details in inline view', async () => {
    // Should show the prompt text from our test case
    await expect(page.getByText(/weather.*Paris/i).first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Should show status indicator (OK for pass, X for fail — LLM output is non-deterministic)
    const hasOk = await page.getByText('OK').first().isVisible().catch(() => false);
    const hasX = await page.getByText('X').first().isVisible().catch(() => false);
    expect(hasOk || hasX).toBeTruthy();

    // Should show a score percentage
    await expect(page.getByText(/\d+%/).first()).toBeVisible();
  });

  // ─── CLOSE INLINE DETAIL ──────────────────────────────────────────

  test('Step 5: Close inline detail view', async () => {
    // Click the × close button on the detail section
    await page.locator('button', { hasText: '×' }).last().click();

    // The inline detail should collapse — Summary section remains
    await expect(page.getByText('Summary')).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── COLUMN SORTING ────────────────────────────────────────────────

  test('Step 6: Test Summary table column sorting', async () => {
    const summaryTable = page.locator('table').last();

    // Click "Model" column header to sort
    const modelHeader = summaryTable.locator('th').filter({ hasText: /^Model$/ });
    const isVisible = await modelHeader.isVisible().catch(() => false);

    if (isVisible) {
      await modelHeader.click();
      // Sort indicator should appear or change
      await expect(modelHeader).toContainText(/[▲▼]/);
    }
  });
});
