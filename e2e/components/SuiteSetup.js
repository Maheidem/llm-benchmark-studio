/**
 * SuiteSetup component object — encapsulates suite creation + quick eval run.
 * Extracted from evaluate-run.spec.js for reuse across tool-eval test files.
 *
 * CONSTRAINT: All test data uses ZAI provider and its models ONLY.
 *
 * Usage:
 *   const ss = new SuiteSetup(page);
 *   await ss.createSuiteWithCase('My Suite');               // Create suite + tool + test case
 *   await ss.runQuickEval('My Suite', 'GLM-4.5-Air');       // Run eval and wait for results
 */
const { expect } = require('@playwright/test');
const { TIMEOUT } = require('../helpers/constants');

const TOOL_JSON = JSON.stringify({
  type: 'function',
  function: {
    name: 'get_weather',
    description: 'Get weather',
    parameters: {
      type: 'object',
      properties: {
        city: { type: 'string' },
      },
      required: ['city'],
    },
  },
});

class SuiteSetup {
  constructor(page) {
    this.page = page;
  }

  /**
   * Create a suite with 1 tool (get_weather) + 1 test case via the UI.
   * Navigates to Tool Eval > Suites, creates suite, adds tool + test case.
   */
  async createSuiteWithCase(suiteName = 'E2E Test Suite') {
    // Navigate to Tool Eval > Suites
    await this.page.getByRole('link', { name: 'Tool Eval' }).click();
    await this.page.waitForURL('**/tool-eval/**', { timeout: TIMEOUT.nav });

    await this.page.locator('.te-subtab').filter({ hasText: 'Suites' }).click();
    await this.page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });

    // Click "New Suite" — redirects to editor
    await this.page.locator('button.run-btn').filter({ hasText: 'New Suite' }).click();
    await this.page.waitForURL('**/tool-eval/suites/**', { timeout: TIMEOUT.nav });

    // Fill suite name
    const nameInput = this.page.locator('input[placeholder="Suite Name"]');
    await nameInput.fill('');
    await nameInput.fill(suiteName);
    await nameInput.blur();
    await this.page.waitForTimeout(500);

    // Add tool: click "+ Add Tool"
    await this.page.getByText('+ Add Tool').click();

    // Fill tool JSON in textarea
    const toolTextarea = this.page.locator('textarea').first();
    await toolTextarea.fill(TOOL_JSON);

    // Save tool
    await this.page.locator('.modal-btn-confirm').filter({ hasText: 'Save Tool' }).click();

    // Verify tool appears
    await expect(this.page.getByText('get_weather').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Add test case: click "+ Add Test Case"
    await this.page.getByText('+ Add Test Case').click();

    // Fill prompt in the USER MESSAGE textarea (not the Expected Parameters one)
    const promptTextarea = this.page.locator('textarea[placeholder*="user prompt"]');
    await promptTextarea.fill("What's the weather in Paris?");

    // Fill expected tool
    const expectedToolInput = this.page.locator(
      'input[placeholder="tool_name (comma-separated for alternatives)"]',
    );
    await expectedToolInput.fill('get_weather');

    // Save test case
    await this.page.locator('.modal-btn-confirm').filter({ hasText: 'Save' }).click();

    // Verify test case appears
    await expect(this.page.getByText('weather in Paris').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });
  }

  /**
   * Run eval on a suite and wait for completion. Returns after results table visible.
   * Assumes provider + models are already configured.
   * @param {string} suiteName - Suite to select from dropdown
   * @param {string} modelName - Model card to click (default: 'GLM-4.5-Air')
   */
  async runQuickEval(suiteName, modelName = 'GLM-4.5-Air') {
    // Navigate to Tool Eval > Evaluate
    await this.page.locator('.te-subtab').filter({ hasText: 'Evaluate' }).click();
    await this.page.waitForURL('**/tool-eval/evaluate', { timeout: TIMEOUT.nav });

    // Select suite from dropdown
    const suiteSelect = this.page.locator('select').first();
    await suiteSelect.waitFor({ state: 'visible', timeout: TIMEOUT.nav });
    const option = suiteSelect.locator('option', { hasText: suiteName });
    await expect(option).toBeAttached({ timeout: TIMEOUT.nav });
    const optionValue = await option.getAttribute('value');
    await suiteSelect.selectOption(optionValue);

    // Select model
    const modelCard = this.page.locator('.model-card').filter({ hasText: modelName });
    await modelCard.click();
    await expect(modelCard).toHaveClass(/selected/);

    // Start eval
    await this.page.locator('.run-btn').filter({ hasText: 'Start Eval' }).click();

    // Wait for progress UI
    await expect(this.page.locator('.pulse-dot')).toBeVisible({
      timeout: TIMEOUT.nav,
    });

    // Wait for results table to appear (up to stress timeout for LLM call)
    await expect(this.page.locator('.results-table tbody tr').first()).toBeVisible({
      timeout: TIMEOUT.stress,
    });
  }
}

module.exports = { SuiteSetup };
