/**
 * @smoke Registration Test Suite
 *
 * Deterministic browser tests for user registration flow.
 * Covers: successful registration, duplicate email rejection.
 *
 * The test user credentials are saved to e2e/.auth/test-user.json
 * for reuse by other test suites.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD } = require('../../helpers/constants');
const fs = require('fs');
const path = require('path');

const AUTH_DIR = path.join(__dirname, '..', '..', '.auth');
const TEST_USER_FILE = path.join(AUTH_DIR, 'test-user.json');

// Unique email per test run to avoid collisions
const TEST_EMAIL = uniqueEmail('e2e');

test.describe('@smoke Registration', () => {
  test.describe.configure({ mode: 'serial' });

  test('registers a new user successfully', async ({ browser }) => {
    // Fresh context — no existing auth state
    const context = await browser.newContext();
    const page = await context.newPage();
    const auth = new AuthModal(page);

    // Navigate to login/landing page
    await page.goto('/login');

    // Open modal via "Sign Up", fill form, submit
    await auth.register(TEST_EMAIL, TEST_PASSWORD);

    // After successful registration, app redirects to /benchmark
    await page.waitForURL('**/benchmark', { timeout: 10_000 });
    await expect(page).toHaveURL(/\/benchmark/);

    // Save credentials for other test suites to reuse
    if (!fs.existsSync(AUTH_DIR)) {
      fs.mkdirSync(AUTH_DIR, { recursive: true });
    }

    const storageState = await context.storageState();
    fs.writeFileSync(
      TEST_USER_FILE,
      JSON.stringify(
        {
          email: TEST_EMAIL,
          password: TEST_PASSWORD,
          storageState,
          createdAt: new Date().toISOString(),
        },
        null,
        2,
      ),
    );

    await context.close();
  });

  test('rejects registration with duplicate email', async ({ browser }) => {
    // Fresh context — no existing auth state
    const context = await browser.newContext();
    const page = await context.newPage();
    const auth = new AuthModal(page);

    await page.goto('/login');

    // Try to register with the SAME email from the previous test
    await auth.register(TEST_EMAIL, TEST_PASSWORD);

    // Should show error banner with "Email already registered"
    const errorText = await auth.getErrorText();
    expect(errorText).toContain('Email already registered');

    // Should still be on /login — NOT redirected to /benchmark
    await expect(page).toHaveURL(/\/login/);

    await context.close();
  });
});
