import { test, expect } from '@playwright/test';
import path from 'node:path';
import { HUB, apiAs, json, newRoleContext, stateFile } from './helpers';

// Second round of the UI review (dist/ui-review.md, "Re-review"): theme in 3 icons,
// real version in the catalog, Recents without a test route,
// own account without "Deactivate", 403 with a page header, login with show password.
// Stack up (HUB_DEMO_USERS=1).

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
  // Light + dark side by side in one file (dist/theme-switch.png).
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
  // The last section (Governance) appears without scrolling the page.
  await expect(page.locator('#hub-admin-group-gov')).toBeInViewport();
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
  const prod = list.find((d) => d.app === 'servicedesk' && d.env === 'Produção' && d.status === 'Implantado');
  if (prod) {
    await expect(page.locator('.app-card[data-app="servicedesk"] .app-version')).toHaveText(`v${prod.version.replace(/^v/, '')}`);
  }
});

test('Recentes: sem rota de teste e sem repetidos; própria conta sem "Desativar" @core', async ({ page }) => {
  await page.goto('/apps/inventory');
  await page.goto('/apps/inventory/e2e-hostil').catch(() => undefined);
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

test('menu "⋯" da última linha abre para cima, inteiro dentro do painel @core', async ({ page }) => {
  await page.goto('/admin/users');
  const last = page.locator('table details.hub-row-menu').last();
  await last.scrollIntoViewIfNeeded();
  await last.locator('summary').click();
  await expect(last).toHaveClass(/hub-row-menu--up/);
  const fits = await last.evaluate((d) => {
    const l = d.querySelector('.hub-row-menu-list')!.getBoundingClientRect();
    const p = d.closest('.content-panel')!.getBoundingClientRect();
    return l.top >= p.top && l.bottom <= p.bottom;
  });
  expect(fits).toBe(true);
});

test('"i" ao lado do título abre o painel com a descrição da página @core', async ({ page }) => {
  await page.goto('/admin/tasks');
  await expect(page.locator('.page-subtitle')).toHaveCount(0);
  const btn = page.locator('[data-hub-info-open]');
  await btn.click();
  const panel = page.locator('#hub-page-info');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('Importações, exportações e outros processos longos');
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(btn).toBeFocused();
});

test('versão do hub visível no rodapé do menu lateral para quem está logado @core', async ({ page }) => {
  await page.goto('/admin/tasks');
  await expect(page.locator('.rail-footer [data-hub-version]')).toBeVisible();
  await expect(page.locator('.rail-footer [data-hub-version]')).toHaveText(/^v\d+\.\d+\.\d+/);
});

test('hover da linha de /admin/users pinta também a faixa sob a coluna Ações', async ({ page }) => {
  await page.goto('/admin/users');
  const row = page.locator('tbody tr').nth(1);
  const b = (await row.boundingBox())!;
  await page.mouse.move(b.x + 200, b.y + 15);
  await expect.poll(() => row.evaluate((tr) => getComputedStyle((tr as HTMLTableRowElement).cells[0]).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
  const [cell, under] = await page.evaluate(([x, y, cx, cy]) => {
    const bgOf = (e: Element | null) => { for (let n = e; n; n = n.parentElement) { const c = getComputedStyle(n).backgroundColor; if (c !== 'rgba(0, 0, 0, 0)') return c; } return ''; };
    return [bgOf(document.elementFromPoint(cx, cy)), bgOf(document.elementFromPoint(x, y))];
  }, [b.x + b.width - 40, b.y + b.height - 4, b.x + 200, b.y + 15]);
  expect(under).toBe(cell);
});

test('Containers: capacidade do host com uso medido, escala, gráfico de 5 min, valor/total no hover e runtime no "i" @core', async ({ page }) => {
  await page.goto('/admin/runtime');
  const panel = page.locator('[data-ops-host]');
  await expect(panel.locator('.ops-hm-head').first()).toHaveText(/^CPU\s*[0-9,]+ \/ [0-9,]+ vCPU · [0-9]+%$/);
  const titles = await panel.locator('.ops-seg').evaluateAll((els) => els.map((e) => e.getAttribute('title') || ''));
  expect(titles.length).toBe(8);
  for (const t of titles) expect(t).toMatch(/[0-9,]+ \/ [0-9,]+ (vCPU|GiB) \([0-9,]+%\)$/);
  await expect(panel.locator('.ops-hm-scale').first().locator('i')).toHaveCount(5);
  await expect(panel.locator('.ops-chart svg')).toHaveCount(2);
  await expect(panel.locator('.ops-chart .ops-area')).toHaveCount(6);
  await expect(panel.locator('.ops-chart-hit title').first()).toHaveText(/Total [0-9,]+ \/ [0-9,]+ vCPU · Sistema/);
  await expect(panel.locator('.hub-info-toggle')).toHaveAttribute('title', /^Runtime: /);
  await expect(page.locator('#ops-socket')).toHaveCount(0);
  // refreshed by itself: the panel node is replaced within the polling interval
  await panel.evaluate((el) => el.setAttribute('data-old', '1'));
  await expect(page.locator('[data-ops-host][data-old]')).toHaveCount(0, { timeout: 12_000 });
});
