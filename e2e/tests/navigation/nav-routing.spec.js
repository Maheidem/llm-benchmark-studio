/**
 * @smoke Navigation & Routing Test Suite
 *
 * Deterministic browser tests for all top-level nav links, tool-eval subtabs,
 * and settings subtabs. Also verifies the Admin tab is hidden for regular users.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-nav');

test.describe('@smoke Navigation & Routing', () => {
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
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── DEFAULT ROUTE ──────────────────────────────────────────────────────

  test('Step 1: /benchmark loads as default after login', async () => {
    await expect(page).toHaveURL(/\/benchmark/);
    await expect(page.getByText('Select Models')).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── TOOL EVAL NAV ─────────────────────────────────────────────────────

  test('Step 2: Click "Tool Eval" nav link → /tool-eval', async () => {
    await page.locator('a.tab', { hasText: 'Tool Eval' }).click();
    await page.waitForURL('**/tool-eval**', { timeout: TIMEOUT.nav });
    await expect(page).toHaveURL(/\/tool-eval/);
  });

  // ─── TOOL EVAL SUBTABS ─────────────────────────────────────────────────

  test('Step 3: Click all tool-eval subtabs', async () => {
    const subtabs = [
      { label: 'Suites', path: '/tool-eval/suites' },
      { label: 'Evaluate', path: '/tool-eval/evaluate' },
      { label: 'Param Tuner', path: '/tool-eval/param-tuner' },
      { label: 'Prompt Tuner', path: '/tool-eval/prompt-tuner' },
      { label: 'Judge', path: '/tool-eval/judge' },
      { label: 'Timeline', path: '/tool-eval/timeline' },
      { label: 'History', path: '/tool-eval/history' },
    ];

    for (const { label, path } of subtabs) {
      await page.locator('.te-subtab', { hasText: label }).click();
      await page.waitForURL(`**${path}`, { timeout: TIMEOUT.nav });
      await expect(page).toHaveURL(new RegExp(path.replace(/\//g, '\\/')));
    }
  });

  // ─── HISTORY NAV ────────────────────────────────────────────────────────

  test('Step 4: Click "History" top nav → /history', async () => {
    await page.locator('a.tab', { hasText: 'History' }).click();
    await page.waitForURL('**/history', { timeout: TIMEOUT.nav });
    await expect(page).toHaveURL(/\/history/);
  });

  // ─── ANALYTICS NAV ──────────────────────────────────────────────────────

  test('Step 5: Click "Analytics" top nav → /analytics/leaderboard', async () => {
    await page.locator('a.tab', { hasText: 'Analytics' }).click();
    await page.waitForURL('**/analytics**', { timeout: TIMEOUT.nav });
    await expect(page).toHaveURL(/\/analytics/);
  });

  // ─── SCHEDULES NAV ──────────────────────────────────────────────────────

  test('Step 6: Click "Schedules" top nav → /schedules', async () => {
    await page.locator('a.tab', { hasText: 'Schedules' }).click();
    await page.waitForURL('**/schedules', { timeout: TIMEOUT.nav });
    await expect(page).toHaveURL(/\/schedules/);
  });

  // ─── SETTINGS NAV + SUBTABS ─────────────────────────────────────────────

  test('Step 7: Click "Settings" top nav → /settings/keys, then subtabs', async () => {
    // Click Settings in top nav
    await page.locator('a.tab', { hasText: 'Settings' }).click();
    await page.waitForURL('**/settings**', { timeout: TIMEOUT.nav });
    await expect(page).toHaveURL(/\/settings\/keys/);

    // Settings subtabs
    const settingsTabs = [
      { label: 'Providers', path: '/settings/providers' },
      { label: 'Judge', path: '/settings/judge' },
      { label: 'Tuning', path: '/settings/tuning' },
    ];

    for (const { label, path } of settingsTabs) {
      await page.locator('.settings-tab', { hasText: label }).click();
      await page.waitForURL(`**${path}`, { timeout: TIMEOUT.nav });
      await expect(page).toHaveURL(new RegExp(path.replace(/\//g, '\\/')));
    }
  });

  // ─── ADMIN TAB HIDDEN ──────────────────────────────────────────────────

  test('Step 8: Admin tab is NOT visible for regular user', async () => {
    const adminTab = page.locator('a.tab', { hasText: 'Admin' });
    await expect(adminTab).not.toBeVisible();
  });
});
