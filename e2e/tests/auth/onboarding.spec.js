/**
 * @smoke Auth — Registration & Landing Page E2E Test
 *
 * User journeys for the landing page and registration flow:
 *   1. Landing page shows hero section with feature cards
 *   2. "Sign Up" button opens auth modal in register mode
 *   3. "Login" button (topbar) opens auth modal in login mode
 *   4. Register a fresh user → redirected to benchmark
 *   5. "Get Started Free" button opens auth modal
 *   6. OnboardingWizard shows for fresh user after registration
 *   7. Complete onboarding via wizard → wizard hides
 *
 * Self-contained: registers its own user.
 * No LLM calls — purely UI interaction.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

test.describe('@smoke Auth — Registration & Landing Page', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(60_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();
    await page.goto('/login');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── LANDING PAGE HERO ──────────────────────────────────────────────

  test('Step 1: Landing page shows hero section', async () => {
    await expect(page.getByRole('heading', { name: 'BENCHMARK STUDIO' })).toBeVisible({
      timeout: TIMEOUT.nav,
    });
    await expect(page.getByText('Measure · Compare · Optimize')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Get Started Free' })).toBeVisible();
  });

  // ─── FEATURE CARDS ──────────────────────────────────────────────────

  test('Step 2: Landing page shows feature cards', async () => {
    await expect(page.getByText('Real-Time Benchmarks')).toBeVisible();
    await expect(page.getByText('Tool Calling Eval')).toBeVisible();
    await expect(page.getByText('Analytics Dashboard')).toBeVisible();
    await expect(page.getByText('Scheduled Runs')).toBeVisible();
  });

  // ─── SIGN UP BUTTON OPENS MODAL ────────────────────────────────────

  test('Step 3: "Sign Up" button opens auth modal in register mode', async () => {
    await page.getByRole('button', { name: 'Sign Up' }).click();

    // Modal should appear
    const overlay = page.locator('.modal-overlay');
    await expect(overlay).toBeVisible({ timeout: TIMEOUT.modal });

    // Register tab should be active (submit button says "Create Account")
    await expect(overlay.locator('button[type="submit"]')).toContainText('Create Account');

    // Close modal by clicking overlay
    await overlay.click({ position: { x: 5, y: 5 } });
    await expect(overlay).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── LOGIN BUTTON OPENS MODAL ──────────────────────────────────────

  test('Step 4: Topbar "Login" button opens auth modal in login mode', async () => {
    await page.locator('.landing-topbar').getByRole('button', { name: 'Login' }).click();

    // Modal should appear
    const overlay = page.locator('.modal-overlay');
    await expect(overlay).toBeVisible({ timeout: TIMEOUT.modal });

    // Login tab should be active (submit button says "Login")
    await expect(overlay.locator('button[type="submit"]')).toContainText('Login');

    // Close modal
    await overlay.click({ position: { x: 5, y: 5 } });
    await expect(overlay).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── GET STARTED FREE OPENS MODAL ──────────────────────────────────

  test('Step 5: "Get Started Free" button opens auth modal', async () => {
    await page.getByRole('button', { name: 'Get Started Free' }).click();

    const overlay = page.locator('.modal-overlay');
    await expect(overlay).toBeVisible({ timeout: TIMEOUT.modal });

    // Close modal
    await overlay.click({ position: { x: 5, y: 5 } });
    await expect(overlay).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── REGISTER FRESH USER ───────────────────────────────────────────

  test('Step 6: Register fresh user → redirected to benchmark', async () => {
    const email = uniqueEmail('e2e-onboarding');
    const auth = new AuthModal(page);

    await auth.register(email, TEST_PASSWORD, { dismissOnboarding: false });
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Should be on benchmark page
    await expect(page).toHaveURL(/\/benchmark/);

    // Header should show user email
    await expect(page.getByText(email)).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── ONBOARDING WIZARD SHOWS ──────────────────────────────────────

  test('Step 7: OnboardingWizard shows for fresh user', async () => {
    // The onboarding wizard renders as a fixed full-screen overlay
    // It should appear automatically after registration + redirect
    const wizardHeading = page.getByRole('heading', { name: 'Welcome to Benchmark Studio!' });
    await expect(wizardHeading).toBeVisible({ timeout: TIMEOUT.nav });

    // Step 1 of 3 should be visible
    await expect(page.getByText('Step 1 of 3: Choose Your Provider')).toBeVisible();

    // Provider options should be shown
    await expect(page.getByText('OpenAI')).toBeVisible();
    await expect(page.getByText('Anthropic')).toBeVisible();
    await expect(page.getByText('Google Gemini')).toBeVisible();
  });

  // ─── COMPLETE ONBOARDING ──────────────────────────────────────────

  test('Step 8: Complete onboarding via wizard → wizard hides', async () => {
    // Click "Skip All" to complete onboarding quickly
    await page.getByRole('button', { name: 'Skip All' }).click();

    // Wizard should disappear
    const wizardHeading = page.getByRole('heading', { name: 'Welcome to Benchmark Studio!' });
    await expect(wizardHeading).not.toBeVisible({ timeout: TIMEOUT.nav });

    // Verify onboarding status is now completed via API
    const status = await page.evaluate(async () => {
      const token = localStorage.getItem('auth_token');
      const res = await fetch('/api/onboarding/status', {
        headers: { 'Authorization': 'Bearer ' + token },
      });
      return res.json();
    });
    expect(status.completed).toBe(true);
  });
});
