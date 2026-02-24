/**
 * @regression Settings — Tuning Panel E2E Test
 *
 * User journeys for the Tuning settings panel:
 *   1. Navigate to Settings > Tuning tab
 *   2. Verify Parameter Tuner Defaults section opens by default
 *   3. Modify Max Combinations and verify save feedback
 *   4. Open Prompt Tuner Defaults section and change mode
 *   5. Initialize Parameter Support and verify provider table
 *   6. Switch provider in param support dropdown
 *
 * Self-contained: registers its own user.
 * No LLM calls — purely UI interaction.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-settings-adv');

test.describe('@regression Settings — Tuning Panel', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(60_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    // Dismiss onboarding if visible
    const skipBtn = page.getByRole('button', { name: 'Skip All' });
    if (await skipBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await skipBtn.click();
    }
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── NAVIGATE TO TUNING TAB ─────────────────────────────────────────

  test('Step 1: Navigate to Settings > Tuning', async () => {
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.waitForURL('**/settings/**', { timeout: TIMEOUT.nav });

    // Click Tuning tab
    await page.getByRole('link', { name: 'Tuning' }).click();
    await page.waitForURL('**/settings/tuning', { timeout: TIMEOUT.nav });

    // Verify Parameter Tuner Defaults section is visible (open by default)
    await expect(page.getByText('Parameter Tuner Defaults')).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // ─── PARAM TUNER DEFAULTS ──────────────────────────────────────────

  test('Step 2: Verify and modify Parameter Tuner Defaults', async () => {
    // Max Combinations input should be visible
    const maxCombInput = page.locator('input[type="number"]').first();
    await expect(maxCombInput).toBeVisible();

    // Verify default value is 50
    await expect(maxCombInput).toHaveValue('50');

    // Change to 25
    await maxCombInput.fill('25');
    await maxCombInput.blur();

    // Wait for debounced save
    await page.waitForTimeout(1_000);

    // Save feedback should appear
    await expect(page.getByText('Saved')).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── TEMPERATURE RANGE ──────────────────────────────────────────────

  test('Step 3: Modify temperature range values', async () => {
    // Find temperature range section
    await expect(page.getByText('Temperature Range')).toBeVisible();

    // The temp range inputs: Min, Max, Step
    // They're in a grid under "Temperature Range" heading
    const tempSection = page.locator('.mt-4').filter({ hasText: 'Temperature Range' });
    const inputs = tempSection.locator('input[type="number"]');

    // Verify there are 3 inputs (Min, Max, Step)
    await expect(inputs).toHaveCount(3);

    // Change Min to 0.2
    await inputs.nth(0).fill('0.2');
    await inputs.nth(0).blur();

    await page.waitForTimeout(700);
    await expect(page.getByText('Saved')).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── PROMPT TUNER SECTION ──────────────────────────────────────────

  test('Step 4: Open Prompt Tuner Defaults and change mode', async () => {
    // Click to expand Prompt Tuner Defaults section
    await page.getByText('Prompt Tuner Defaults').click();

    // Mode dropdown should appear
    const modeSelect = page.locator('.settings-select').first();
    await expect(modeSelect).toBeVisible({ timeout: TIMEOUT.modal });

    // Change mode to "Thorough"
    await modeSelect.selectOption('thorough');

    // Wait for debounced save
    await page.waitForTimeout(1_000);

    // Save feedback
    await expect(page.getByText('Saved')).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── INITIALIZE PARAMETER SUPPORT ──────────────────────────────────

  test('Step 5: Initialize Parameter Support', async () => {
    // Click to expand Provider Parameter Support section
    await page.getByText('Provider Parameter Support').click();

    // "Initialize Parameter Support" button should appear
    const initBtn = page.getByRole('button', { name: 'Initialize Parameter Support' });
    await expect(initBtn).toBeVisible({ timeout: TIMEOUT.modal });

    // Click to seed param support
    await initBtn.click();

    // Wait for seeding to complete — a provider dropdown should appear
    await expect(page.locator('.settings-select').filter({ hasText: /openai|anthropic|google/i })).toBeVisible({
      timeout: TIMEOUT.fetch,
    });

    // A param table should appear with at least one row
    await expect(page.locator('table').last().locator('tbody tr').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── SWITCH PROVIDER ──────────────────────────────────────────────

  test('Step 6: Switch provider in param support dropdown', async () => {
    // The provider select is the one within the param support section
    const providerSelect = page.locator('.settings-select').last();
    const options = providerSelect.locator('option');

    // Should have multiple providers
    const count = await options.count();
    expect(count).toBeGreaterThan(1);

    // Select the second provider
    const secondValue = await options.nth(1).getAttribute('value');
    await providerSelect.selectOption(secondValue);

    // Table should update (provider name changes in header)
    await page.waitForTimeout(500);

    // Params table should still be visible
    await expect(page.locator('table').last()).toBeVisible();
  });
});
