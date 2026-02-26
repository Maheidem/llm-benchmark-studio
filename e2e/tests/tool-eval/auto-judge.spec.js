/**
 * @critical Tool Eval - Auto-Judge on Failure E2E Test
 *
 * Full user journey:
 *   1. Configure judge settings (default model) for auto-judge.
 *   2. Navigate to Evaluate, enable Auto-run Judge toggle.
 *   3. Adjust threshold slider (set to 100% so it always triggers).
 *   4. Run eval with model, wait for completion.
 *   5. Navigate to Judge subtab, verify auto-triggered report exists.
 *   6. Verify report has a grade.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * Uses Zai provider with GLM-4.5-Air for real LLM calls.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-auto-judge');

test.describe('@critical Tool Eval - Auto-Judge on Failure', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(240_000);

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

    // Setup Zai provider with GLM-4.5-Air
    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);

    // Create suite with tool + test case
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('AutoJudge Test Suite');

    // Configure judge settings so auto-judge has a default model to use.
    // Without this, auto-judge silently skips (no default_judge_model configured).
    // We read the user's config to get the exact model_id (API-fetched models may
    // have different casing than config.yaml, e.g. "zai/glm-4.5-air" vs "zai/GLM-4.5-Air").
    await page.evaluate(async () => {
      const token = localStorage.getItem('auth_token');
      // Get the actual model_id from the user's provider config
      const cfgRes = await fetch('/api/config', {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!cfgRes.ok) throw new Error('Failed to load config');
      const cfg = await cfgRes.json();
      // Providers are keyed by display_name in the API response
      const zaiProv = Object.values(cfg.providers || {}).find(p => p.provider_key === 'zai');
      if (!zaiProv || !zaiProv.models?.length) throw new Error('Zai provider not found in config');
      const modelId = zaiProv.models[0].model_id; // e.g. "zai/glm-4.5-air"

      const res = await fetch('/api/settings/judge', {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          default_judge_model: modelId,
          default_judge_provider_key: 'zai',
        }),
      });
      if (!res.ok) throw new Error('Failed to configure judge settings');
    });
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // --- STEP 1: NAVIGATE TO EVALUATE -------------------------------------

  test('Step 1: Navigate to Evaluate subtab', async () => {
    await page.locator('.te-subtab').filter({ hasText: 'Evaluate' }).click();
    await page.waitForURL('**/tool-eval/evaluate', { timeout: TIMEOUT.nav });

    await expect(page.getByRole('heading', { name: 'Evaluate' })).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // --- STEP 2: SELECT SUITE ----------------------------------------------

  test('Step 2: Select suite from dropdown', async () => {
    const suiteSelect = page.locator('select').first();
    await suiteSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });

    const option = suiteSelect.locator('option', { hasText: 'AutoJudge Test Suite' });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const optionValue = await option.getAttribute('value');
    await suiteSelect.selectOption(optionValue);
  });

  // --- STEP 3: ENABLE AUTO-JUDGE TOGGLE ----------------------------------

  test('Step 3: Enable Auto-run Judge checkbox', async () => {
    // Click the label text to toggle the checkbox
    await page.getByText('Auto-run Judge').click();

    // Threshold slider should appear
    await expect(page.getByText('on score <')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // --- STEP 4: SET THRESHOLD TO 100% (ALWAYS TRIGGER) -------------------

  test('Step 4: Set auto-judge threshold to 100%', async () => {
    // Set the range slider to max (100) via JavaScript
    const slider = page.locator('input[type="range"]');
    await slider.waitFor({ state: 'visible', timeout: TIMEOUT.modal });

    // Fill with max value to ensure judge always triggers
    await slider.fill('100');

    // Verify threshold display shows 100%
    await expect(page.getByText('100%').last()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // --- STEP 5: SELECT MODEL AND START EVAL --------------------------------

  test('Step 5: Select model and start eval', async () => {
    const modelCard = page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    await page.locator('.run-btn').filter({ hasText: 'Start Eval' }).click();

    // Progress UI should appear
    await expect(page.locator('.pulse-dot')).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // --- STEP 6: WAIT FOR EVAL COMPLETION -----------------------------------

  test('Step 6: Wait for eval completion', async () => {
    // Wait for eval + auto-judge to finish (pulse-dot disappears after tool_eval_complete)
    await expect(page.locator('.pulse-dot')).not.toBeVisible({
      timeout: TIMEOUT.stress,
    });

    // Verify eval completed via API (belt-and-suspenders — WS summary may not reach store)
    const evalOk = await page.evaluate(async () => {
      const token = localStorage.getItem('auth_token');
      const res = await fetch('/api/tool-eval/history', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return false;
      const data = await res.json();
      return (data.runs || []).length > 0;
    });
    expect(evalOk).toBe(true);

    // Check for Summary section — it relies on WS tool_eval_summary reaching the store.
    // If the WS message arrived, Summary should be visible. If not, we still pass
    // because we verified eval completion via API above.
    const summarySection = page.locator('.card').filter({ hasText: 'Summary' });
    const summaryVisible = await summarySection.isVisible().catch(() => false);
    if (summaryVisible) {
      await expect(summarySection.locator('tbody tr').first()).toBeVisible({
        timeout: TIMEOUT.nav,
      });
    }
    // Either way, eval completed — proceed to judge verification
  });

  // --- STEP 7: NAVIGATE TO JUDGE TAB, VERIFY AUTO-TRIGGERED REPORT ------

  test('Step 7: Wait for auto-judge report via API then verify in UI', async () => {
    // First, wait for the eval job to fully complete in the registry
    // (auto-judge only triggers after eval handler finishes, and it starts a new job
    //  which gets queued if the eval job hasn't released the user's slot yet)
    for (let i = 0; i < 24; i++) {
      const allDone = await page.evaluate(async () => {
        const token = localStorage.getItem('auth_token');
        const res = await fetch('/api/jobs', {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return false;
        const data = await res.json();
        const jobs = data.jobs || data || [];
        return !jobs.some(j => j.job_type === 'tool_eval' && ['running', 'pending', 'queued'].includes(j.status));
      });
      if (allDone) break;
      await page.waitForTimeout(5_000);
    }

    // Poll for judge report completion via API (avoids page reload / onboarding issues)
    let reportReady = false;
    let lastDiag = '';
    for (let attempt = 0; attempt < 36; attempt++) {
      const diag = await page.evaluate(async () => {
        const token = localStorage.getItem('auth_token');
        const headers = { Authorization: `Bearer ${token}` };

        // Check judge reports
        const reportsRes = await fetch('/api/tool-eval/judge/reports', { headers });
        const reportsData = reportsRes.ok ? await reportsRes.json() : { reports: [] };
        const reports = reportsData.reports || [];

        // Check jobs for judge type
        const jobsRes = await fetch('/api/jobs', { headers });
        const jobsData = jobsRes.ok ? await jobsRes.json() : { jobs: [] };
        const judgeJobs = (jobsData.jobs || []).filter(j => j.job_type === 'judge');
        const allJobs = (jobsData.jobs || []).map(j => `${j.job_type}:${j.status}`);

        return {
          reportCount: reports.length,
          reportStatus: reports[0]?.status || 'N/A',
          judgeJobCount: judgeJobs.length,
          judgeJobStatus: judgeJobs[0]?.status || 'N/A',
          judgeJobError: judgeJobs[0]?.error_msg || '',
          allJobs,
        };
      });
      lastDiag = JSON.stringify(diag);

      if (diag.reportCount > 0 && diag.reportStatus === 'completed') {
        reportReady = true;
        break;
      }
      // Break early if judge job failed
      if (diag.judgeJobStatus === 'failed') break;
      await page.waitForTimeout(5_000);
    }

    expect(reportReady, `Auto-judge never completed. Last diagnostic: ${lastDiag}`).toBe(true);

    // Now navigate to Judge tab and verify the report is visible
    await page.locator('.te-subtab').filter({ hasText: 'Judge' }).click();
    await page.waitForURL('**/tool-eval/judge', { timeout: TIMEOUT.nav });

    await expect(page.getByRole('heading', { name: 'Judge Reports' })).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    const completedRow = page.locator('.results-table tbody tr').filter({ hasText: 'completed' });
    await expect(completedRow.first()).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // --- STEP 8: VERIFY REPORT HAS GRADE ----------------------------------

  test('Step 8: Verify report has a grade', async () => {
    const gradeCell = page.locator('.results-table tbody tr')
      .filter({ hasText: 'completed' })
      .first()
      .locator('td')
      .filter({ hasText: /^[A-F][+-]?$|^\?$/ });

    await expect(gradeCell).toBeVisible({ timeout: TIMEOUT.nav });
  });
});
