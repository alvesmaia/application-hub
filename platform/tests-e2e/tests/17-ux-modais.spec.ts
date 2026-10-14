import { test, expect, type Page, type Locator } from '@playwright/test';
import { openApp, stateFile } from './helpers';

// Modals with a form do not close when clicking outside (what was typed would be lost).
// They close only through the Close/Cancel button or Esc — and Esc is ignored when the form has changed.
test.use({ storageState: stateFile('admin') });

/** Clicks the darkened background, away from the modal box. */
async function clickBackdrop(page: Page, overlay: Locator) {
  const box = (await overlay.boundingBox())!;
  await page.mouse.click(box.x + 8, box.y + box.height - 8);
}

test('modal do hub (Aplicações › Variáveis): clicar fora não fecha nem perde o texto @core', async ({ page }) => {
  await page.goto('/admin/env');
  await page.click('[data-admin-add="env"]');
  const overlay = page.locator('.overlay').last();
  const modal = overlay.locator('.modal');
  await expect(modal).toBeVisible();
  // Clean: clicking outside does not close; Esc closes.
  await clickBackdrop(page, overlay);
  await expect(modal).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(modal).toHaveCount(0);
  // Changed: clicking outside and Esc do not close; the text is still there.
  await page.click('[data-admin-add="env"]');
  await modal.locator('[data-field="name"]').fill('E2E_NAO_PERDER');
  await clickBackdrop(page, overlay);
  await expect(modal).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(modal).toBeVisible();
  await expect(modal.locator('[data-field="name"]')).toHaveValue('E2E_NAO_PERDER');
  // Cancel closes.
  await modal.locator('.modal-cancel').click();
  await expect(modal).toHaveCount(0);
});

test('modal de app (data-hub-modal, hub-kit): clicar fora não fecha nem perde o texto @core', async ({ page }) => {
  await openApp(page, '/apps/ativos/movements');
  const trigger = page.locator('[data-hub-modal]').first();
  await expect(trigger).toBeVisible({ timeout: 30_000 });
  await trigger.click();
  const overlay = page.locator('.overlay[data-hub-layer]').last();
  const modal = overlay.locator('.modal');
  await expect(modal).toBeVisible();
  const input = modal.locator('input').first();
  await input.fill('Notebook digitado');
  await clickBackdrop(page, overlay);
  await expect(modal).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(modal).toBeVisible();
  await expect(input).toHaveValue('Notebook digitado');
  await modal.locator('[data-hub-close]').first().click();
  await expect(overlay).toHaveCount(0);
  // Unchanged, Esc closes.
  await trigger.click();
  await expect(page.locator('.overlay[data-hub-layer] .modal')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.overlay[data-hub-layer]')).toHaveCount(0);
});
