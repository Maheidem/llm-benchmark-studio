/**
 * Modal interaction helpers.
 * Extracted from zai-provider-setup.spec.js and stress-test.spec.js to eliminate duplication.
 */

/** Click danger-confirm modal button (Delete/Remove) and wait for hidden */
async function confirmDangerModal(page) {
  const modal = page.locator('.modal-overlay');
  await modal.waitFor({ state: 'visible', timeout: 5_000 });
  await modal.locator('.modal-btn-danger').click();
  await modal.waitFor({ state: 'hidden', timeout: 5_000 });
}

/** Click confirm modal button and wait for hidden */
async function confirmModal(page) {
  const modal = page.locator('.modal-overlay');
  await modal.waitFor({ state: 'visible', timeout: 5_000 });
  await modal.locator('.modal-btn-confirm').click();
  await modal.waitFor({ state: 'hidden', timeout: 5_000 });
}

/** Fill modal input, click confirm, wait for hidden */
async function fillModalInput(page, value) {
  const modal = page.locator('.modal-overlay');
  await modal.waitFor({ state: 'visible', timeout: 5_000 });
  await modal.locator('.modal-input').fill(value);
  await modal.locator('.modal-btn-confirm').click();
  await modal.waitFor({ state: 'hidden', timeout: 5_000 });
}

module.exports = { confirmDangerModal, confirmModal, fillModalInput };
