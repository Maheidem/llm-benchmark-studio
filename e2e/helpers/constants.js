/**
 * Shared test constants and utilities.
 * Single source of truth for all E2E test configuration.
 *
 * CONSTRAINT: All provider/model test data must use ZAI ONLY.
 * No fake providers (e.g. "TestProv") — Zai is the only authorized
 * test provider with real API access. Within Zai, any model is fair game.
 */
const TEST_PASSWORD = 'TestPass123!';
const ZAI_API_KEY = 'fc4c9b7c1abb4018896632bb37d97238.gfvrpk58QqsUNFUA';

/** Generate a unique email with optional prefix to avoid collisions between tests */
function uniqueEmail(prefix = 'e2e') {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@test.local`;
}

/** Graduated timeout values (ms) */
const TIMEOUT = {
  modal: 5_000,
  nav: 10_000,
  fetch: 15_000,
  apiDiscovery: 30_000,
  benchmark: 90_000,
  stress: 120_000,
};

/**
 * Dismiss the onboarding wizard if visible (safe no-op if absent).
 * Also calls the API directly to guarantee persistence across page reloads.
 *
 * Call this after page.reload() or page.goto() to handle the wizard overlay.
 */
async function dismissOnboarding(page) {
  // 1. Ensure onboarding is marked complete via direct API call
  try {
    await page.evaluate(async () => {
      const token = localStorage.getItem('auth_token');
      if (token) {
        await fetch('/api/onboarding/complete', {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        });
      }
    });
  } catch { /* ignore — page may still be loading */ }

  // 2. Dismiss the wizard UI if it's already visible
  try {
    const skipBtn = page.getByRole('button', { name: 'Skip All' });
    await skipBtn.waitFor({ state: 'visible', timeout: 3_000 });
    await skipBtn.click();
    await page.getByRole('heading', { name: 'Welcome to Benchmark Studio!' })
      .waitFor({ state: 'hidden', timeout: 5_000 });
  } catch { /* Wizard not visible — expected */ }
}

module.exports = { TEST_PASSWORD, ZAI_API_KEY, uniqueEmail, TIMEOUT, dismissOnboarding };
