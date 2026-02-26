/**
 * @smoke Judge & Tuning Settings Test Suite
 *
 * Deterministic browser tests for the Judge and Tuning settings panels.
 * Covers: Judge panel controls (auto-judge toggle, model selector, mode, temperature),
 *         Tuning panel controls (max combinations, temperature range, prompt tuner mode),
 *         and persistence across page reloads.
 *
 * Self-contained: registers its own user, sets up Zai provider (needed for judge model list).
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT, dismissOnboarding } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-judge-tuning');

test.describe('@smoke Judge & Tuning Settings', () => {
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

    // Set up Zai provider with GLM-4.5-Air — needed for judge model dropdown
    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── JUDGE PANEL ──────────────────────────────────────────────────────

  test('Step 1: Navigate to Settings > Judge and verify panel loads', async () => {
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('link', { name: /Judge/ }).click();
    await expect(page).toHaveURL(/\/settings\/judge/);

    // Verify the "Judge Model" section label is visible
    await expect(
      page.locator('.section-label', { hasText: 'Judge Model' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Verify key controls are present
    await expect(page.getByText('Auto-judge enabled')).toBeVisible();
    await expect(page.locator('.field-label', { hasText: /^Judge Model$/ })).toBeVisible();
    await expect(page.locator('.field-label', { hasText: /^Judge Mode$/ })).toBeVisible();
    await expect(page.locator('.field-label', { hasText: 'Temperature' })).toBeVisible();
  });

  test('Step 2: Interact with judge settings controls', async () => {
    // --- Toggle auto-judge checkbox ---
    const autoJudgeCheckbox = page.locator('input[type="checkbox"]').first();
    const initialChecked = await autoJudgeCheckbox.isChecked();

    await autoJudgeCheckbox.click();
    const afterToggle = await autoJudgeCheckbox.isChecked();
    expect(afterToggle).toBe(!initialChecked);

    // --- Verify judge model selector has options from Zai provider ---
    const modelSelect = page.locator('.settings-select').first();
    await expect(modelSelect).toBeVisible();

    // Should have the placeholder + at least one Zai model option
    const optionCount = await modelSelect.locator('option').count();
    expect(optionCount).toBeGreaterThanOrEqual(2); // placeholder + GLM-4.5-Air

    // Select the first real model option (skip the placeholder)
    const firstModelOption = modelSelect.locator('option').nth(1);
    const modelValue = await firstModelOption.getAttribute('value');
    await modelSelect.selectOption(modelValue);

    // --- Change judge mode to "Manual only" ---
    const modeSelect = page.locator('.settings-select').nth(1);
    await modeSelect.selectOption('manual');
    await expect(modeSelect).toHaveValue('manual');

    // --- Set temperature to 0.7 ---
    const tempInput = page.locator('.settings-input').first();
    await tempInput.fill('0.7');
    await tempInput.dispatchEvent('change');

    // --- Set custom instructions ---
    const textarea = page.locator('textarea');
    await textarea.fill('Focus on correctness. Penalize hallucinations.');

    // Wait for debounced save to complete
    await page.waitForTimeout(800);

    // Verify "Saved" feedback appears (exact match to avoid matching toast "Key saved")
    await expect(page.getByText('Saved', { exact: true })).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── TUNING PANEL ─────────────────────────────────────────────────────

  test('Step 3: Navigate to Settings > Tuning and verify panel loads', async () => {
    await page.getByRole('link', { name: /Tuning/ }).click();
    await expect(page).toHaveURL(/\/settings\/tuning/);

    // Verify "Parameter Tuner Defaults" section is visible (open by default)
    await expect(
      page.locator('.section-label', { hasText: 'Parameter Tuner Defaults' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Verify parameter tuner fields are visible
    await expect(page.locator('.field-label', { hasText: 'Max Combinations' })).toBeVisible();
  });

  test('Step 4: Interact with tuning settings controls', async () => {
    // --- Modify max combinations ---
    const maxCombInput = page.locator('.settings-input').first();
    await maxCombInput.fill('25');
    await maxCombInput.dispatchEvent('change');

    // --- Modify temperature range min ---
    const tempMinInput = page.locator('.settings-input').nth(1);
    await tempMinInput.fill('0.2');
    await tempMinInput.dispatchEvent('change');

    // --- Expand Prompt Tuner Defaults section (collapsed by default) ---
    const promptTunerHeader = page.locator('.section-label', { hasText: 'Prompt Tuner Defaults' });
    await promptTunerHeader.click();

    // Verify prompt tuner fields become visible
    await expect(page.locator('.field-label', { hasText: 'Mode' })).toBeVisible({ timeout: TIMEOUT.modal });

    // --- Change prompt tuner mode to "thorough" ---
    const modeSelect = page.locator('.settings-select').filter({ hasText: /Quick|Thorough|Exhaustive/ }).first();
    await modeSelect.selectOption('thorough');

    // Wait for debounced save
    await page.waitForTimeout(800);

    // Verify "Saved" feedback (exact match to avoid matching toast "Key saved")
    await expect(page.getByText('Saved', { exact: true })).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // ─── PERSISTENCE ──────────────────────────────────────────────────────

  test('Step 5: Reload page and verify settings persisted', async () => {
    await page.reload({ waitUntil: 'networkidle' });
    await dismissOnboarding(page);

    // --- Verify judge settings persisted ---
    await page.getByRole('link', { name: /Judge/ }).click();
    await expect(page).toHaveURL(/\/settings\/judge/);
    await expect(
      page.locator('.section-label', { hasText: 'Judge Model' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Auto-judge should be toggled on (we toggled it from off to on in Step 2)
    const autoJudgeCheckbox = page.locator('input[type="checkbox"]').first();
    await expect(autoJudgeCheckbox).toBeChecked();

    // Judge mode should be "manual"
    const modeSelect = page.locator('.settings-select').nth(1);
    await expect(modeSelect).toHaveValue('manual');

    // Temperature should be 0.7
    const tempInput = page.locator('.settings-input').first();
    await expect(tempInput).toHaveValue('0.7');

    // Custom instructions should persist
    const textarea = page.locator('textarea');
    await expect(textarea).toHaveValue('Focus on correctness. Penalize hallucinations.');

    // --- Verify tuning settings persisted ---
    await page.getByRole('link', { name: /Tuning/ }).click();
    await expect(page).toHaveURL(/\/settings\/tuning/);
    await expect(
      page.locator('.section-label', { hasText: 'Parameter Tuner Defaults' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });

    // Max combinations should be 25
    const maxCombInput = page.locator('.settings-input').first();
    await expect(maxCombInput).toHaveValue('25');

    // Temperature min should be 0.2
    const tempMinInput = page.locator('.settings-input').nth(1);
    await expect(tempMinInput).toHaveValue('0.2');

    // Expand prompt tuner section and check mode
    const promptTunerHeader = page.locator('.section-label', { hasText: 'Prompt Tuner Defaults' });
    await promptTunerHeader.click();
    await expect(page.locator('.field-label', { hasText: 'Mode' })).toBeVisible({ timeout: TIMEOUT.modal });

    const promptModeSelect = page.locator('.settings-select').filter({ hasText: /Quick|Thorough|Exhaustive/ }).first();
    await expect(promptModeSelect).toHaveValue('thorough');
  });
});
