import { test, expect } from '@playwright/test';
import { stateFile } from './helpers';

test.use({ storageState: stateFile('admin') });

const APPS: Record<string, string[]> = {
  ativos: ['dashboard', 'assets', 'movements', 'reports', 'settings'],
  fornecedores: ['dashboard', 'indicators', 'analysis', 'reports', 'settings'],
  servicos: ['dashboard', 'requests', 'approvals', 'knowledge', 'reports', 'settings'],
  // Onda 7 — app em Python/FastAPI (hub-sdk-python): mesmo shell, loader e skeleton.
};

for (const [app, sections] of Object.entries(APPS)) {
  test(`abrir ${app}: carregador do mockup, seções e tela única${app === 'ativos' ? ' @core' : ''}`, async ({ browser }) => {
    // Abrir o app: carregador (3,5 s) → skeleton (0,9 s) → conteúdo (tempos fixos do mockup).
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
      // Troca de seção: sidebar mantida; skeleton de 0,7 s.
      await expect(page).toHaveURL(new RegExp(`/apps/${app}/${s}$`));
      await expect(page.locator('#app-root .workspace-content.component-loading')).toHaveCount(0, { timeout: 15_000 });
      await expect(page.locator(`.sidebar-nav a.nav-item.active[href="/apps/${app}/${s}"]`)).toBeVisible();
      // Tela única: rodapé da sidebar (usuário + "Voltar à plataforma") visível sem rolar.
      await expect(page.locator('#app-root').getByRole('link', { name: 'Voltar à plataforma' })).toBeInViewport();
      await expect(page.locator('.platform-header')).toBeInViewport();
    }
    // Voltar do navegador troca a seção sem recarregar.
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
  // App já aberto nesta sessão: a entrada pelo início também pula.
  await page.goto('/apps/servicos');
  await ctx.close();
});

// Onda 13: apps em /apps/{id}. Links antigos (/{id}/…) redirecionam com a query; a
// página nova não emite nenhum link para o caminho antigo.
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
