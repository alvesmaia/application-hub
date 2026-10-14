import { test, expect } from '@playwright/test';
import { CP, USERS } from './helpers';

// "View in staging" → confirmation (user gesture) → staging host with its own session
// (hub_stg, tied to the main session) → signing out on the main host ends the staging.
// Uses its own login: the logout must not drop the others' saved session.
// Python hub: staging is a mode of the same host (cookie `hub_env`, set on /hub-env), not a separate host (F5b).
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
