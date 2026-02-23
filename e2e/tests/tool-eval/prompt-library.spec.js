/**
 * @smoke Tool Eval - Prompt Library E2E Test
 *
 * Full user journey:
 *   1. Navigate to Prompt Library subtab, verify empty state.
 *   2. Save a new prompt version via the form.
 *   3. Verify version appears in the list with correct source badge.
 *   4. Edit the label inline.
 *   5. Save a second version, select both for diff comparison.
 *   6. Verify diff panel shows 2 side-by-side prompts.
 *   7. Delete a version.
 *   8. Copy prompt to clipboard (verify button exists).
 *
 * Self-contained: registers its own user (no dependency on other test files).
 * No LLM calls required - pure UI CRUD.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-prompt-lib');

test.describe('@smoke Tool Eval - Prompt Library', () => {
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

    // Navigate to Tool Eval > Prompt Library
    await page.getByRole('link', { name: 'Tool Eval' }).click();
    await page.waitForURL('**/tool-eval/**', { timeout: TIMEOUT.nav });
    await page.locator('.te-subtab').filter({ hasText: 'Prompt Library' }).click();
    await page.waitForURL('**/tool-eval/prompt-library', { timeout: TIMEOUT.nav });
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // --- STEP 1: VERIFY EMPTY STATE -----------------------------------------

  test('Step 1: Verify empty state message', async () => {
    await expect(page.getByRole('heading', { name: 'Prompt Library' })).toBeVisible({
      timeout: TIMEOUT.nav,
    });
    await expect(page.getByText('No saved prompts yet')).toBeVisible({
      timeout: TIMEOUT.nav,
    });
  });

  // --- STEP 2: SAVE A NEW PROMPT VERSION ----------------------------------

  test('Step 2: Save a new prompt version with label', async () => {
    // Click "+ Save New" button
    await page.getByRole('button', { name: '+ Save New' }).click();

    // Fill prompt text
    const textarea = page.locator('textarea[placeholder="Enter system prompt..."]');
    await textarea.waitFor({ state: 'visible', timeout: TIMEOUT.modal });
    await textarea.fill('You are a helpful weather assistant. Always use the get_weather tool when asked about weather.');

    // Fill label
    const labelInput = page.locator('input[placeholder="e.g. v1-tool-focused"]');
    await labelInput.fill('v1-weather');

    // Click Save
    await page.locator('.modal-btn-confirm').filter({ hasText: 'Save' }).click();

    // Verify empty state is gone and version appears
    await expect(page.getByText('No saved prompts yet')).not.toBeVisible({
      timeout: TIMEOUT.modal,
    });
    await expect(page.getByText('v1-weather')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // --- STEP 3: VERIFY SOURCE BADGE ----------------------------------------

  test('Step 3: Verify version has "manual" source badge', async () => {
    // Source badge text should show "manual"
    await expect(page.getByText('manual').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // --- STEP 4: EDIT LABEL INLINE ------------------------------------------

  test('Step 4: Edit label inline', async () => {
    // Click on the label span to enter edit mode
    const labelSpan = page.locator('span.cursor-pointer').filter({ hasText: 'v1-weather' });
    await labelSpan.click();

    // The inline edit input appears inside the version card
    // It's the only <input> visible in the card (the version card, not the save form)
    const versionCard = page.locator('.card.rounded-md').filter({
      has: page.locator('button', { hasText: 'Load' }),
    });
    const editInput = versionCard.locator('input').first();
    await editInput.waitFor({ state: 'visible', timeout: TIMEOUT.modal });
    await editInput.fill('v1-weather-updated');

    // Click "Save" next to the edit input (inside the card, not the form Save)
    await versionCard.getByRole('button', { name: 'Save' }).click();

    // Verify updated label
    await expect(page.getByText('v1-weather-updated')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // --- STEP 5: SAVE A SECOND VERSION -------------------------------------

  test('Step 5: Save a second prompt version', async () => {
    await page.getByRole('button', { name: '+ Save New' }).click();

    const textarea = page.locator('textarea[placeholder="Enter system prompt..."]');
    await textarea.waitFor({ state: 'visible', timeout: TIMEOUT.modal });
    await textarea.fill('You are a strict tool-calling assistant. Only call tools when explicitly asked. Never hallucinate tool calls.');

    const labelInput = page.locator('input[placeholder="e.g. v1-tool-focused"]');
    await labelInput.fill('v2-strict');

    await page.locator('.modal-btn-confirm').filter({ hasText: 'Save' }).click();

    // Verify both versions visible
    await expect(page.getByText('v1-weather-updated')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
    await expect(page.getByText('v2-strict')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // --- STEP 6: SELECT BOTH FOR DIFF COMPARISON ----------------------------

  test('Step 6: Select two versions for diff comparison', async () => {
    // Click the diff checkbox on first version card
    const versionCards = page.locator('.card.rounded-md').filter({
      has: page.locator('button', { hasText: 'Load' }),
    });

    // Click first card's diff toggle
    await versionCards.nth(0).locator('button[title*="diff"]').click();

    // "Select one more version to compare" message should appear
    await expect(page.getByText('Select one more version to compare')).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Click second card's diff toggle
    await versionCards.nth(1).locator('button[title*="diff"]').click();

    // Diff panel should now show "Comparing 2 Versions"
    await expect(page.getByText('Comparing 2 Versions')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // --- STEP 7: VERIFY DIFF PANEL CONTENT ----------------------------------

  test('Step 7: Verify diff panel shows side-by-side content', async () => {
    // Two <pre> blocks should be visible in the diff panel
    const diffPanel = page.locator('.card').filter({ hasText: 'Comparing 2 Versions' });
    const preTags = diffPanel.locator('pre');
    await expect(preTags).toHaveCount(2);

    // Clear diff
    await diffPanel.getByText('Clear').click();
    await expect(page.getByText('Comparing 2 Versions')).not.toBeVisible({
      timeout: TIMEOUT.modal,
    });
  });

  // --- STEP 8: COPY BUTTON EXISTS -----------------------------------------

  test('Step 8: Verify Copy button exists on version card', async () => {
    const copyBtn = page.getByRole('button', { name: 'Copy' }).first();
    await expect(copyBtn).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // --- STEP 9: LOAD INTO TUNER BUTTON EXISTS ------------------------------

  test('Step 9: Verify Load button exists on version card', async () => {
    const loadBtn = page.getByRole('button', { name: 'Load' }).first();
    await expect(loadBtn).toBeVisible({ timeout: TIMEOUT.modal });
  });

  // --- STEP 10: DELETE A VERSION ------------------------------------------

  test('Step 10: Delete a prompt version', async () => {
    // Dismiss any dialog that might appear
    page.on('dialog', dialog => dialog.accept());

    // Click delete (trash icon) on the second version
    const deleteButtons = page.locator('button[title="Delete version"]');
    const count = await deleteButtons.count();
    expect(count).toBeGreaterThanOrEqual(1);

    await deleteButtons.last().click();

    // One version should remain
    const versionCards = page.locator('.card.rounded-md').filter({
      has: page.locator('button', { hasText: 'Load' }),
    });
    await expect(versionCards).toHaveCount(1, { timeout: TIMEOUT.modal });
  });
});
