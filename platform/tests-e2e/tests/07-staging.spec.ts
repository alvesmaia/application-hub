import { test, expect } from '@playwright/test';
import { CP, HUB_STAGING, USERS } from './helpers';

// "Ver em staging" → confirmação (gesto do usuário) → host de staging com sessão própria
// (hub_stg, amarrada à sessão principal) → sair no host principal encerra o staging.
// Usa um login próprio: o logout não pode derrubar a sessão salva dos outros testes.
// Hub Python: staging é um modo do mesmo host (cookie `hub_env`, ligado em /hub-env), não um host separado (F5b).
test('staging (hub Python): "Ver em staging" liga o modo no mesmo host, faixa e volta à produção', async ({ browser }) => {
  test.skip(!CP.startsWith('app-hub-'), 'só na pilha Python');
  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await ctx.newPage();
  await page.goto('/login');
  await page.fill('#email', USERS.admin.email);
  await page.fill('#password', USERS.admin.password);
  await page.click('button.auth-submit');
  await expect(page).toHaveURL(/\/$|\/applications/);
  await page.goto('/admin/deploys');
  const link = page.locator('a[href^="/hub-env?to=staging"]').first();
  await expect(link, 'há um app com staging ativo (rode 06-ops antes)').toBeAttached();
  const href = (await link.getAttribute('href'))!;
  const app = new URL(href, 'http://x').searchParams.get('next')!;
  await page.goto(href);
  await expect(page).toHaveURL(new RegExp(`${app}$`));
  await expect(page.locator('[data-hub-staging-bar]')).toBeVisible();
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 30_000 });
  await page.locator('[data-hub-staging-bar] a').click();
  await expect(page.locator('[data-hub-staging-bar]')).toHaveCount(0);
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 30_000 });
  await ctx.close();
});

test('staging: "Ver em staging" abre o host de staging; logout no principal encerra', async ({ browser }) => {
  test.skip(CP.startsWith('app-hub-'), 'pilha Rust: staging em host separado (o hub Python usa o modo acima)');
  test.setTimeout(180_000);
  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await ctx.newPage();
  await page.goto('/login');
  await page.fill('#email', USERS.admin.email);
  await page.fill('#password', USERS.admin.password);
  await page.click('button.auth-submit');
  await expect(page).toHaveURL(/\/$|\/applications/);

  await page.goto('/admin/deploys');
  const link = page.locator('a[href$="?hub_env=staging"]').first();
  await expect(link, 'há um app com staging ativo (rode 06-ops antes)').toBeVisible();
  const app = (await link.getAttribute('href'))!.split('?')[0].replace('/', '');
  await link.click();
  await expect(page.locator('h1.auth-title')).toHaveText('Ver em staging');
  await page.click('form[action^="/account/staging"] button[type="submit"]');
  await expect(page).toHaveURL(new RegExp(`^${HUB_STAGING.replace(/[.]/g, '\\.')}/${app}`), { timeout: 30_000 });
  await expect(page.locator('[data-hub-env="staging"]')).toBeVisible();
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 30_000 });
  const stagingUrl = page.url();
  // O plano de controle não existe no host de staging.
  expect((await page.request.get(`${HUB_STAGING}/admin/deploys`)).status()).toBe(404);

  // Sair no host principal (outra aba, mesmo navegador) → staging morre.
  const main = await ctx.newPage();
  await main.goto('/applications');
  await main.locator('.hub-user-menu summary').click();
  await main.locator('[data-logout]').click();
  await expect(main).toHaveURL(/\/login/);
  const r = await page.request.get(stagingUrl);
  expect(r.status()).toBe(401);
  await page.reload();
  await expect(page.locator('[data-hub-env="staging"] ~ * #app-root .app-sidebar')).toHaveCount(0);
  await ctx.close();
});
