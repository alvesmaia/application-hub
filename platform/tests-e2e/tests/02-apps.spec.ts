import { test, expect } from '@playwright/test';
import { stateFile } from './helpers';

test.use({ storageState: stateFile('admin') });

const APPS: Record<string, string[]> = {
  ativos: ['dashboard', 'assets', 'movements', 'reports', 'settings'],
  fornecedores: ['dashboard', 'indicators', 'analysis', 'reports', 'settings'],
  servicos: ['dashboard', 'requests', 'approvals', 'knowledge', 'reports', 'settings'],
  // Wave 7 — Python/FastAPI app (hub-sdk-python): same shell, loader and skeleton.
};

for (const [app, sections] of Object.entries(APPS)) {
  test(`abrir ${app}: carregador do mockup, seções e tela única${app === 'ativos' ? ' @core' : ''}`, async ({ browser }) => {
    // Opening the app: loader (3.5 s) → skeleton (0.9 s) → content (fixed mockup times).
    const ctx = await browser.newContext({ storageState: stateFile('admin') });
    const page = await ctx.newPage();
    await page.goto('/applications');
    await page.locator(`a[href="/apps/${app}"]`).first().click();
    await expect(page).toHaveURL(new RegExp(`/apps/${app}$`));
    await expect(page.locator('#app-root .app-loading')).toBeVisible();
    await expect(page.locator('#app-root .app-loading')).toHaveCount(0, { timeout: 20_000 });
    await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 20_000 });
    await expect(page.locator('#app-root .app-sidebar')).toBeVisible();

    for (const s of sections.slice(1)) {
      await page.locator(`.sidebar-nav a.nav-item[href="/apps/${app}/${s}"]`).click();
      // Section change: sidebar kept; 0.7 s skeleton.
      await expect(page).toHaveURL(new RegExp(`/apps/${app}/${s}$`));
      await expect(page.locator('#app-root .workspace-content.component-loading')).toHaveCount(0, { timeout: 15_000 });
      await expect(page.locator(`.sidebar-nav a.nav-item.active[href="/apps/${app}/${s}"]`)).toBeVisible();
      // Single screen: sidebar footer (user + "Back to the platform") visible without scrolling.
      await expect(page.locator('#app-root').getByRole('link', { name: 'Voltar à plataforma' })).toBeInViewport();
      await expect(page.locator('.platform-header')).toBeInViewport();
    }
    // Browser back changes the section without reloading.
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/apps/${app}/${sections[sections.length - 2]}$`));
    await ctx.close();
  });
}

test('deep link e recarregar chegam ao conteúdo', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: stateFile('admin') });
  const page = await ctx.newPage();
  await page.goto('/apps/servicos/requests?status=Aberto');
  await expect(page.locator('.app-loading')).toHaveCount(0, { timeout: 10_000 });
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 10_000 });
  await page.reload();
  // App already open in this session: entering through the start also skips it.
  await page.goto('/apps/servicos');
  await ctx.close();
});

// Wave 13: apps at /apps/{id}. Old links (/{id}/…) redirect keeping the query; the
// new page does not emit any link to the old path.
test('caminho antigo do app redireciona para /apps/{id} com a query @core', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: stateFile('admin') });
  const r = await ctx.request.get('/ativos/assets?q=MacBook&status=Ativo', { maxRedirects: 0 });
  expect(r.status()).toBe(301);
  expect(r.headers()['location']).toBe('/apps/ativos/assets?q=MacBook&status=Ativo');
  const p = await ctx.request.post('/ativos/itens/export.xlsx', { maxRedirects: 0, headers: { origin: new URL(r.url()).origin } });
  expect(p.status()).toBe(308);
  const page = await ctx.newPage();
  await page.goto('/ativos/assets?q=MacBook');
  await expect(page).toHaveURL(/\/apps\/ativos\/assets\?q=MacBook$/);
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 20_000 });
  const old = await page.$$eval('[href],[action],[data-hub-get],[data-hub-post],[data-hub-poll]', (els) => els.flatMap((e) =>
    ['href', 'action', 'data-hub-get', 'data-hub-post', 'data-hub-poll'].map((a) => e.getAttribute(a) || '')
      .filter((v) => /^\/(ativos|fornecedores|servicos|contratos|coletor)([/?#]|$)/.test(v))));
  expect(old).toEqual([]);
  await ctx.close();
});
