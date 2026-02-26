/**
 * @critical Tool Eval — Judge Reports E2E Test
 *
 * Full user journey:
 *   1. Register user, set up Zai provider with GLM-4.5-Air.
 *   2. Configure judge settings (auto-judge + post_eval mode) BEFORE running eval.
 *   3. Create suite with tool + test case, run eval (auto-triggers judge).
 *   4. Navigate to Judge subtab, verify notification, wait for report.
 *   5. Verify report table, click row for detail modal, test compare mode.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * Uses Zai provider with GLM-4.5-Air for real LLM calls.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT, dismissOnboarding } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-judge-reports');

test.describe('@critical Tool Eval — Judge Reports', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(240_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    // Extend beforeAll timeout — register + setup + eval + judge trigger takes > 30s
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

    // Create suite with tool + test case
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('Judge Test Suite');

    // Run eval first
    await ss.runQuickEval('Judge Test Suite', 'GLM-4.5-Air');

    // Wait for eval job to fully complete in registry (avoids judge being queued)
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

    // Trigger judge manually via API (auto-judge is request-body-driven, not settings-driven)
    await page.evaluate(async () => {
      const token = localStorage.getItem('auth_token');
      const headers = {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      };

      // Get the most recent eval run
      const histRes = await fetch('/api/tool-eval/history', { headers });
      const histData = await histRes.json();
      const evalRun = histData.runs[0];
      if (!evalRun) throw new Error('No eval run found');

      // Get config to find the correct model compound key for judging
      const configRes = await fetch('/api/config', { headers });
      const configData = await configRes.json();
      // Find first available model (use Zai GLM-4.5-Air)
      let judgeModel = '';
      let judgeProviderKey = '';
      for (const [provName, provData] of Object.entries(configData.providers || {})) {
        const pk = provData.provider_key || provName;
        for (const m of (provData.models || [])) {
          if (m.model_id && m.model_id.includes('glm')) {
            judgeModel = m.model_id;
            judgeProviderKey = pk;
            break;
          }
        }
        if (judgeModel) break;
      }
      if (!judgeModel) throw new Error('No judge model found in config');

      // Trigger judge via API
      const judgeRes = await fetch('/api/tool-eval/judge', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          eval_run_id: evalRun.id,
          judge_model: judgeModel,
          judge_provider_key: judgeProviderKey,
          mode: 'post_eval',
        }),
      });
      if (!judgeRes.ok) {
        const errText = await judgeRes.text();
        throw new Error('Failed to start judge: ' + errText);
      }
    });

    // Poll for judge completion instead of fixed wait (more reliable)
    for (let i = 0; i < 12; i++) {
      const done = await page.evaluate(async () => {
        const token = localStorage.getItem('auth_token');
        const res = await fetch('/api/tool-eval/judge/reports', {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return false;
        const data = await res.json();
        return (data.reports || []).some(r => r.status === 'completed');
      });
      if (done) break;
      await page.waitForTimeout(5_000);
    }
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── NAVIGATE TO JUDGE SUBTAB ────────────────────────────────────────

  test('Step 1: Navigate to Judge subtab', async () => {
    await page.locator('.te-subtab').filter({ hasText: 'Judge' }).click();
    await page.waitForURL('**/tool-eval/judge', { timeout: TIMEOUT.nav });

    await expect(page.getByRole('heading', { name: 'Judge Reports' })).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── NOTIFICATION: Check for judge job ───────────────────────────────

  test('Step 2: Notification — check for judge job', async () => {
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Judge notification (⚖️ icon) — may be running or already done
    const judgeNotif = page.locator('.notif-item').filter({
      has: page.locator('.notif-icon.judge'),
    });
    await expect(judgeNotif.first()).toBeVisible({ timeout: TIMEOUT.nav });

    // Close dropdown
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).not.toBeVisible();
  });

  // ─── WAIT FOR JUDGE REPORT ───────────────────────────────────────────

  test('Step 3: Wait for judge report to complete', async () => {
    // Judge makes LLM calls — poll by reloading until "completed" appears
    const completedRow = page.locator('.results-table tbody tr').filter({ hasText: 'completed' });
    for (let attempt = 0; attempt < 18; attempt++) {
      if ((await completedRow.count()) > 0) break;
      await page.waitForTimeout(10_000);
      await page.reload();
      await page.waitForLoadState('networkidle');
      await dismissOnboarding(page);
    }
    await expect(completedRow.first()).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── VERIFY REPORT TABLE STRUCTURE ───────────────────────────────────

  test('Step 4: Verify report table structure', async () => {
    // Verify expected headers are present
    const headers = page.locator('.results-table th');
    await expect(headers.filter({ hasText: 'Date' })).toBeVisible();
    await expect(headers.filter({ hasText: 'Grade' })).toBeVisible();
    await expect(headers.filter({ hasText: 'Score' })).toBeVisible();
    await expect(headers.filter({ hasText: 'Status' })).toBeVisible();

    // At least 1 completed row
    const completedRow = page.locator('.results-table tbody tr').filter({ hasText: 'completed' }).first();
    await expect(completedRow).toBeVisible();

    // Grade should be present in the row (A-F with optional +/-, or "?" if LLM parse failed)
    await expect(completedRow.locator('td').filter({ hasText: /^[A-F][+-]?$|^\?$/ })).toBeVisible();
  });

  // ─── CLICK ROW → DETAIL MODAL ────────────────────────────────────────

  test('Step 5: Click row to open detail modal', async () => {
    // Click first table row
    await page.locator('.results-table tbody tr').first().click();

    // Modal/overlay should appear
    const modal = page.locator('.fixed.inset-0').filter({
      has: page.locator('.card'),
    });
    await expect(modal).toBeVisible({ timeout: TIMEOUT.modal });

    // Verify some content loaded in the modal (section label or detail text)
    await expect(modal.locator('.section-label').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Close modal — click outside the card area or find a close button
    const closeBtn = modal.locator('button').filter({ hasText: /Close|X|\u00d7/ });
    const hasCloseBtn = (await closeBtn.count()) > 0;
    if (hasCloseBtn) {
      await closeBtn.first().click();
    } else {
      // Click the overlay backdrop to close
      await modal.click({ position: { x: 5, y: 5 } });
    }

    // Modal should be hidden
    await expect(modal).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── COMPARE MODE TOGGLE ─────────────────────────────────────────────

  test('Step 6: Test compare mode toggle', async () => {
    // Click "Compare" button to enter compare mode
    await page.getByRole('button', { name: 'Compare' }).click();

    // Checkbox column should appear in the table
    const checkboxes = page.locator('.results-table input[type="checkbox"]');
    await expect(checkboxes.first()).toBeVisible({ timeout: TIMEOUT.modal });

    // "Compare (0/2)" counter button should be visible (disabled until 2 selected)
    await expect(page.getByText(/Compare \(\d+\/\d+\)/)).toBeVisible();

    // Click "Cancel" to exit compare mode (toggle button text changes to "Cancel")
    await page.getByRole('button', { name: 'Cancel' }).click();

    // Checkbox column should disappear
    await expect(checkboxes.first()).not.toBeVisible({ timeout: TIMEOUT.modal });
  });
});
