/**
 * @critical Tool Eval — Judge Report Detail Modal
 *
 * Tests the JudgeHistory detail modal (JudgeReportView): overall verdict card,
 * per-model strengths/weaknesses expand, per-case verdicts toggle.
 *
 * Self-contained: registers its own user, sets up Zai, creates suite, runs eval,
 * then triggers judge via API (since auto-judge requires frontend to pass config
 * in eval request body, which EvaluateView doesn't currently do).
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT, dismissOnboarding } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-judge-detail');

test.describe('@critical Tool Eval — Judge Report Details', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(300_000);

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

    // Create suite + run eval
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('Judge Detail Suite');
    await ss.runQuickEval('Judge Detail Suite', 'GLM-4.5-Air');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── TRIGGER JUDGE + NAVIGATE TO TAB ───────────────────────────────

  test('Step 1: Trigger judge via API and wait for report', async () => {
    // 1. Get eval run ID from tool-eval history
    const evalRunId = await page.evaluate(async () => {
      const token = localStorage.getItem('auth_token');
      const res = await fetch('/api/tool-eval/history', {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      return data.runs?.[0]?.id;
    });
    expect(evalRunId).toBeTruthy();

    // Wait for the eval job to fully complete in the registry.
    // The eval job must be 'done' before the judge can start (per-user concurrency limit = 1).
    for (let i = 0; i < 24; i++) {
      const allDone = await page.evaluate(async () => {
        const token = localStorage.getItem('auth_token');
        const res = await fetch('/api/jobs', {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return false;
        const data = await res.json();
        const jobs = data.jobs || data || [];
        // Check that no tool_eval jobs are still running/queued
        return !jobs.some(j => j.job_type === 'tool_eval' && ['running', 'pending', 'queued'].includes(j.status));
      });
      if (allDone) break;
      await page.waitForTimeout(5_000);
    }

    // 2. Find the correct model_id from config
    const modelInfo = await page.evaluate(async () => {
      const token = localStorage.getItem('auth_token');
      const res = await fetch('/api/config', {
        headers: { Authorization: `Bearer ${token}` },
      });
      const config = await res.json();
      for (const [, prov] of Object.entries(config.providers || {})) {
        for (const m of prov.models || []) {
          const dn = (m.display_name || '').toLowerCase();
          const mid = (m.model_id || '').toLowerCase();
          if (dn.includes('glm') || mid.includes('glm')) {
            return { model_id: m.model_id, provider_key: prov.provider_key };
          }
        }
      }
      return null;
    });
    expect(modelInfo).toBeTruthy();

    // 3. Trigger judge POST
    const judgeResult = await page.evaluate(
      async ({ runId, modelId, providerKey }) => {
        const token = localStorage.getItem('auth_token');
        const res = await fetch('/api/tool-eval/judge', {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            eval_run_id: runId,
            judge_model: modelId,
            judge_provider_key: providerKey,
            mode: 'post_eval',
          }),
        });
        return { status: res.status, data: await res.json() };
      },
      { runId: evalRunId, modelId: modelInfo.model_id, providerKey: modelInfo.provider_key },
    );
    expect(judgeResult.status).toBe(200);

    // 4. Poll API for judge report completion (more reliable than UI polling)
    let completed = false;
    let lastDiag = '';
    for (let i = 0; i < 36; i++) {
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

        return {
          reportCount: reports.length,
          reportStatus: reports[0]?.status || 'N/A',
          judgeJobCount: judgeJobs.length,
          judgeJobStatus: judgeJobs[0]?.status || 'N/A',
          judgeJobError: judgeJobs[0]?.error_msg || '',
        };
      });
      lastDiag = JSON.stringify(diag);

      if (diag.reportStatus === 'completed') {
        completed = true;
        break;
      }
      // Also break if report exists with error status (don't poll forever)
      if (diag.reportStatus === 'error') break;
      await page.waitForTimeout(5_000);
    }
    // Include diagnostic info in the error message
    expect(completed, `Judge never completed. Last diagnostic: ${lastDiag}`).toBe(true);

    // Navigate to Judge tab to verify UI
    await page.goto('/tool-eval/judge', { waitUntil: 'networkidle' });
    await dismissOnboarding(page);

    // Verify table has the completed row with a grade (A-F or "?" if LLM parse failed)
    const table = page.locator('.results-table');
    await expect(table.locator('tbody tr').first()).toBeVisible();
  });

  // ─── CLICK ROW → DETAIL MODAL ──────────────────────────────────────

  test('Step 2: Click report row to open detail modal', async () => {
    const row = page.locator('.results-table tbody tr').first();
    await row.click();

    // Detail modal should open
    const modal = page.locator('.fixed.inset-0.z-50');
    await expect(modal).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── VERIFY OVERALL VERDICT CARD ──────────────────────────────────

  test('Step 3: Verify overall verdict card with grade and score', async () => {
    const modal = page.locator('.fixed.inset-0.z-50');

    // Should display a grade (A-F with optional +/-, or "?" if LLM parse failed)
    await expect(modal.getByText(/^[A-F][+-]?$|^\?$/).first()).toBeVisible();

    // Should show a score (/100 or just a number)
    await expect(modal.getByText(/\/100|\d+/).first()).toBeVisible();

    // Should mention the judge model
    await expect(modal.getByText(/GLM|glm/i).first()).toBeVisible();
  });

  // ─── VERIFY PER-MODEL ANALYSIS ──────────────────────────────────────

  test('Step 4: Verify per-model strengths and weaknesses', async () => {
    const modal = page.locator('.fixed.inset-0.z-50');

    // Should show strengths section (green + markers)
    const strengthsVisible = await modal
      .getByText(/strength/i)
      .first()
      .isVisible()
      .catch(() => false);

    // Should show weaknesses section (red - markers)
    const weaknessesVisible = await modal
      .getByText(/weakness/i)
      .first()
      .isVisible()
      .catch(() => false);

    // At least one should be visible for a completed report
    expect(strengthsVisible || weaknessesVisible).toBeTruthy();
  });

  // ─── EXPAND MODEL DETAILS ──────────────────────────────────────────

  test('Step 5: Expand model details for cross-case analysis', async () => {
    const modal = page.locator('.fixed.inset-0.z-50');

    // Find and click expand button (shows cross-case analysis + recommendations)
    const expandBtn = modal
      .locator('button')
      .filter({ hasText: /expand|more|detail/i })
      .first();
    const hasExpandBtn = await expandBtn.isVisible().catch(() => false);

    if (hasExpandBtn) {
      await expandBtn.click();
      await expect(modal.getByText(/recommend|analysis/i).first()).toBeVisible({
        timeout: TIMEOUT.modal,
      });
    }
  });

  // ─── TOGGLE PER-CASE VERDICTS ─────────────────────────────────────

  test('Step 6: Toggle per-case verdicts table', async () => {
    const modal = page.locator('.fixed.inset-0.z-50');

    // Find verdicts toggle button
    const verdictsBtn = modal
      .locator('button')
      .filter({ hasText: /verdict/i })
      .first();
    const hasVerdictsBtn = await verdictsBtn.isVisible().catch(() => false);

    if (hasVerdictsBtn) {
      await verdictsBtn.click();
      const verdictIndicator = modal.getByText(/pass|marginal|fail/i).first();
      await expect(verdictIndicator).toBeVisible({ timeout: TIMEOUT.modal });
    }
  });

  // ─── CLOSE MODAL ──────────────────────────────────────────────────

  test('Step 7: Close detail modal', async () => {
    const modal = page.locator('.fixed.inset-0.z-50');

    // Click close button
    const closeBtn = modal
      .locator('button')
      .filter({ hasText: /Close/i })
      .first();
    await closeBtn.click();

    // Modal should be hidden
    await expect(modal).not.toBeVisible({ timeout: TIMEOUT.modal });

    // Results table should still be visible
    await expect(page.locator('.results-table')).toBeVisible();
  });

  // ─── VERIFY TABLE STRUCTURE ───────────────────────────────────────

  test('Step 8: Verify judge table has expected columns', async () => {
    const table = page.locator('.results-table');

    // Should have these column headers
    await expect(table.locator('th').filter({ hasText: /Date/i })).toBeVisible();
    await expect(table.locator('th').filter({ hasText: /Grade/i })).toBeVisible();
    await expect(table.locator('th').filter({ hasText: /Score/i })).toBeVisible();
    await expect(table.locator('th').filter({ hasText: /Status/i })).toBeVisible();
  });
});
