/**
 * @regression BFCL Import user journey
 *
 * Covers the "Import BFCL" button on the SuitesView:
 * - Button is visible on the Tool Eval suites page
 * - Clicking opens a file picker (input[type=file] is triggered)
 * - After successful import, the imported suite appears in the list
 * - Error toast is shown for an invalid BFCL format file
 *
 * Self-contained: registers its own user, sets up Zai provider.
 */
const { test, expect } = require('@playwright/test');
const { AuthModal } = require('../../components/AuthModal');
const { ProviderSetup } = require('../../components/ProviderSetup');
const { uniqueEmail, TEST_PASSWORD, TIMEOUT } = require('../../helpers/constants');

const TEST_EMAIL = uniqueEmail('e2e-bfcl-import');

/** Minimal valid BFCL V3 JSON with one entry */
const VALID_BFCL = JSON.stringify([
  {
    id: 'weather_0',
    function: [
      {
        name: 'get_weather',
        description: 'Get current weather for a city',
        parameters: {
          type: 'object',
          properties: {
            city: { type: 'string', description: 'City name' },
          },
          required: ['city'],
        },
      },
    ],
    question: [[{ role: 'user', content: "What's the weather in Paris?" }]],
    answer: [{ get_weather: { city: 'Paris' } }],
  },
]);

/** Invalid BFCL: missing required "function" and "question" keys */
const INVALID_BFCL = JSON.stringify([
  {
    name: 'Not a BFCL suite',
    tools: [],
  },
]);

test.describe('@regression BFCL Import', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(120_000);

  /** @type {import('@playwright/test').BrowserContext} */
  let context;
  /** @type {import('@playwright/test').Page} */
  let page;

  test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();

    // Register user + setup Zai provider
    const auth = new AuthModal(page);
    await page.goto('/login');
    await auth.register(TEST_EMAIL, TEST_PASSWORD);
    await page.waitForURL('**/benchmark', { timeout: TIMEOUT.nav });

    const ps = new ProviderSetup(page);
    await ps.setupZai(['GLM-4.5-Air']);

    // Navigate to Tool Eval > Suites
    await page.getByRole('link', { name: 'Tool Eval' }).click();
    await page.waitForURL('**/tool-eval/**', { timeout: TIMEOUT.nav });
    await page.locator('.te-subtab').filter({ hasText: 'Suites' }).click();
    await page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });
  });

  test.afterAll(async () => {
    await context?.close();
  });

  // ─── BUTTON VISIBILITY ───────────────────────────────────────────────────

  test('Step 1: "Import BFCL" button is visible on the Suites page', async () => {
    await expect(
      page.locator('button').filter({ hasText: /Import BFCL/i }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── FILE PICKER TRIGGERED ───────────────────────────────────────────────

  test('Step 2: Clicking "Import BFCL" opens a file chooser', async () => {
    // The button creates a hidden input[type=file] and calls .click() on it.
    // Playwright's waitForEvent('filechooser') intercepts that native dialog.
    const fileChooserPromise = page.waitForEvent('filechooser', { timeout: TIMEOUT.modal });

    await page.locator('button').filter({ hasText: /Import BFCL/i }).click();

    // If the file chooser fires, the button is correctly wired to a file input.
    const fileChooser = await fileChooserPromise;
    expect(fileChooser).toBeTruthy();

    // Dismiss without selecting (set an empty files array is not supported,
    // so we just let the chooser go out of scope — no file selected means
    // the onchange handler never fires).
  });

  // ─── SUCCESSFUL IMPORT ───────────────────────────────────────────────────

  test('Step 3: Importing a valid BFCL JSON creates the suite and shows it in the list', async () => {
    const fileChooserPromise = page.waitForEvent('filechooser', { timeout: TIMEOUT.modal });
    await page.locator('button').filter({ hasText: /Import BFCL/i }).click();

    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: 'my-bfcl-suite.json',
      mimeType: 'application/json',
      buffer: Buffer.from(VALID_BFCL),
    });

    // After import the handler calls openSuite() which redirects to the editor
    await page.waitForURL('**/tool-eval/suites/**', { timeout: TIMEOUT.fetch });

    // The suite editor should show the get_weather tool
    await expect(page.getByText('get_weather').first()).toBeVisible({
      timeout: TIMEOUT.modal,
    });

    // Navigate back to suites list and verify the suite is present
    await page.locator('.te-subtab').filter({ hasText: 'Suites' }).click();
    await page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });

    // Suite name is derived from filename: "my-bfcl-suite" (extension stripped)
    await expect(
      page.getByRole('button', { name: 'my-bfcl-suite' }),
    ).toBeVisible({ timeout: TIMEOUT.nav });
  });

  // ─── ERROR TOAST FOR INVALID FORMAT ──────────────────────────────────────

  test('Step 4: Importing an invalid BFCL format shows an error toast', async () => {
    // Ensure we are on the suites list page
    await page.locator('.te-subtab').filter({ hasText: 'Suites' }).click();
    await page.waitForURL('**/tool-eval/suites', { timeout: TIMEOUT.nav });

    const fileChooserPromise = page.waitForEvent('filechooser', { timeout: TIMEOUT.modal });
    await page.locator('button').filter({ hasText: /Import BFCL/i }).click();

    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: 'bad-bfcl.json',
      mimeType: 'application/json',
      buffer: Buffer.from(INVALID_BFCL),
    });

    // The frontend validates that entries have "function" and "question" keys
    // and shows an error toast when they are missing.
    await expect(
      page.locator('.toast, [class*="toast"], [role="alert"]').filter({
        hasText: /invalid bfcl|failed to import/i,
      }),
    ).toBeVisible({ timeout: TIMEOUT.modal });

    // We should NOT have been redirected to an editor
    await expect(page).toHaveURL(/\/tool-eval\/suites$/, { timeout: TIMEOUT.modal });
  });
});
