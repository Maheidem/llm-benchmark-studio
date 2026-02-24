/**
 * @smoke Login / Logout Test Suite
 *
 * Deterministic browser tests for login and logout flows.
 * Covers: header shows user email, logout redirects, auth guard, login works.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-login');

test.describe('@smoke Login / Logout', () => {
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

  // ─── VERIFY HEADER STATE ───────────────────────────────────────────────

  test('Step 1: Header shows user email and Logout button', async () => {
    // User email displayed in a small mono span
    const emailSpan = page.locator('span.text-\\[11px\\].text-zinc-500.font-mono');
    await expect(emailSpan).toBeVisible({ timeout: TIMEOUT.nav });
    await expect(emailSpan).toHaveText(TEST_EMAIL);

    // Logout button visible
    const logoutBtn = page.getByRole('button', { name: 'Logout' });
    await expect(logoutBtn).toBeVisible();
  });

  // ─── LOGOUT ─────────────────────────────────────────────────────────────

  test('Step 2: Logout redirects to /login', async () => {
    await page.getByRole('button', { name: 'Logout' }).click();
    await page.waitForURL('**/login', { timeout: TIMEOUT.nav });
    await expect(page).toHaveURL(/\/login/);
  });

  // ─── AUTH GUARD ─────────────────────────────────────────────────────────

  test('Step 3: Auth guard redirects /benchmark to /login', async () => {
    await page.goto('/benchmark');
    await page.waitForURL('**/login', { timeout: TIMEOUT.nav });
    await expect(page).toHaveURL(/\/login/);
  });

  // ─── LOGIN ──────────────────────────────────────────────────────────────

  test('Step 4: Login redirects to /benchmark', async () => {
    const auth = new AuthModal(page);
    await auth.login(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    await expect(page).toHaveURL(/\/benchmark/);
  });
});
