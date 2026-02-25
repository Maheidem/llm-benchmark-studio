/**
 * @critical Auto-Optimize — Full Run E2E Test
 *
 * Full user journey:
 *   1. Navigate to Tool Eval > Auto-Optimize subtab.
 *   2. Configure optimization — select suite, fill base prompt, select model, set params.
 *   3. Start auto-optimize run and verify progress UI appears.
 *   4. Wait for completion and verify results (best prompt card, score).
 *   5. Verify action buttons on best prompt card (Use This Prompt, Save to Library, New Run).
 *   6. Click "Use This Prompt" and verify toast notification.
 *   7. Click "New Run" to return to config form.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * Uses Zai provider with GLM-4.5-Air for real LLM calls.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-autoopt');

test.describe('@critical Auto-Optimize — Full Run', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(240_000);

  let context;
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });
    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('AutoOpt Suite');
  });

  test.afterAll(async () => { await context?.close(); });

  test('Step 1: Navigate to Auto-Optimize subtab', async () => {
    await page.getByRole('link', { name: 'Tool Eval' }).click();
    await page.waitForURL('**/tool-eval/**', { timeout: TIMEOUT.nav });
    const tab = page.locator('.te-subtab').filter({ hasText: /Auto.Optim/i });
    await expect(tab).toBeVisible({ timeout: TIMEOUT.nav });
    await tab.click();
    await page.waitForURL('**/tool-eval/auto-optimize', { timeout: TIMEOUT.nav });
    await expect(page.locator('h2').filter({ hasText: 'Auto-Optimize' })).toBeVisible({ timeout: TIMEOUT.nav });
  });

  test('Step 2: Configure optimization — suite, prompt, model, params', async () => {
    // Select suite
    const suiteSelect = page.locator('select').first();
    await suiteSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });
    const option = suiteSelect.locator('option', { hasText: 'AutoOpt Suite' });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const val = await option.getAttribute('value');
    await suiteSelect.selectOption(val);

    // Fill base prompt
    const textarea = page.locator('textarea').first();
    await textarea.fill('You are a helpful assistant that uses tools accurately.');

    // Select optimization model (GLM-4.5-Air) — wait for options to load
    const modelSelect = page.locator('select').nth(1);
    const glmOption = modelSelect.locator('option', { hasText: 'GLM-4.5-Air' });
    await expect(glmOption).toBeAttached({ timeout: TIMEOUT.nav });
    const modelVal = await glmOption.getAttribute('value');
    await modelSelect.selectOption(modelVal);

    // Set minimal params: max iterations=1, population=2
    const numberInputs = page.locator('input[type="number"]');
    await numberInputs.first().fill('1');   // max iterations
    await numberInputs.nth(1).fill('2');    // population size
  });

  test('Step 3: Start auto-optimize and verify progress', async () => {
    const startBtn = page.getByRole('button', { name: 'Start Auto-Optimize' });
    await startBtn.click();

    // Progress UI should appear (allow time for API call + job submission)
    await expect(page.locator('.pulse-dot')).toBeVisible({ timeout: TIMEOUT.fetch });
  });

  test('Step 4: Wait for completion and verify results', async () => {
    // Wait for pulse-dot to disappear (run complete) — long timeout for LLM calls
    await expect(page.locator('.pulse-dot')).not.toBeVisible({ timeout: 200_000 });

    // Best prompt card should be visible
    const bestCard = page.getByText(/Best Prompt/i).first();
    await expect(bestCard).toBeVisible({ timeout: TIMEOUT.nav });

    // Score should be visible (percentage)
    await expect(page.getByText(/%/).first()).toBeVisible({ timeout: TIMEOUT.nav });
  });

  test('Step 5: Verify action buttons on best prompt card', async () => {
    // "Use This Prompt" button
    const useBtn = page.locator('button').filter({ hasText: /Use This Prompt/i });
    await expect(useBtn).toBeVisible({ timeout: TIMEOUT.nav });

    // "Save to Library" or "View in Library" button
    const libraryBtn = page.locator('button, a').filter({ hasText: /Save to Library|View in Library/i }).first();
    await expect(libraryBtn).toBeVisible({ timeout: TIMEOUT.nav });

    // "New Run" button
    const newRunBtn = page.locator('button').filter({ hasText: /New Run/i });
    await expect(newRunBtn).toBeVisible({ timeout: TIMEOUT.nav });
  });

  test('Step 6: Click "Use This Prompt" and verify toast', async () => {
    const useBtn = page.locator('button').filter({ hasText: /Use This Prompt/i });
    await useBtn.click();

    // Toast should appear
    await expect(page.locator('.toast-success, [class*="toast"]').first()).toBeVisible({ timeout: TIMEOUT.modal });
  });

  test('Step 7: Click "New Run" to return to config form', async () => {
    const newRunBtn = page.locator('button').filter({ hasText: /New Run/i });
    await newRunBtn.click();

    // Config form should be visible again (suite selector)
    await expect(page.locator('select').first()).toBeVisible({ timeout: TIMEOUT.nav });

    // Start button should be present
    const startBtn = page.locator('button.run-btn, button').filter({ hasText: /Start|Optimize/i }).first();
    await expect(startBtn).toBeVisible({ timeout: TIMEOUT.nav });
  });
});
