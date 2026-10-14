import { test, expect } from '@playwright/test';
import { apiAs, stateFile } from './helpers';

// Wave 12 (2.8) — Administration/Operations in the browser's language (Accept-Language, without
// a saved preference): the server delivers the page translated by the catalog, the
// hub-i18n.js translates what the JavaScript writes afterwards (toasts) and the error messages of the
// API arrive translated. Does not change the admin's preference (other specs expect pt-BR).
// Stack up (HUB_DEMO_USERS=1).

test.describe('inglês', () => {
  test.use({ storageState: stateFile('admin'), locale: 'en-US' });

  test('Administração e Operações em inglês, com toast traduzido no navegador @core', async ({ page }) => {
    await page.goto('/admin/users');
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.getByText('Accounts with access to the platform, their roles and the state of each access.')).toBeVisible();
    await expect(page.locator('body')).not.toContainText('Contas com acesso à plataforma');

    await page.goto('/admin/env');
    await expect(page.getByRole('button', { name: /New variable/ })).toBeVisible();

    await page.goto('/admin/deploys?tab=import');
    await expect(page.getByRole('heading', { name: 'Validation pipeline' })).toBeVisible();
    await expect(page.locator('body')).not.toContainText('Pipeline de validação');

    // Text written by the JavaScript after loading (toast): translated by hub-i18n.js.
    await page.waitForFunction(() => typeof (window as unknown as { hubT?: unknown }).hubT === 'function');
    await expect.poll(() => page.evaluate(() => (window as unknown as { hubT: (s: string) => string }).hubT('Variável excluída.'))).toBe('Variable deleted.');
    await page.evaluate(() => {
      const t = document.getElementById('toast')!;
      t.textContent = 'Token sem acesso ao app ativos.';
    });
    await expect(page.locator('#toast')).toHaveText('Token has no access to app ativos.');
  });
});

test.describe('navegador em espanhol', () => {
  test.use({ storageState: stateFile('admin'), locale: 'es-ES' });

  test('espanhol (fora do produto) cai em inglês, também no erro da API v1 @core', async ({ page }) => {
    await page.goto('/admin/backups');
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.getByText('Backups and snapshots').first()).toBeVisible();
    const api = await apiAs('admin');
    const r = await api.get('/api/v1/jobs/999999', { headers: { 'accept-language': 'es-ES' } });
    expect(r.status()).toBe(404);
    const p = await r.json();
    expect(p.title).toBe('Not found');
    expect(p.detail).toBe('Job not found.');
    // pt-BR continua igual.
    const pt = await (await api.get('/api/v1/jobs/999999', { headers: { 'accept-language': 'pt-BR' } })).json();
    expect(pt.title).toBe('Não encontrado');
  });
});
