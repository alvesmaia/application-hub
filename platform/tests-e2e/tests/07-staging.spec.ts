import { test, expect } from '@playwright/test';
import { CP, USERS } from './helpers';

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
