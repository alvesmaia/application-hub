import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';
import { apiAs, json, newRoleContext, stateFile, uniq } from './helpers';

// My account (/account): Profile, Security and Access tabs (personal tokens and authorized apps);
// the user menu keeps only My account, tour and Sign out. 1440 and 390 (phone). Stack up (HUB_DEMO_USERS=1).
test.use({ storageState: stateFile('admin') });
const DIST = path.resolve(__dirname, '..', '..', 'dist');
const RUN = uniq();
const MOBILE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true };

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
}

test.afterAll(async () => {
  const api = await apiAs('admin');
  await api.put('/api/me/prefs', { data: { theme: '', lang: '' } });
});

test('menu do usuário só com Minha conta, Desenvolvedores, tour e Sair @core', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/applications');
  await page.locator('details.hub-user-menu > summary').click();
  const pop = page.locator('.hub-user-popover');
  await expect(pop.locator('.hub-user-who')).toBeVisible();
  await expect(pop.getByRole('link', { name: 'Minha conta' })).toBeVisible();
  const dev = pop.getByRole('link', { name: /^Desenvolvedores/ });
  await expect(dev).toHaveAttribute('href', '/developers');
  await expect(dev).toHaveAttribute('target', '_blank');
  await expect(dev).toHaveAttribute('rel', 'noopener');
  await expect(pop.getByRole('button', { name: 'Fazer o tour guiado' })).toBeVisible();
  await expect(pop.getByRole('button', { name: 'Sair' })).toBeVisible();
  await expect(pop.locator('[role="radiogroup"], select')).toHaveCount(0);
  for (const gone of ['Tokens pessoais', 'Apps autorizados', 'Alterar senha']) {
    await expect(pop.getByText(gone)).toHaveCount(0);
  }
  await pop.screenshot({ path: path.join(DIST, 'user-menu-after-light.png') });
  await pop.getByRole('link', { name: 'Minha conta' }).click();
  await expect(page).toHaveURL(/\/account$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Minha conta' })).toBeVisible();
  await expect(page.locator('.eyebrow').first()).toHaveText(/Conta/i);
});

test('seções: perfil, tema persiste, idioma aplica, senha ida e volta @core', async ({ page }) => {
  const api = await apiAs('admin');
  await api.put('/api/me/prefs', { data: { theme: '', lang: '' } });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/account');
  await expect(page.locator('nav.hub-tabbar').getByRole('link')).toHaveCount(3);
  await expect(page.locator('[data-channel-form]')).toBeVisible();
  await expect(page.locator('[data-acct-email]')).toContainText('@');
  await expect(page.locator('[data-acct-name]')).not.toBeEmpty();
  await expect(page.locator('[data-acct-role]')).not.toBeEmpty();
  await page.screenshot({ path: path.join(DIST, 'settings-1440-light.png'), fullPage: true });
  // Theme: applies right away and persists.
  const seg = page.locator('.acct-theme-seg');
  await seg.locator('[value="dark"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).theme).toBe('dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(seg.locator('[value="dark"]')).toHaveAttribute('aria-checked', 'true');
  await page.screenshot({ path: path.join(DIST, 'settings-1440-dark.png'), fullPage: true });
  await seg.locator('[value="light"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  // Language: applies without an OK button.
  await page.locator('#acct-lang-select').selectOption('en');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page).toHaveURL(/\/account$/);
  await expect(page.getByRole('heading', { level: 1, name: 'My account' })).toBeVisible();
  await page.locator('#acct-lang-select').selectOption('pt-BR');
  await expect(page.locator('html')).toHaveAttribute('lang', 'pt-BR');
  // Password (Security tab): its own credential screen, returns here.
  await page.locator('nav.hub-tabbar').getByRole('link', { name: 'Segurança' }).click();
  await page.locator('[data-acct-password]').click();
  await expect(page).toHaveURL(/\/account\/password\?next=/);
  await expect(page.locator('.auth-card')).toBeVisible();
  await page.locator('[data-pw-cancel]').click();
  await expect(page).toHaveURL(/\/account\/security$/);
});

test('tokens e apps autorizados dentro de Configurações; links antigos valem @core', async ({ page, browser }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/account');
  await page.locator('nav.hub-tabbar').getByRole('link', { name: 'Acessos' }).click();
  await expect(page).toHaveURL(/\/account\/access$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Minha conta' })).toBeVisible();
  await expect(page.locator('nav.hub-tabbar [aria-current="page"]')).toHaveText('Acessos');
  const name = `e2e-cfg-${RUN}`;
  await page.locator('[data-acct-new-token]').click();
  const dlg = page.locator('#new-token');
  await dlg.locator('#tok-name').fill(name);
  await dlg.getByRole('button', { name: 'Criar token' }).click();
  await expect(dlg.locator('[data-token-secret]')).toHaveText(/^hubt_/);
  await dlg.locator('[data-token-done]').click();
  const row = page.locator('[data-token-row]', { hasText: name });
  await expect(row).toBeVisible();
  await row.locator('[data-token-revoke]').click();
  await page.locator('#revoke-token [data-acct-ok]').click();
  await expect(page.locator('[data-token-row]', { hasText: name })).toHaveCount(0);
  // Authorized apps (a reader also accesses their own settings).
  const ctx = await newRoleContext(browser, 'leitor');
  const lp = await ctx.newPage();
  for (const url of ['/account', '/account/security', '/account/access']) {
    await lp.goto(url);
    await expect(lp.getByRole('heading', { level: 1, name: 'Minha conta' })).toBeVisible();
  }
  await lp.goto('/account/authorized-apps'); // old link: redirects to the Access tab
  await expect(lp.getByRole('heading', { level: 2, name: 'Apps autorizados' })).toBeVisible();
  await expect(lp.locator('[data-acct-apps]')).toBeVisible();
  await expect(lp.locator('nav.hub-tabbar [aria-current="page"]')).toHaveText('Acessos');
  await ctx.close();
});

test.describe('celular', () => {
  test.use(MOBILE);
  test('configurações empilhadas e menu ☰ com Configurações e Sair @core', async ({ page }) => {
    await page.goto('/applications');
    await page.locator('[data-hub-drawer-toggle]').click();
    const acct = page.locator('.hub-drawer-account');
    await expect(acct.getByRole('link', { name: 'Minha conta' })).toBeVisible();
    await expect(acct.getByRole('button', { name: 'Sair' })).toBeVisible();
    await expect(acct.locator('[role="radiogroup"], select')).toHaveCount(0);
    await page.screenshot({ path: path.join(DIST, 'user-menu-after-mobile.png') });
    await acct.getByRole('link', { name: 'Minha conta' }).click();
    await expect(page).toHaveURL(/\/account$/);
    await noOverflow(page);
    await page.screenshot({ path: path.join(DIST, 'settings-390-light.png'), fullPage: true });
    await page.locator('.acct-theme-seg [value="dark"]').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.screenshot({ path: path.join(DIST, 'settings-390-dark.png'), fullPage: true });
    await page.locator('.acct-theme-seg [value="light"]').click();
    await page.goto('/account/access');
    await noOverflow(page);
  });
});
