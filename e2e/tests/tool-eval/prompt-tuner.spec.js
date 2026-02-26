/**
 * @critical Prompt Tuner E2E Test
 *
 * Full user journey:
 *   1. Create Zai provider, set API key, fetch models, add GLM-4.5-Air.
 *   2. Create a suite with tool + test case via SuiteSetup.
 *   3. Navigate to Prompt Tuner, configure Quick mode, select meta/target model.
 *   4. Run prompt tuning, verify progress UI + notifications + refresh persistence.
 *   5. Wait for completion, validate Best Prompt card, done notification, history page.
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * Uses Zai provider with GLM-4.5-Air for real LLM calls.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT, dismissOnboarding } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-prompt-tuner');

test.describe('@critical Prompt Tuner', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(180_000);

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

    // Setup Zai provider with GLM-4.5-Air
    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);

    // Create suite with tool + test case
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('PRT Test Suite');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // --- NAVIGATE TO PROMPT TUNER -------------------------------------------

  test('Step 1: Navigate to Prompt Tuner config', async () => {
    await page.locator('.te-subtab').filter({ hasText: 'Prompt Tuner' }).click();
    await page.waitForURL('**/tool-eval/prompt-tuner', { timeout: TIMEOUT.nav });

    await expect(page.locator('h2').filter({ hasText: 'Prompt Tuner' })).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // --- SELECT SUITE -------------------------------------------------------

  test('Step 2: Select suite', async () => {
    const suiteSelect = page.locator('select').first();
    await suiteSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });

    const option = suiteSelect.locator('option', { hasText: 'PRT Test Suite' });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const optionValue = await option.getAttribute('value');
    await suiteSelect.selectOption(optionValue);
  });

  // --- VERIFY QUICK MODE DEFAULT ------------------------------------------

  test('Step 3: Verify Quick mode default', async () => {
    // Both mode cards should be visible
    await expect(page.getByText('Quick')).toBeVisible({ timeout: TIMEOUT.nav });
    await expect(page.getByText('Evolutionary')).toBeVisible({ timeout: TIMEOUT.nav });

    // "Quick" card should have lime border (inline style: rgba(191,255,0,0.3))
    const quickCard = page.locator('div.bg-white\\/\\[0\\.04\\]').filter({ hasText: 'Quick' });
    await expect(quickCard).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // --- SELECT META MODEL --------------------------------------------------

  test('Step 4: Select meta model', async () => {
    const metaModelSelect = page.locator('select').nth(1);
    await metaModelSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });

    const option = metaModelSelect.locator('option', { hasText: 'GLM-4.5-Air' });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const optionValue = await option.getAttribute('value');
    await metaModelSelect.selectOption(optionValue);
  });

  // --- SELECT TARGET MODEL ------------------------------------------------

  test('Step 5: Select target model', async () => {
    const modelCard = page
      .locator('.model-card')
      .filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);
  });

  // --- SET POPULATION SIZE ------------------------------------------------

  test('Step 6: Set population size to 2', async () => {
    const popInput = page.locator('input[type="number"]').first();
    await popInput.waitFor({ state: 'visible', timeout: TIMEOUT.nav });
    await popInput.fill('2');
  });

  // --- VERIFY BASE PROMPT TEXTAREA ----------------------------------------

  test('Step 7: Verify base prompt textarea', async () => {
    const textarea = page.locator('textarea').filter({
      has: page.locator(':scope'),
    });
    // Find textarea with placeholder containing "helpful assistant"
    const basePrompt = page.locator('textarea[placeholder*="helpful assistant"]');
    const count = await basePrompt.count();

    if (count > 0) {
      await expect(basePrompt.first()).toBeVisible({ timeout: TIMEOUT.nav });
    } else {
      // Fallback: any textarea in the config section should have some default text
      const anyTextarea = page.locator('textarea').first();
      await expect(anyTextarea).toBeVisible({ timeout: TIMEOUT.nav });
      const value = await anyTextarea.inputValue();
      expect(value.length).toBeGreaterThan(0);
    }
  });

  // --- START PROMPT TUNING ------------------------------------------------

  test('Step 8: Start prompt tuning', async () => {
    await page.locator('.run-btn').filter({ hasText: 'Start Prompt Tuning' }).click();
    await page.waitForURL('**/tool-eval/prompt-tuner/run', { timeout: TIMEOUT.nav });
  });

  // --- VERIFY PROGRESS UI -------------------------------------------------

  test('Step 9: Verify progress UI', async () => {
    // Pulse dot should be visible during tuning
    await expect(page.locator('.pulse-dot')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // ETA appears after first prompt evaluation completes
    await expect(page.getByText(/~\d+[smh]\s+left/).first()).toBeVisible({ timeout: TIMEOUT.stress });
  });

  // --- NOTIFICATION: During run -------------------------------------------

  test('Step 10: Notification - running state', async () => {
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Running notification with progress %
    const runningItem = page
      .locator('.notif-item')
      .filter({ hasText: /Running/ });
    await expect(runningItem).toBeVisible({ timeout: TIMEOUT.modal });
    await expect(runningItem).toContainText(/\d+%/);

    // Close dropdown
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).not.toBeVisible();
  });

  // --- NOTIFICATION: Persistence across refresh ---------------------------

  test('Step 11: Refresh + verify notification persists', async () => {
    await page.reload();
    await page.waitForLoadState('networkidle');
    await dismissOnboarding(page);

    // Wait for app hydration - bell should be interactive again
    await expect(page.locator('.notif-bell')).toBeVisible({ timeout: TIMEOUT.nav });

    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Notification still present (hydrated from GET /api/jobs)
    const runningItem = page
      .locator('.notif-item')
      .filter({ hasText: /Running/ });
    await expect(runningItem).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // --- NOTIFICATION: Click to navigate to live run page -------------------

  test('Step 12: Click notification - navigate to live run page', async () => {
    const runningItem = page
      .locator('.notif-item')
      .filter({ hasText: /Running/ });
    await runningItem.click();

    await expect(page).toHaveURL(/\/tool-eval\/prompt-tuner\/run/, {
      timeout: TIMEOUT.nav,
    });
  });

  // --- WAIT FOR COMPLETION ------------------------------------------------

  test('Step 13: Wait for completion', async () => {
    // Navigate back to prompt-tuner run page
    await page.goto('/tool-eval/prompt-tuner/run');
    await page.waitForLoadState('networkidle');
    await dismissOnboarding(page);

    // Wait for "Best Prompt" text to appear (up to stress timeout)
    await expect(page.getByText('Best Prompt')).toBeVisible({
      timeout: TIMEOUT.stress,
    });
  });

  // --- VERIFY BEST PROMPT CARD --------------------------------------------

  test('Step 14: Verify Best Prompt card', async () => {
    await expect(page.getByText('Best Prompt')).toBeVisible({
      timeout: TIMEOUT.nav,
    });
    await expect(
      page.getByRole('button', { name: /Apply to Context/i }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
    await expect(
      page.getByRole('button', { name: /Copy/i }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // --- NOTIFICATION: Done state -------------------------------------------

  test('Step 15: Notification - done state', async () => {
    await page.locator('.notif-bell').click();
    await expect(page.locator('.notif-dropdown.open')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    const doneBadge = page.locator('.notif-status-badge.done');
    await expect(doneBadge).toBeVisible({ timeout: TIMEOUT.modal });
    await expect(doneBadge).toHaveText('Done');

    // Close dropdown
    await page.locator('.notif-bell').click();
  });

  // --- HISTORY: Verify entry ----------------------------------------------

  test('Step 16: Navigate to History, verify entry', async () => {
    await page.goto('/tool-eval/prompt-tuner/history');
    await page.waitForURL('**/tool-eval/prompt-tuner/history', { timeout: TIMEOUT.nav });
    await dismissOnboarding(page);

    await expect(page.getByText('Prompt Tuner History')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Card with suite name and status
    await expect(page.getByText('PRT Test Suite').first()).toBeVisible({
      timeout: TIMEOUT.nav,
    });
    await expect(page.getByText('completed').first()).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });
});
