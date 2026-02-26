/**
 * AuthModal component object — encapsulates all selectors for the login/register modal.
 * Selectors derived from: frontend/src/components/auth/AuthModal.vue
 *                          frontend/src/views/LandingPage.vue
 *
 * The modal is NOT visible by default. Call openViaSignUp() or openViaLogin()
 * to trigger it from the landing page first.
 */
class AuthModal {
  constructor(page) {
    this.page = page;
    this.overlay = page.locator('.modal-overlay');

    // Selectors scoped INSIDE the modal overlay
    this.emailInput = this.overlay.getByPlaceholder('you@example.com');
    this.passwordInput = this.overlay.getByPlaceholder('Min 8 characters');
    this.registerTab = this.overlay.getByRole('button', { name: 'Register' });
    this.loginTab = this.overlay.getByRole('button', { name: 'Login' });
    this.errorBanner = this.overlay.locator('.error-banner');
  }

  /** Submit button inside the modal (text: "Login" or "Create Account") */
  get submitButton() {
    return this.overlay.locator('button[type="submit"]');
  }

  /** Open modal from landing page via "Sign Up" button (sets mode to register) */
  async openViaSignUp() {
    await this.page.getByRole('button', { name: 'Sign Up' }).click();
    await this.overlay.waitFor({ state: 'visible', timeout: 5_000 });
  }

  /** Open modal from landing page via top-bar "Login" button (sets mode to login) */
  async openViaLogin() {
    // Use the topbar Login button (landing-btn-secondary), not the in-modal tab
    await this.page.locator('.landing-topbar').getByRole('button', { name: 'Login' }).click();
    await this.overlay.waitFor({ state: 'visible', timeout: 5_000 });
  }

  async switchToRegister() {
    await this.registerTab.click();
  }

  async switchToLogin() {
    await this.loginTab.click();
  }

  async fillCredentials(email, password) {
    await this.emailInput.fill(email);
    await this.passwordInput.fill(password);
  }

  async submit() {
    await this.submitButton.click();
  }

  async register(email, password, { dismissOnboarding = true } = {}) {
    await this.openViaSignUp();
    await this.fillCredentials(email, password);
    await this.submit();
    if (dismissOnboarding) {
      await this.dismissOnboarding();
    }
  }

  /**
   * Dismiss the onboarding wizard if it appears (safe no-op if absent).
   * Must be called AFTER page navigation completes (e.g., after waitForURL).
   */
  async dismissOnboarding() {
    // Belt-and-suspenders: call API directly to persist onboarding status
    try {
      await this.page.evaluate(async () => {
        const token = localStorage.getItem('auth_token');
        if (token) {
          await fetch('/api/onboarding/complete', {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
          });
        }
      });
    } catch { /* ignore */ }

    // Then dismiss the wizard UI if visible
    try {
      const skipBtn = this.page.getByRole('button', { name: 'Skip All' });
      await skipBtn.waitFor({ state: 'visible', timeout: 5_000 });
      await skipBtn.click();
      const heading = this.page.getByRole('heading', { name: 'Welcome to Benchmark Studio!' });
      await heading.waitFor({ state: 'hidden', timeout: 5_000 });
    } catch {
      // Wizard didn't appear — that's fine (e.g., registration failed or onboarding already done)
    }
  }

  async login(email, password) {
    await this.openViaLogin();
    await this.fillCredentials(email, password);
    await this.submit();
  }

  async getErrorText() {
    await this.errorBanner.waitFor({ state: 'visible', timeout: 5_000 });
    return this.errorBanner.textContent();
  }
}

module.exports = { AuthModal };
