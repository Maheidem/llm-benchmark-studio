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

module.exports = { TEST_PASSWORD, ZAI_API_KEY, uniqueEmail, TIMEOUT };
