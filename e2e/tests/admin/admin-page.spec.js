/**
 * @regression Admin — Dashboard E2E Test
 *
 * User journeys for the admin dashboard:
 *   1. Register user and promote to admin via direct DB update
 *   2. Navigate to Admin page, verify heading and system health card
 *   3. Verify Active Jobs tab (default) shows table or empty state
 *   4. Switch to Users tab, verify user table with role/actions
 *   5. Click "Limits" on a user → rate limit modal opens
 *   6. Switch to Audit Log tab, verify filters and table
 *   7. Non-admin user sees "Access Denied"
 *
 * Self-contained: registers its own user, promotes via sqlite3.
 * No LLM calls — purely UI interaction.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');
const { execSync } = require('child_process');
const path = require('path');

const ADMIN_EMAIL = uniqueEmail('e2e-admin');
const NON_ADMIN_EMAIL = uniqueEmail('e2e-nonadmin');
const DB_PATH = path.resolve(__dirname, '../../../data/benchmark_studio.db');

test.describe('@regression Admin — Dashboard', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(60_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register the admin user
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(ADMIN_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Dismiss onboarding if visible
    const skipBtn = page.getByRole('button', { name: 'Skip All' });
    if (await skipBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await skipBtn.click();
    }

    // Promote user to admin via direct DB update (use .timeout to wait for lock release)
    execSync(`sqlite3 "${DB_PATH}" ".timeout 10000" "UPDATE users SET role='admin' WHERE email='${ADMIN_EMAIL}'"`, {
      timeout: 15_000,
    });

    // Refresh the page to pick up the new role (JWT will be refreshed)
    // Need to re-login to get a new JWT with admin role
    await page.evaluate(() => {
      localStorage.removeItem('auth_token');
      localStorage.removeItem('refresh_token');
    });
    await page.goto('/login');
    await auth.login(ADMIN_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── NAVIGATE TO ADMIN PAGE ─────────────────────────────────────────

  test('Step 1: Navigate to Admin page and verify heading', async () => {
    await page.getByRole('link', { name: 'Admin' }).click();
    await page.waitForURL('**/admin', { timeout: TIMEOUT.nav });

    await expect(page.getByText('Admin Dashboard')).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── SYSTEM HEALTH CARD ─────────────────────────────────────────────

  test('Step 2: Verify System Health card', async () => {
    await expect(page.getByRole('heading', { name: 'System Health' })).toBeVisible();

    // Health metrics should load
    await expect(page.getByText('DB Size')).toBeVisible({ timeout: TIMEOUT.fetch });
    await expect(page.getByText('Results Files')).toBeVisible();
    await expect(page.getByText('Uptime')).toBeVisible();
    await expect(page.locator('div').filter({ hasText: /^Active Jobs$/ })).toBeVisible();
    await expect(page.getByText('WS Clients')).toBeVisible();
  });

  // ─── ACTIVE JOBS TAB ───────────────────────────────────────────────

  test('Step 3: Verify Active Jobs tab (default)', async () => {
    // Active Jobs tab should be active by default
    const jobsTab = page.locator('button.tab').filter({ hasText: 'Active Jobs' });
    await expect(jobsTab).toBeVisible();

    // Should show either job table or "No active processes" message
    const noJobs = page.getByText('No active processes.');
    const jobsTable = page.locator('table.w-full').first();

    // Wait for loading to complete, then check content
    const hasNoJobs = await noJobs.isVisible().catch(() => false);
    const hasTable = await jobsTable.isVisible().catch(() => false);
    expect(hasNoJobs || hasTable).toBe(true);
  });

  // ─── USERS TAB ──────────────────────────────────────────────────────

  test('Step 4: Switch to Users tab and verify user table', async () => {
    await page.locator('button.tab').filter({ hasText: 'Users' }).click();

    // Users table should appear (first .results-table — Audit Log is the second, both in DOM via v-show)
    const usersTable = page.locator('table.results-table').first();
    await expect(usersTable).toBeVisible({ timeout: TIMEOUT.fetch });

    // Table should have expected columns
    await expect(usersTable.getByText('Email')).toBeVisible();
    await expect(usersTable.getByText('Role')).toBeVisible();
    await expect(usersTable.getByText('Actions')).toBeVisible();

    // Our admin user should be visible
    await expect(usersTable.getByText(ADMIN_EMAIL)).toBeVisible();

    // Admin user should have role dropdown showing "admin"
    const adminRow = usersTable.locator('tr').filter({ hasText: ADMIN_EMAIL });
    const roleSelect = adminRow.locator('select');
    await expect(roleSelect).toHaveValue('admin');

    // Admin's own role dropdown should be disabled (can't change own role)
    await expect(roleSelect).toBeDisabled();
  });

  // ─── RATE LIMIT MODAL ──────────────────────────────────────────────

  test('Step 5: Click Limits button → rate limit modal opens', async () => {
    const usersTable = page.locator('table.results-table').first();
    const adminRow = usersTable.locator('tr').filter({ hasText: ADMIN_EMAIL });

    // Click "Limits" button
    await adminRow.getByRole('button', { name: 'Limits' }).click();

    // Rate limit modal should appear
    const modal = page.locator('.modal-overlay');
    await expect(modal).toBeVisible({ timeout: TIMEOUT.modal });
    await expect(modal.getByText('Rate Limits')).toBeVisible();

    // Verify input fields are present
    await expect(modal.getByText('Benchmarks Per Hour')).toBeVisible();
    await expect(modal.getByText('Max Concurrent')).toBeVisible();
    await expect(modal.getByText('Max Runs Per Benchmark')).toBeVisible();

    // Verify default values
    const inputs = modal.locator('input[type="number"]');
    await expect(inputs).toHaveCount(3);

    // Close modal
    await modal.getByRole('button', { name: 'Cancel' }).click();
    await expect(modal).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── AUDIT LOG TAB ──────────────────────────────────────────────────

  test('Step 6: Switch to Audit Log tab and verify filters', async () => {
    await page.locator('button.tab').filter({ hasText: 'Audit Log' }).click();

    // Wait for audit log to load
    await page.waitForTimeout(1_000);

    // Filters should be visible — 3 select dropdowns
    const selects = page.locator('select');
    // There should be at least 3 selects (user, action, since)
    const selectCount = await selects.count();
    expect(selectCount).toBeGreaterThanOrEqual(3);

    // Verify filter dropdowns are present (options are not "visible" — they're inside <select>)
    await expect(page.locator('select').filter({ has: page.locator('option', { hasText: 'All Users' }) })).toBeVisible();
    await expect(page.locator('select').filter({ has: page.locator('option', { hasText: 'All Actions' }) })).toBeVisible();
    await expect(page.locator('select').filter({ has: page.locator('option', { hasText: 'All Time' }) })).toBeVisible();

    // Audit log table or "No audit entries" should be visible
    const noEntries = page.getByText('No audit entries found.');
    const auditTable = page.locator('table.results-table').last();
    const hasNoEntries = await noEntries.isVisible().catch(() => false);
    const hasTable = await auditTable.isVisible().catch(() => false);
    expect(hasNoEntries || hasTable).toBe(true);

    // If table is visible, verify column headers via th elements
    if (hasTable) {
      await expect(auditTable.locator('th').filter({ hasText: 'Time' })).toBeVisible();
      await expect(auditTable.locator('th').filter({ hasText: 'Action' })).toBeVisible();
    }
  });

  // ─── PAGINATION ─────────────────────────────────────────────────────

  test('Step 7: Verify audit log pagination controls', async () => {
    // Prev/Next buttons should exist
    await expect(page.getByRole('button', { name: 'Prev' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Next' })).toBeVisible();

    // Prev should be disabled on first page
    await expect(page.getByRole('button', { name: 'Prev' })).toBeDisabled();
  });

  // ─── NON-ADMIN ACCESS DENIED ───────────────────────────────────────

  test('Step 8: Non-admin user sees Access Denied', async () => {
    // Register a non-admin user in a new context
    const ctx2 = await page.context().browser().newContext();
    const page2 = await ctx2.newPage();

    const auth2 = new AuthModal(page2);
    await page2.goto('/login');
    await auth2.register(NON_ADMIN_EMAIL, TEST_PASSWORD);
    await page2.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Dismiss onboarding
    const skipBtn = page2.getByRole('button', { name: 'Skip All' });
    if (await skipBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await skipBtn.click();
    }

    // Try to navigate to admin page directly — router guard redirects to /benchmark
    await page2.goto('/admin');
    await page2.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Should be on benchmark page, NOT admin
    await expect(page2).toHaveURL(/\/benchmark/);

    await ctx2.close();
  });
});
