import { test, expect } from '@playwright/test';
import { stateFile } from '../tests/helpers';

// Páginas-chave do hub e dos fragmentos dos apps (Rust e Python). Máscaras cobrem o
// que muda sozinho (horários relativos, console de logs, gráficos ao vivo).
const PAGES: [string, string][] = [
  ['inicio', '/'],
  ['aplicacoes', '/applications'],
  ['admin-config', '/admin'],
  ['admin-variaveis', '/admin/env'],
  ['admin-funcoes', '/admin/roles'],
  ['admin-usuarios', '/admin/users'],
  ['admin-saude', '/admin/health'],
  ['ops-implantacoes', '/admin/deploys'],
  ['ops-backups', '/admin/backups'],
  ['app-ativos', '/apps/ativos'],
  ['app-servicos', '/apps/servicos/dashboard'],
];

test('login', async ({ page }) => {
  await page.goto('/login');
  await expect(page).toHaveScreenshot('login.png', { fullPage: true });
});

test.describe('autenticado', () => {
  test.use({ storageState: stateFile('admin') });
  for (const [name, path] of PAGES) {
    test(name, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState('networkidle').catch(() => {});
      // Apps: espera o fragmento substituir o loader/skeleton.
      if (path.startsWith('/apps/ativos') || path.startsWith('/apps/servicos')) {
        await page.waitForFunction(() => !document.querySelector('.loader-fill') && !!document.querySelector('.workspace'), null, { timeout: 20_000 });
        await page.waitForTimeout(1500);
      } else {
        await page.waitForTimeout(600);
      }
      await expect(page).toHaveScreenshot(`${name}.png`, {
        fullPage: false,
        mask: [page.locator('.platform-rail'), page.locator('time, .rel-time, [data-rel], .log-console, .notif-badge, .timeline-meta, .audit-when, .hub-list small')],
      });
    });
  }
});

// Onda 9: as mesmas telas no tema escuro (escolha do usuário gravada no servidor) e o
// login com o SO escuro — continua claro (tema padrão = claro; login sempre claro).
test('login (SO escuro → claro)', async ({ browser }) => {
  const ctx = await browser.newContext({ colorScheme: 'dark', viewport: { width: 1440, height: 900 }, locale: 'pt-BR' });
  const page = await ctx.newPage();
  await page.goto('/login');
  await expect(page).toHaveScreenshot('login-os-dark.png', { fullPage: true });
  await ctx.close();
});

test.describe('autenticado — tema escuro', () => {
  test.use({ storageState: stateFile('admin') });
  test.beforeAll(async ({ playwright }) => {
    const api = await playwright.request.newContext({ baseURL: process.env.HUB || 'http://localhost:8080', storageState: stateFile('admin'), extraHTTPHeaders: { origin: process.env.HUB || 'http://localhost:8080' } });
    await api.put('/api/me/prefs', { data: { theme: 'dark' } });
    await api.dispose();
  });
  test.afterAll(async ({ playwright }) => {
    const api = await playwright.request.newContext({ baseURL: process.env.HUB || 'http://localhost:8080', storageState: stateFile('admin'), extraHTTPHeaders: { origin: process.env.HUB || 'http://localhost:8080' } });
    await api.put('/api/me/prefs', { data: { theme: '' } });
    await api.dispose();
  });
  for (const [name, path] of PAGES) {
    test(`${name} (escuro)`, async ({ page }) => {
      await page.goto(path);
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
      await page.waitForLoadState('networkidle').catch(() => {});
      if (path.startsWith('/apps/ativos') || path.startsWith('/apps/servicos')) {
        await page.waitForFunction(() => !document.querySelector('.loader-fill') && !!document.querySelector('.workspace'), null, { timeout: 20_000 });
        await page.waitForTimeout(1500);
      } else {
        await page.waitForTimeout(600);
      }
      await expect(page).toHaveScreenshot(`${name}-dark.png`, {
        fullPage: false,
        mask: [page.locator('.platform-rail'), page.locator('time, .rel-time, [data-rel], .log-console, .notif-badge, .timeline-meta, .audit-when, .hub-list small')],
      });
    });
  }
});
