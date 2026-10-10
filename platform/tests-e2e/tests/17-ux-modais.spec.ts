import { test, expect, type Page, type Locator } from '@playwright/test';
import { openApp, stateFile } from './helpers';

// Modais com formulário não fecham ao clicar fora (o que foi digitado se perderia).
// Fecham só pelo botão Fechar/Cancelar ou por Esc — e Esc é ignorado com o formulário alterado.
test.use({ storageState: stateFile('admin') });

/** Clica no fundo escurecido, longe da caixa do modal. */
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
  // Limpo: clicar fora não fecha; Esc fecha.
  await clickBackdrop(page, overlay);
  await expect(modal).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(modal).toHaveCount(0);
  // Alterado: clicar fora e Esc não fecham; o texto continua lá.
  await page.click('[data-admin-add="env"]');
  await modal.locator('[data-field="name"]').fill('E2E_NAO_PERDER');
  await clickBackdrop(page, overlay);
  await expect(modal).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(modal).toBeVisible();
  await expect(modal.locator('[data-field="name"]')).toHaveValue('E2E_NAO_PERDER');
  // Cancelar fecha.
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
  // Sem alteração, Esc fecha.
  await trigger.click();
  await expect(page.locator('.overlay[data-hub-layer] .modal')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.overlay[data-hub-layer]')).toHaveCount(0);
});
