/**
 * ProviderSetup component object — encapsulates the full provider creation workflow.
 * Extracted from zai-provider-setup.spec.js and stress-test.spec.js.
 *
 * CONSTRAINT: All test data uses ZAI provider and its models ONLY.
 * No fake/mock providers — Zai has real API access for E2E testing.
 *
 * Usage:
 *   const ps = new ProviderSetup(page);
 *   await ps.setupZai(['GLM-4.5-Air']);           // Full Zai setup in one call
 *   await ps.setupZai(['GLM-4.7', 'GLM-5']);      // Multiple models
 */
const { expect } = require('@playwright/test');
const { TIMEOUT, ZAI_API_KEY } = require('../helpers/constants');

class ProviderSetup {
  constructor(page) {
    this.page = page;
  }

  /** Navigate to Settings > Providers */
  async navigateToProviders() {
    await this.page.getByRole('link', { name: 'Settings' }).click();
    await this.page.getByRole('link', { name: 'Providers' }).click();
    await expect(this.page).toHaveURL(/\/settings\/providers/);
  }

  /** Create a new provider with name, API base URL, and model prefix */
  async createProvider(name, apiBase, prefix) {
    await this.page.getByRole('button', { name: '+ Add Provider' }).click();
    await this.page.getByPlaceholder('My Provider').fill(name);
    await this.page.getByPlaceholder('https://api.example.com/v1').fill(apiBase);
    await this.page
      .locator('input[placeholder="Optional -- prepended to model IDs"]')
      .fill(prefix);
    await this.page.getByRole('button', { name: 'Create Provider' }).click();
    await expect(
      this.page.locator('.badge', { hasText: name }),
    ).toBeVisible({ timeout: TIMEOUT.modal });
  }

  /** Navigate to API Keys tab and set the key for a provider */
  async setApiKey(providerName, key) {
    await this.page.getByRole('link', { name: 'API Keys' }).click();
    await expect(this.page).toHaveURL(/\/settings\/keys/);

    const row = this.page.locator('.px-5.py-3').filter({
      has: this.page.locator('.text-zinc-300', {
        hasText: new RegExp(`^${providerName}$`),
      }),
    });
    await row.getByRole('button', { name: /Set Key/ }).click();

    const modal = this.page.locator('.modal-overlay');
    await modal.waitFor({ state: 'visible', timeout: TIMEOUT.modal });
    await modal.locator('.modal-input').fill(key);
    await modal.locator('.modal-btn-confirm').click();
    await modal.waitFor({ state: 'hidden', timeout: TIMEOUT.modal });
    await expect(
      this.page.getByText('YOUR KEY').first(),
    ).toBeVisible({ timeout: TIMEOUT.modal });
  }

  /** Fetch models from the provider API and add selected ones */
  async fetchAndAddModels(providerName, modelNames) {
    await this.page.getByRole('link', { name: 'Providers' }).click();
    await expect(this.page).toHaveURL(/\/settings\/providers/);

    const card = this.page.locator('.card').filter({
      has: this.page.locator('.badge', { hasText: providerName }),
    });
    await card.getByRole('button', { name: 'Fetch' }).click();

    const dialog = this.page.locator('.modal-overlay');
    await dialog.waitFor({ state: 'visible', timeout: TIMEOUT.apiDiscovery });

    for (const modelName of modelNames) {
      const label = dialog.locator('label').filter({ hasText: modelName });
      const checkbox = label.locator('input[type="checkbox"]');
      if (!(await checkbox.isChecked())) {
        await checkbox.check();
      }
    }

    await dialog.getByRole('button', { name: 'Add Selected' }).click();
    await dialog.waitFor({ state: 'hidden', timeout: TIMEOUT.nav });

    for (const modelName of modelNames) {
      await expect(
        this.page.getByText(modelName).first(),
      ).toBeVisible({ timeout: TIMEOUT.modal });
    }
  }

  /** Set context window tier for a model within a provider card */
  async setContextWindow(providerName, modelName, tier) {
    const card = this.page.locator('.card').filter({
      has: this.page.locator('.badge', { hasText: providerName }),
    });
    const modelSection = card.locator('div.px-5').filter({ hasText: modelName });

    await modelSection.getByRole('button', { name: 'Edit' }).click();
    await modelSection.getByRole('button', { name: tier }).click();
    await modelSection.getByRole('button', { name: 'Save' }).click();
    await expect(modelSection.getByText('Saved')).toBeVisible({
      timeout: TIMEOUT.modal,
    });
    await modelSection.getByRole('button', { name: 'Collapse' }).click();
  }

  /**
   * Full Zai provider setup: create → set key → fetch models → set context.
   * @param {string[]} models - Model names to add (default: ['GLM-4.5-Air'])
   * @param {string} contextTier - Context tier button label (default: '200K')
   */
  async setupZai(models = ['GLM-4.5-Air'], contextTier = '200K') {
    await this.navigateToProviders();
    await this.createProvider(
      'Zai',
      'https://api.z.ai/api/coding/paas/v4/',
      'zai',
    );
    await this.setApiKey('Zai', ZAI_API_KEY);
    await this.fetchAndAddModels('Zai', models);
    for (const model of models) {
      await this.setContextWindow('Zai', model, contextTier);
    }
  }
}

module.exports = { ProviderSetup };
