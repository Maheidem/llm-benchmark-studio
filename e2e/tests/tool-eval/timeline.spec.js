/**
 * @regression Tool Eval — Timeline View E2E Test
 *
 * User journey:
 *   1. Create a suite with tool + test case, run a quick eval.
 *   2. Create an experiment via API, pin the eval run as baseline.
 *   3. Navigate to Timeline, select experiment, verify baseline + entries.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * Uses Zai provider with GLM-4.5-Air for real LLM calls.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-timeline');
const SUITE_NAME = 'Timeline Test Suite';

test.describe('@regression Tool Eval — Timeline View', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(180_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(120_000);
    context = await browser.newContext();
    page = await context.newPage();

    // Register a fresh user
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Setup Zai provider with GLM-4.5-Air
    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);

    // Create suite with tool + test case, then run eval
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase(SUITE_NAME);
    await ss.runQuickEval(SUITE_NAME, 'GLM-4.5-Air');

    // Create an experiment linked to the suite + pin eval as baseline via API.
    // Then re-run eval linked to the experiment to create a timeline entry.
    await page.evaluate(async (suiteName) => {
      const token = localStorage.getItem('auth_token');
      const headers = {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      };

      // Get eval history to find the run we just completed
      const histRes = await fetch('/api/tool-eval/history', { headers });
      const histData = await histRes.json();
      const evalRun = histData.runs.find(r => r.suite_name === suiteName);
      if (!evalRun) throw new Error('Eval run not found in history');

      // Extract actual model info from the eval run's models array
      const models = evalRun.models || [];
      if (models.length === 0) throw new Error('No models found in eval run');

      // Create experiment linked to the suite with eval as baseline
      const expRes = await fetch('/api/experiments', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          name: suiteName + ' Experiment',
          suite_id: evalRun.suite_id,
          baseline_eval_id: evalRun.id,
        }),
      });
      if (!expRes.ok) {
        const errText = await expRes.text();
        throw new Error('Failed to create experiment: ' + errText);
      }

      const expData = await expRes.json();
      const expId = expData.experiment_id;

      // Re-run eval with experiment_id using the correct model_id from the eval run
      const evalRes = await fetch('/api/tool-eval', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          suite_id: evalRun.suite_id,
          models: models,
          temperature: 0,
          experiment_id: expId,
        }),
      });
      if (!evalRes.ok) {
        const errText = await evalRes.text();
        throw new Error('Failed to start linked eval: ' + errText);
      }

      window.__timelineExpId = expId;
    }, SUITE_NAME);

    // Wait for the linked eval job to complete
    await page.waitForTimeout(30_000);
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── STEP 1: NAVIGATE TO TIMELINE ──────────────────────────────────

  test('Step 1: Navigate to Timeline subtab', async () => {
    await page.getByRole('link', { name: 'Tool Eval' }).click();
    await page.waitForURL('**/tool-eval/**', { timeout: TIMEOUT.nav });

    await page.locator('.te-subtab').filter({ hasText: 'Timeline' }).click();
    await page.waitForURL('**/tool-eval/timeline', { timeout: TIMEOUT.nav });

    await expect(
      page.locator('h2').filter({ hasText: 'Experiment Timeline' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── STEP 2: SELECT EXPERIMENT ─────────────────────────────────────

  test('Step 2: Select experiment from dropdown', async () => {
    const select = page.locator('select').first();
    await select.waitFor({ state: 'visible', timeout: TIMEOUT.nav });

    // Wait for at least one option beyond the placeholder
    await expect(
      select.locator('option:not([value=""])'),
    ).not.toHaveCount(0, { timeout: TIMEOUT.fetch });

    // Select the first real option (our experiment)
    const option = select.locator('option:not([value=""])').first();
    const optionValue = await option.getAttribute('value');
    await select.selectOption(optionValue);

    // Wait for timeline content to load after selection
    await page.waitForTimeout(2_000);
  });

  // ─── STEP 3: VERIFY BASELINE INFO ─────────────────────────────────

  test('Step 3: Verify baseline info card', async () => {
    // "Baseline" label should be visible in the baseline info card
    await expect(page.getByText('Baseline')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // A score percentage should be visible (e.g. "100.0%" or "50.0%")
    await expect(page.locator('text=/\\d+\\.\\d+%/').first()).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── STEP 4: VERIFY TIMELINE ENTRIES ───────────────────────────────

  test('Step 4: Verify timeline entries exist', async () => {
    // At least one timeline entry should be visible after loading.
    // Entries have type labels like "Eval" and are grouped by date ("Today").
    await expect(page.getByText('Today')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Verify the eval entry exists with a score percentage
    await expect(page.getByText('Eval').first()).toBeVisible({
      timeout: TIMEOUT.nav,
    });
    await expect(page.getByText(/\d+\.\d+%/).first()).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });
});
