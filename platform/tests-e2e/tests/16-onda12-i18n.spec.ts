import { test, expect } from '@playwright/test';
import { apiAs, stateFile } from './helpers';

// Onda 12 (2.8) — Administração/Operações no idioma do navegador (Accept-Language, sem
// preferência salva): o servidor entrega a página traduzida pelo catálogo, o
// hub-i18n.js traduz o que o JavaScript escreve depois (toasts) e as mensagens de erro da
// API chegam traduzidas. Não muda a preferência do admin (outras specs esperam pt-BR).
// Compose no ar (HUB_DEMO_USERS=1).

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

    // Texto escrito pelo JavaScript depois da carga (toast): traduzido pelo hub-i18n.js.
    await page.waitForFunction(() => typeof (window as unknown as { hubT?: unknown }).hubT === 'function');
    await expect.poll(() => page.evaluate(() => (window as unknown as { hubT: (s: string) => string }).hubT('Variável excluída.'))).toBe('Variable deleted.');
    await page.evaluate(() => {
      const t = document.getElementById('toast')!;
      t.textContent = 'Token sem acesso ao app ativos.';
    });
    await expect(page.locator('#toast')).toHaveText('Token has no access to app ativos.');
  });
});

test.describe('espanhol', () => {
  test.use({ storageState: stateFile('admin'), locale: 'es-ES' });

  test('Operações em espanhol e erro da API v1 traduzido @core', async ({ page }) => {
    await page.goto('/admin/backups');
    await expect(page.locator('html')).toHaveAttribute('lang', 'es');
    await expect(page.getByText('Copias de seguridad y snapshots').first()).toBeVisible();
    const api = await apiAs('admin');
    const r = await api.get('/api/v1/jobs/999999', { headers: { 'accept-language': 'es-ES' } });
    expect(r.status()).toBe(404);
    const p = await r.json();
    expect(p.title).toBe('No encontrado');
    expect(p.detail).toBe('Job no encontrado.');
    // pt-BR continua igual.
    const pt = await (await api.get('/api/v1/jobs/999999', { headers: { 'accept-language': 'pt-BR' } })).json();
    expect(pt.title).toBe('Não encontrado');
  });
});
