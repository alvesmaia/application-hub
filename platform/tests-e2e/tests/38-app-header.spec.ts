import { test, expect } from '@playwright/test';
import { openApp, stateFile } from './helpers';

// Inside an app the header serves that app: its own search (manifest `search`) and the bell with its own
// notifications; the hub itself has no search box.
test.use({ storageState: stateFile('admin') });

test('dentro do app: busca do próprio app no cabeçalho e sino só com as notificações dele @core', async ({ page }) => {
  await page.goto('/applications');
  await expect(page.locator('.hub-app-search')).toHaveCount(0);
  await openApp(page, '/apps/inventory/dashboard');
  const box = page.locator('.hub-app-search input[name="q"]');
  await expect(box).toHaveAttribute('placeholder', /Buscar em Gestão de Ativos/);
  await box.fill('Notebook');
  await box.press('Enter');
  await expect(page).toHaveURL(/\/apps\/inventory\/assets\?q=Notebook/);
  await expect(page.locator('#app-root input.table-search[name="q"]')).toHaveValue('Notebook');
  // the app's own text-ink role (app.css: --app-ink) reaches its fields; the catalog color no longer overwrites it
  const ink = await page.evaluate(() => {
    const r = document.getElementById('app-root')!;
    return { role: getComputedStyle(r).getPropertyValue('--color-text-ink').trim().toLowerCase(), field: getComputedStyle(r.querySelector('input.table-search')!).color };
  });
  expect(ink.role).not.toBe('#0f766e');
  expect(ink.field).not.toBe('rgb(15, 118, 110)');
  await page.locator('.hub-notif-menu > summary').click();
  await expect(page.locator('.hub-notif-popover .hub-popover-title')).toContainText('Gestão de Ativos');
  await expect(page.locator('.hub-notif-popover a.hub-notif-all')).toHaveAttribute('href', '/inbox');
  await openApp(page, '/apps/collector');
  await expect(page.locator('.hub-app-search')).toHaveCount(0); // no `search` in its manifest
});
