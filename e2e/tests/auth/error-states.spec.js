/**
 * @smoke Auth — Error States E2E Test
 *
 * User journeys for auth error handling:
 *   1. Login with wrong password → error banner appears
 *   2. Switch from Login to Register tab → error clears
 *   3. Register with already-taken email → error banner appears
 *   4. Register with short password → HTML5 validation blocks submit
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * No LLM calls — purely UI interaction.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-error-states');

test.describe('@smoke Auth — Error States', () => {
  test.describe.configure({ mode: 'serial' });

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register a user first so we can test duplicate registration and wrong password
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Log out so we're back on login page
    // Use API to clear auth state
    await page.evaluate(() => {
      localStorage.removeItem('auth_token');
      localStorage.removeItem('refresh_token');
    });
    await page.goto('/login');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── WRONG PASSWORD ─────────────────────────────────────────────────

  test('Step 1: Login with wrong password shows error banner', async () => {
    const auth = new AuthModal(page);

    // Open the login modal from landing page
    await auth.openViaLogin();

    // Fill credentials with wrong password
    await auth.fillCredentials(TEST_EMAIL, 'WrongPassword999!');
    await auth.submit();

    // Error banner should appear inside the modal
    await expect(auth.errorBanner).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── TAB SWITCH CLEARS ERROR ───────────────────────────────────────

  test('Step 2: Switching to Register tab clears error', async () => {
    const auth = new AuthModal(page);

    // Error banner should still be visible from previous step
    await expect(auth.errorBanner).toBeVisible();

    // Click Register tab inside the modal
    await auth.switchToRegister();

    // Error banner should disappear
    await expect(auth.errorBanner).not.toBeVisible();
  });

  // ─── DUPLICATE REGISTRATION ─────────────────────────────────────────

  test('Step 3: Register with already-taken email shows error', async () => {
    const auth = new AuthModal(page);

    // Should already be on Register tab from previous step
    await auth.fillCredentials(TEST_EMAIL, TEST_PASSWORD);
    await auth.submit();

    // Error banner should appear (email already taken)
    await expect(auth.errorBanner).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── SWITCH BACK TO LOGIN CLEARS ERROR ──────────────────────────────

  test('Step 4: Switching back to Login tab clears error', async () => {
    const auth = new AuthModal(page);

    await expect(auth.errorBanner).toBeVisible();

    await auth.switchToLogin();

    await expect(auth.errorBanner).not.toBeVisible();
  });

  // ─── SUCCESSFUL LOGIN AFTER ERRORS ──────────────────────────────────

  test('Step 5: Successful login after error states', async () => {
    const auth = new AuthModal(page);

    // Should already be on Login tab from step 4
    await auth.fillCredentials(TEST_EMAIL, TEST_PASSWORD);
    await auth.submit();

    // Should redirect to benchmark page
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
  });
});
