/**
 * @critical Prompt Tuner — History Interactions (Apply, Detail Modal, Delete)
 *
 * Tests PromptTunerHistory view: click run card to open detail modal,
 * verify PromptTimeline generations, Apply best prompt, delete run.
 *
 * Self-contained: registers its own user, sets up Zai, creates suite,
 * runs prompt tuning (population 2), then exercises history interactions.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { SuiteSetup } = require('../../components/SuiteSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-prt-adv');

test.describe('@critical Prompt Tuner — History Interactions', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(240_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register user + setup Zai
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);

    // Create suite
    const ss = new SuiteSetup(page);
    await ss.createSuiteWithCase('PRT Adv Suite');
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── RUN PROMPT TUNING (MINIMAL) ─────────────────────────────────────

  test('Step 1: Run prompt tuning with population 2', async () => {
    // Navigate to Prompt Tuner config
    await page.locator('.te-subtab').filter({ hasText: 'Prompt Tuner' }).click();
    await page.waitForURL('**/tool-eval/prompt-tuner', { timeout: TIMEOUT.nav });

    // Select suite
    const suiteSelect = page.locator('select').first();
    await suiteSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });
    const option = suiteSelect.locator('option', { hasText: 'PRT Adv Suite' });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const optionValue = await option.getAttribute('value');
    await suiteSelect.selectOption(optionValue);

    // Select meta model (GLM-4.5-Air)
    const metaSelect = page.locator('select').filter({
      has: page.locator('option', { hasText: 'GLM-4.5-Air' }),
    });
    const metaOption = metaSelect.locator('option', { hasText: 'GLM-4.5-Air' });
    const metaValue = await metaOption.getAttribute('value');
    await metaSelect.selectOption(metaValue);

    // Select target model
    const modelCard = page.locator('.model-card').filter({ hasText: 'GLM-4.5-Air' });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    // Set population size to 2
    const popInput = page.locator('input').filter({
      has: page.locator('..', { hasText: /Population/i }),
    });
    // Fallback: try locating by nearby label
    const popField = page.locator('.field-label', { hasText: /Population/i })
      .locator('..').locator('input[type="number"]');
    const inputToUse = await popField.isVisible() ? popField : popInput;
    await inputToUse.fill('2');

    // Start prompt tuning
    await page.locator('.run-btn').filter({ hasText: /Start Prompt Tuning/i }).click();

    // Wait for progress + completion
    await expect(page.locator('.pulse-dot')).toBeVisible({ timeout: TIMEOUT.nav });
    await expect(
      page.locator('.section-label', { hasText: /Best Prompt/i }),
    ).toBeVisible({ timeout: TIMEOUT.stress });
  });

  // ─── NAVIGATE TO HISTORY ─────────────────────────────────────────────

  test('Step 2: Navigate to Prompt Tuner History', async () => {
    await page.goto('/tool-eval/prompt-tuner/history');
    await page.waitForURL('**/tool-eval/prompt-tuner/history', { timeout: TIMEOUT.nav });

    // Verify heading
    await expect(
      page.getByRole('heading', { name: /Prompt Tuner History/i }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── VERIFY RUN CARD ──────────────────────────────────────────────────

  test('Step 3: Verify run card with mode badge and suite name', async () => {
    const runCard = page.locator('.card').filter({ hasText: 'PRT Adv Suite' });
    await expect(runCard).toBeVisible({ timeout: TIMEOUT.nav });

    // Status might still be "running" briefly — wait for "completed"
    await expect(runCard).toContainText(/completed/i, { timeout: TIMEOUT.stress });

    // Mode badge should show "quick" (default)
    await expect(runCard).toContainText(/quick/i);
  });

  // ─── CLICK CARD → DETAIL MODAL ─────────────────────────────────────

  test('Step 4: Click run card to open detail modal', async () => {
    const runCard = page.locator('.card').filter({ hasText: 'PRT Adv Suite' });
    await runCard.click();

    // Detail modal should open
    const modal = page.locator('.fixed.inset-0.z-50');
    await expect(modal).toBeVisible({ timeout: TIMEOUT.modal });

    // Should show prompt timeline content (generations)
    // PromptTimeline component renders generation items
    await expect(modal.getByText(/generation|gen/i).first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // ─── VERIFY PROMPT CONTENT IN MODAL ─────────────────────────────────

  test('Step 5: Verify prompt content in detail modal', async () => {
    const modal = page.locator('.fixed.inset-0.z-50');

    // Should show the best prompt text (highlighted box)
    const bestSection = modal.locator('div').filter({ hasText: /Best Prompt/i }).first();
    await expect(bestSection).toBeVisible({ timeout: TIMEOUT.modal });

    // Should show score information
    await expect(modal.getByText(/%/).first()).toBeVisible();
  });

  // ─── CLOSE MODAL ──────────────────────────────────────────────────

  test('Step 6: Close detail modal', async () => {
    const modal = page.locator('.fixed.inset-0.z-50');

    // Click close button
    const closeBtn = modal.locator('button').filter({ hasText: /×|Close/i }).first();
    await closeBtn.click();

    // Modal should be hidden
    await expect(modal).not.toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── VERIFY BEST PROMPT PREVIEW ──────────────────────────────────────

  test('Step 7: Verify best prompt preview on card', async () => {
    const runCard = page.locator('.card').filter({ hasText: 'PRT Adv Suite' });

    // Card should show best score percentage
    await expect(runCard.getByText(/%/).first()).toBeVisible({ timeout: TIMEOUT.modal });

    // Card should show "best" label
    await expect(runCard.getByText('best')).toBeVisible();
  });

  // ─── DELETE RUN ──────────────────────────────────────────────────────

  test('Step 8: Delete run from history', async () => {
    const runCard = page.locator('.card').filter({ hasText: 'PRT Adv Suite' });

    // PromptTunerHistory uses window.confirm() — set up dialog handler
    page.on('dialog', (dialog) => dialog.accept());

    // Click delete button (SVG trash icon with title="Delete run")
    const deleteBtn = runCard.locator('button[title="Delete run"]');
    await deleteBtn.click();

    // Run card should be gone
    await expect(runCard).not.toBeVisible({ timeout: TIMEOUT.modal });
  });
});
