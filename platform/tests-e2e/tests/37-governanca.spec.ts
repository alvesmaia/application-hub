import { test, expect } from '@playwright/test';
import { apiAs, json, stateFile } from './helpers';

// Governança › Configurações: tabs Geral · Aplicações · Segurança e chaves · Uso; the "visible in the catalog"
// switch of an app saves as catalog_hidden_apps; the old #chaves link opens its tab.
test.use({ storageState: stateFile('admin') });

test('governança: abas e chave "visível no catálogo" salva @core', async ({ page }) => {
  const api = await apiAs('admin');
  const before = await json(await api.get('/api/admin/settings'));
  try {
    await page.goto('/admin');
    for (const t of ['Geral', 'Aplicações', 'Segurança e chaves', 'Uso']) await expect(page.getByRole('tab', { name: t })).toBeVisible();
    await page.getByRole('tab', { name: 'Aplicações' }).click();
    const sw = page.locator('input[name="catalog_visible_app"][value="collector"]');
    await expect(sw).toBeChecked();
    await sw.uncheck();
    await Promise.all([page.waitForEvent('load'), page.locator('#p-apps button[type="submit"]').click()]); // saving reloads
    await expect.poll(async () => (await json(await api.get('/api/admin/settings'))).catalog_hidden_apps).toContain('collector');
    await page.goto('/admin#chaves');
    await expect(page.locator('#p-chaves')).toBeVisible();
    await expect(page.locator('#p-chaves')).toContainText('Cofre de segredos');
  } finally {
    await api.put('/api/admin/settings', { data: before });
    await api.dispose();
  }
});
