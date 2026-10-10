import { test, expect } from '@playwright/test';
import path from 'node:path';
import { HUB, apiAs, json, newRoleContext, stateFile } from './helpers';

// Segunda rodada da revisão de UI (dist/ui-review.md, "Re-revisão"): tema em 3 ícones,
// busca com o interno por último, versão real no catálogo, Recentes sem rota de teste,
// própria conta sem "Desativar", 403 com cabeçalho de página, login com mostrar senha.
// Compose no ar (HUB_DEMO_USERS=1).

test.use({ storageState: stateFile('admin') });
const DIST = path.resolve(__dirname, '..', '..', 'dist');

test('Configurações: tema em 3 opções (radiogroup), claro padrão @core', async ({ page }) => {
  const api = await apiAs('admin');
  await api.put('/api/me/prefs', { data: { theme: '' } });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/account');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  const seg = page.locator('.acct-theme-seg[role="radiogroup"]');
  const radios = seg.locator('[role="radio"]');
  await expect(radios).toHaveCount(3);
  await expect(radios.nth(0)).toHaveText('Claro');
  await expect(radios.nth(1)).toHaveText('Escuro');
  await expect(radios.nth(2)).toHaveText('Automático (sistema)');
  await expect(radios.nth(0)).toHaveAttribute('aria-checked', 'true');
  const light = await page.locator('#aparencia').screenshot();
  await radios.nth(1).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(radios.nth(1)).toHaveAttribute('aria-checked', 'true');
  const dark = await page.locator('#aparencia').screenshot();
  // Claro + escuro lado a lado num só arquivo (dist/theme-switch.png).
  const sheet = await page.context().newPage();
  const img = (b: Buffer) => `<img src="data:image/png;base64,${b.toString('base64')}" style="display:block">`;
  await sheet.setContent(`<body style="margin:0;background:#888;display:flex;gap:16px;padding:16px;width:max-content">${img(light)}${img(dark)}</body>`);
  await sheet.locator('body').screenshot({ path: path.join(DIST, 'theme-switch.png') });
  await sheet.close();
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).theme).toBe('dark');
  await api.put('/api/me/prefs', { data: { theme: '' } });
  await api.dispose();
});

test('Rail a 1280×720: só a seção atual aberta e tudo acessível @core', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/admin/runtime');
  await page.evaluate(() => { for (const k of Object.keys(localStorage)) if (k.startsWith('hub-admin-groups:')) localStorage.removeItem(k); });
  await page.reload();
  const open = page.locator('#hub-admin-menu .rail-group-label[aria-expanded="true"]');
  await expect(open).toHaveCount(1);
  await expect(open).toHaveAttribute('id', 'hub-admin-group-apps');
  // A última seção (Governança) aparece sem rolar a página.
  await expect(page.locator('#hub-admin-group-gov')).toBeInViewport();
});

test('Busca: registros dos apps antes do interno; "ver todos" por app @core', async ({ page }) => {
  const api = await apiAs('admin');
  const r = await json(await api.get('/api/me/search?q=a'));
  const ids: string[] = r.groups.map((g: { id: string }) => g.id);
  if (ids.includes('admin')) expect(ids[ids.length - 1]).toBe('admin');
  const atv = r.groups.find((g: { id: string }) => g.id === 'ativos');
  expect(atv, 'registros do Ativos').toBeTruthy();
  expect(ids.indexOf('ativos')).toBeLessThan(ids.includes('admin') ? ids.indexOf('admin') : 99);
  if (atv.hits.length >= 5) expect(atv.more_url).toContain('app=ativos');
  await api.dispose();
  await page.goto('/search?q=a');
  await expect(page.locator('.hub-search-group mark').first()).toBeVisible();
  const t0 = Date.now();
  // Resultado completo (todos os provedores, teto de 1,5 s por app) em poucos segundos.
  // (networkidle não serve de métrica aqui: o SSE da inbox, /api/me/stream, fica ativo.)
  await page.goto('/search?q=a');
  await expect(page.locator('.hub-search-group').first()).toBeVisible();
  expect(Date.now() - t0).toBeLessThan(4000);
  await page.goto('/search?q=a&app=ativos');
  await expect(page.locator('.hub-search-group')).toHaveCount(1);
  await expect(page.locator('.hub-search-back a')).toBeVisible();
});

test('Catálogo: versão "vX.Y.Z" vinda da implantação ativa @core', async ({ page }) => {
  await page.goto('/applications');
  const versions = await page.locator('.app-card .app-version').allTextContents();
  expect(versions.length).toBeGreaterThan(0);
  for (const v of versions) expect(v.trim()).toMatch(/^v\d+(\.\d+)*/);
  const api = await apiAs('admin');
  const deps = await json(await api.get('/api/ops/deployments'));
  await api.dispose();
  const list = (Array.isArray(deps) ? deps : deps.deployments || []) as { app: string; env: string; status: string; version: string }[];
  const prod = list.find((d) => d.app === 'servicos' && d.env === 'Produção' && d.status === 'Implantado');
  if (prod) {
    await expect(page.locator('.app-card[data-app="servicos"] .app-version')).toHaveText(`v${prod.version.replace(/^v/, '')}`);
  }
});

test('Recentes: sem rota de teste e sem repetidos; própria conta sem "Desativar" @core', async ({ page }) => {
  await page.goto('/apps/ativos');
  await page.goto('/apps/ativos/e2e-hostil').catch(() => undefined);
  await page.goto('/');
  const recents = page.locator('[data-widget="recents"] a, .hub-recents a');
  const hrefs = await recents.evaluateAll((els) => els.map((e) => e.getAttribute('href')));
  expect(hrefs.some((h) => (h || '').includes('e2e'))).toBe(false);
  expect(new Set(hrefs).size).toBe(hrefs.length);
  await page.goto('/admin/users');
  const me = page.locator('tr', { hasText: 'admin@demo.local' });
  await me.locator('summary.row-action').click();
  await expect(me.locator('[data-user-toggle]')).toHaveCount(0);
  await expect(me.locator('.hub-row-menu-list button', { hasText: 'Desativar' })).toHaveCount(0);
});

test('403 com cabeçalho de página; login com "Mostrar senha" @core', async ({ browser }) => {
  const ctx = await newRoleContext(browser, 'leitor');
  const p = await ctx.newPage();
  await p.goto('/admin/users');
  await expect(p.locator('#app-root .page-title')).toHaveText('Acesso negado');
  await expect(p.locator('#app-root .eyebrow')).toHaveText('Acesso');
  await ctx.close();
  const anon = await browser.newContext({ baseURL: HUB, storageState: { cookies: [], origins: [] } });
  const lp = await anon.newPage();
  await lp.goto('/login');
  const toggle = lp.locator('[data-pw-toggle]');
  await expect(toggle).toBeVisible();
  await expect(lp.locator('#password')).toHaveAttribute('type', 'password');
  await toggle.click();
  await expect(lp.locator('#password')).toHaveAttribute('type', 'text');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await anon.close();
});
