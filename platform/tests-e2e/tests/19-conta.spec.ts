import { test, expect, request, type Page } from '@playwright/test';
import { createHash, randomBytes } from 'node:crypto';
import path from 'node:path';
import { HUB, apiAs, json, newRoleContext, stateFile, uniq } from './helpers';

// Account › Authorized apps and Personal tokens in the platform shell (they are not
// credential screens); button contrast (including <a> that look like buttons) on the
// credential and account screens, light and dark; Administration submenu groups; Customize
// on Home. Stack up (HUB_DEMO_USERS=1).

test.use({ storageState: stateFile('admin') });
const RUN = uniq();
const DIST = path.resolve(__dirname, '..', '..', 'dist');
const REDIRECT = 'http://127.0.0.1:53682/e2e-callback';

async function inShell(page: Page) {
  await expect(page.locator('header.platform-header')).toBeVisible();
  await expect(page.locator('aside.platform-rail')).toBeVisible();
  await expect(page.locator('details.hub-user-menu')).toHaveCount(1);
  await expect(page.locator('.auth-card')).toHaveCount(0);
}

/** WCAG contrast of all visible buttons (button, .primary/.secondary-button, a.btn). */
async function buttonContrast(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const parse = (c: string) => {
      const m = c.match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const [r, g, b, a = '1'] = m[1].split(/[ ,/]+/).filter(Boolean);
      return [Number(r), Number(g), Number(b), Number(a)];
    };
    const lum = ([r, g, b]: number[]) => {
      const f = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const bgOf = (el: Element | null): number[] => {
      while (el) {
        const c = parse(getComputedStyle(el).backgroundColor);
        if (c && c[3] > 0.5) return c;
        el = el.parentElement;
      }
      return [255, 255, 255, 1];
    };
    const bad: string[] = [];
    document.querySelectorAll('button, .primary-button, .secondary-button, a.btn, .btn').forEach((el) => {
      const r = (el as HTMLElement).getBoundingClientRect();
      const text = (el.textContent || '').trim();
      if (!r.width || !r.height || !text || getComputedStyle(el).visibility === 'hidden') return;
      if (el.closest('.rail-submenu:not(.open), [hidden], dialog:not([open]), details:not([open]) > :not(summary)')) return;
      const fg = parse(getComputedStyle(el).color);
      if (!fg) return;
      const l1 = lum(fg); const l2 = lum(bgOf(el));
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      if (ratio < 4.5) bad.push(`${el.tagName}.${(el as HTMLElement).className} "${text.slice(0, 30)}" ${ratio.toFixed(2)}`);
    });
    return bad;
  });
}

async function consent(page: Page, name: string) {
  const api = await request.newContext({ baseURL: HUB });
  const reg = await api.post('/oauth/register', { data: { client_name: name, redirect_uris: [REDIRECT], token_endpoint_auth_method: 'none' } });
  expect(reg.status()).toBe(201);
  const cid = (await reg.json()).client_id as string;
  const verifier = randomBytes(48).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  const q = new URLSearchParams({
    response_type: 'code', client_id: cid, redirect_uri: REDIRECT, code_challenge: challenge,
    code_challenge_method: 'S256', state: 'e2e', scope: 'mcp', resource: `${process.env.HUB_PUBLIC_URL || HUB}/mcp`,
  });
  let callback = '';
  page.on('request', (r) => { if (r.url().startsWith(REDIRECT)) callback = r.url(); });
  await page.goto(`/oauth/authorize?${q}`);
  await page.locator('[data-oauth-approve]').click();
  await expect.poll(() => callback).toContain('code=');
  const code = new URL(callback).searchParams.get('code')!;
  const tok = await api.post('/oauth/token', {
    form: { grant_type: 'authorization_code', code, redirect_uri: REDIRECT, client_id: cid, code_verifier: verifier, resource: `${process.env.HUB_PUBLIC_URL || HUB}/mcp` },
  });
  expect(tok.status()).toBe(200);
}

test('apps autorizados: no shell, estado vazio, cliente listado após consentimento e revogação @core', async ({ page, browser }) => {
  // Reader without grants: empty state inside the shell.
  const ctx = await newRoleContext(browser, 'leitor');
  const lp = await ctx.newPage();
  await lp.goto('/');
  await lp.locator('details.hub-user-menu > summary').click();
  await lp.locator('.hub-user-popover').getByRole('link', { name: 'Minha conta' }).click();
  await lp.locator('nav.hub-tabbar').getByRole('link', { name: 'Acessos' }).click();
  await expect(lp).toHaveURL(/\/account\/access$/);
  await inShell(lp);
  await expect(lp.locator('.eyebrow').first()).toHaveText(/Conta/i);
  await expect(lp.getByRole('heading', { level: 2, name: 'Apps autorizados' })).toBeVisible();
  await expect(lp.locator('[data-oauth-empty]')).toContainText('Nenhum aplicativo conectado');
  await expect(lp.locator('body')).not.toContainText('/api/me/tokens');
  await expect(lp.locator('body')).not.toContainText('hubt_');
  expect(await buttonContrast(lp)).toEqual([]);
  await lp.setViewportSize({ width: 1440, height: 900 });
  await lp.screenshot({ path: path.join(DIST, 'fix-authorized-apps.png') });
  await ctx.close();

  // Admin authorizes a client: it appears with return host, permissions and "Not verified".
  const name = `Langdock Conta ${RUN}`;
  await consent(page, name);
  await page.goto('/account/access');
  await inShell(page);
  const row = page.locator('[data-oauth-grant]', { hasText: name });
  await expect(row).toContainText('127.0.0.1:53682');
  await expect(row).toContainText('mcp');
  await expect(row).toContainText('Não verificado');
  // Cancel in the dialog keeps it; confirm revokes.
  await row.locator('[data-oauth-revoke]').click();
  await expect(page.locator('#revoke-app')).toBeVisible();
  await page.locator('#revoke-app [data-acct-cancel]').click();
  await expect(row).toBeVisible();
  await row.locator('[data-oauth-revoke]').click();
  await page.locator('#revoke-app [data-acct-ok]').click();
  await expect(page.locator('[data-acct-flash]')).toHaveText('Acesso revogado.');
  await expect(page.locator('[data-oauth-grant]', { hasText: name })).toHaveCount(0);
});

test('tokens pessoais: criar (mostrado uma vez) e revogar @core', async ({ page }) => {
  await page.goto('/');
  await page.locator('details.hub-user-menu > summary').click();
  await page.locator('.hub-user-popover').getByRole('link', { name: 'Minha conta' }).click();
  await page.locator('nav.hub-tabbar').getByRole('link', { name: 'Acessos' }).click();
  await expect(page).toHaveURL(/\/account\/access$/);
  await inShell(page);
  const name = `e2e-token-${RUN}`;
  await page.locator('[data-acct-new-token]').click();
  const dlg = page.locator('#new-token');
  await expect(dlg).toBeVisible();
  await expect(dlg.locator('select[name="expires_days"] option')).toHaveCount(4);
  await dlg.locator('#tok-name').fill(name);
  await dlg.locator('input[name="actions"][value="mcp"]').check();
  await dlg.locator('select[name="expires_days"]').selectOption('30');
  await dlg.getByRole('button', { name: 'Criar token' }).click();
  const secret = dlg.locator('[data-token-secret]');
  await expect(secret).toHaveText(/^hubt_/);
  const value = (await secret.textContent())!;
  await page.screenshot({ path: path.join(DIST, 'fix-tokens.png') });
  // The token works in the API.
  const tapi = await request.newContext({ baseURL: HUB, extraHTTPHeaders: { authorization: `Bearer ${value}` } });
  expect((await tapi.get('/api/v1/apps')).status()).toBe(200);
  await dlg.locator('[data-token-done]').click();
  // After closing, the list shows the token and the secret no longer appears on the page.
  const row = page.locator('[data-token-row]', { hasText: name });
  await expect(row).toBeVisible();
  await expect(row).toContainText('api:read');
  await expect(row).toContainText('mcp');
  expect(await page.content()).not.toContain(value);
  expect(await buttonContrast(page)).toEqual([]);
  await row.locator('[data-token-revoke]').click();
  await page.locator('#revoke-token [data-acct-ok]').click();
  await expect(page.locator('[data-token-row]', { hasText: name })).toHaveCount(0);
  expect((await tapi.get('/api/v1/apps')).status()).toBe(401);
  const mine = await json(await (await apiAs('admin')).get('/api/me/tokens'));
  expect(mine.items.find((x: { name: string; active: boolean }) => x.name === name)?.active).toBe(false);
});

test('contraste dos botões (claro e escuro) nas telas de credencial e da conta @core', async ({ browser }) => {
  for (const scheme of ['light', 'dark'] as const) {
    // Login (no session), using the system theme.
    const anon = await browser.newContext({ storageState: { cookies: [], origins: [] }, colorScheme: scheme });
    const ap = await anon.newPage();
    await ap.goto('/login');
    expect(await buttonContrast(ap), `login ${scheme}`).toEqual([]);
    await anon.close();
    // Reader ("System" preference): account, consent and password change.
    const ctx = await browser.newContext({ storageState: stateFile('leitor'), colorScheme: scheme });
    const lapi = await apiAs('leitor');
    await lapi.post('/account/prefs?next=%2F', { form: { theme: '' }, maxRedirects: 0 });
    const p = await ctx.newPage();
    for (const url of ['/account', '/account/security', '/account/access', '/account/password', '/inbox']) {
      await p.goto(url);
      expect(await buttonContrast(p), `${url} ${scheme}`).toEqual([]);
    }
    await ctx.close();
  }
});

test('Rail: seções administrativas no nível principal (sem "Administração"), com RBAC @core', async ({ page, browser }) => {
  await page.goto('/');
  // Without the "Administration" parent item: the 5 sections are visible already on Home, after a divider.
  await expect(page.locator('[data-rail-toggle]')).toHaveCount(0);
  await expect(page.locator('.rail-nav > .rail-divider')).toHaveCount(1);
  await expect(page.locator('#hub-admin-group-access')).toBeVisible();
  await expect(page.locator('#hub-admin-menu .rail-group-label .rail-group-icon svg')).toHaveCount(4);
  // Distinct icons on the rail (sections and items).
  const svgs = await page.locator('.rail-nav .rail-icon svg, .rail-nav .rail-group-icon svg').evaluateAll((els) => els.map((e) => e.innerHTML));
  expect(new Set(svgs).size).toBe(svgs.length);
  await page.goto('/admin/users');
  await expect(page.locator('.eyebrow').first()).toHaveText('Acesso');
  const groups = page.locator('#hub-admin-menu .rail-group');
  await expect(groups).toHaveCount(4);
  await expect(page.locator('#hub-admin-menu .rail-group-label')).toContainText(['Acesso', 'Aplicações', 'Operação', 'Governança']);
  for (const g of await groups.all()) {
    const id = await g.getAttribute('aria-labelledby');
    await expect(g).toHaveAttribute('role', 'group');
    await expect(page.locator(`#${id}`)).toBeVisible();
  }
  await expect(groups.nth(0).locator('a.rail-subitem').first()).toHaveAttribute('href', '/admin/users');
  await expect(page.locator('#hub-admin-menu a[href="/developers"]')).toHaveCount(0);  // lives in the user menu
  await expect(page.locator('#hub-admin-menu a.rail-subitem.active')).toHaveAttribute('href', '/admin/users');
  // N1: by default only the current page's section opens (the rail fits at 1280×720).
  await page.evaluate(() => { for (const k of Object.keys(localStorage)) if (k.startsWith('hub-admin-groups:')) localStorage.removeItem(k); });
  await page.reload();
  await expect(page.locator('#hub-admin-group-access')).toHaveAttribute('aria-expanded', 'true');
  for (const g of ['apps', 'ops', 'gov', 'platform']) {
    const b = page.locator(`#hub-admin-group-${g}`);
    if (await b.count()) await expect(b).toHaveAttribute('aria-expanded', 'false');
  }
  await page.locator('#hub-admin-menu .rail-group-label', { hasText: 'Governança' }).click();
  await page.locator('#hub-admin-menu a[href="/admin"]').click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.locator('#hub-admin-menu a.rail-subitem.active')).toHaveAttribute('href', '/admin');
  // Collapsible groups: the user's choice persists; the current page's group always opens.
  const opsBtn = page.locator('#hub-admin-group-ops');
  await expect(opsBtn).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#hub-admin-group-ops-items a[href="/admin/logs"]')).toBeHidden();
  await expect(opsBtn).not.toContainText('(');
  await opsBtn.click();
  await expect(opsBtn).toHaveAttribute('aria-expanded', 'true');
  await page.reload();
  await expect(page.locator('#hub-admin-group-ops')).toHaveAttribute('aria-expanded', 'true');
  // Teclado: Enter recolhe e reabre.
  await page.locator('#hub-admin-group-ops').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#hub-admin-group-ops')).toHaveAttribute('aria-expanded', 'false');
  await page.reload();
  await expect(page.locator('#hub-admin-group-ops')).toHaveAttribute('aria-expanded', 'false');
  await page.goto('/admin/logs');
  await expect(page.locator('#hub-admin-group-ops')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#hub-admin-menu a[href="/admin/logs"]')).toBeVisible();
  await page.evaluate(() => { for (const k of Object.keys(localStorage)) if (k.startsWith('hub-admin-groups:')) localStorage.removeItem(k); });
  await page.reload();
  await page.locator('aside.platform-rail').screenshot({ path: path.join(DIST, 'rail-flat.png') });

  // Operator: only sees the allowed items; a group without items does not show the label.
  const ctx = await newRoleContext(browser, 'operador');
  const op = await ctx.newPage();
  await op.goto('/');
  const links = op.locator('#hub-admin-menu a.rail-subitem');
  const n = await links.count();
  expect(n).toBeGreaterThan(0);
  for (const g of await op.locator('#hub-admin-menu .rail-group').all()) {
    expect(await g.locator('a.rail-subitem').count()).toBeGreaterThan(0);
  }
  const href = await links.first().getAttribute('href');
  await links.first().evaluate((a) => (a as HTMLAnchorElement).click());
  await expect(op).toHaveURL(new RegExp(`${href!.replace(/\//g, '\\/')}$`));
  await ctx.close();
  // Reader: no administrative section.
  const lctx = await newRoleContext(browser, 'leitor');
  const lp = await lctx.newPage();
  await lp.goto('/');
  await expect(lp.locator('#hub-admin-menu')).toHaveCount(0);
  await expect(lp.locator('.rail-nav > .rail-divider')).toHaveCount(0);
  await lctx.close();
});

test('Início › Personalizar: seletores com opções, Cancelar desfaz e Salvar persiste @core', async ({ page }) => {
  const api = await apiAs('admin');
  await api.delete('/api/me/home');
  await page.goto('/');
  await page.locator('[data-home-customize]').click();
  const editor = page.locator('[data-home-editor]');
  await expect(editor).toBeVisible();
  for (const sel of await editor.locator('select').all()) {
    expect(await sel.locator('option').count()).toBeGreaterThan(0);
  }
  const order = async () => page.locator('.hub-widget:not([hidden])').evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.widget));
  const initial = await order();
  const first = initial[0]!;
  // Remove and cancel: comes back as it was.
  await page.locator(`[data-widget="${first}"] [data-widget-remove]`).click();
  await expect(page.locator(`[data-widget="${first}"]`)).toBeHidden();
  // The "Add" selector now has the removed widget as an enabled option.
  await expect(editor.locator(`[data-home-add] option[value="${first}"]:not([disabled])`)).toHaveCount(1);
  await editor.locator('[data-home-cancel]').click();
  await expect(editor).toBeHidden();
  expect(await order()).toEqual(initial);
  // Remove and save: persists and holds after reloading.
  await page.locator('[data-home-customize]').click();
  await page.locator(`[data-widget="${first}"] [data-widget-remove]`).click();
  await editor.locator('[data-home-done]').click();
  await expect(editor).toBeHidden();
  const saved = initial.filter((w) => w !== first);
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).layout).toEqual(saved);
  await page.reload();
  expect(await order()).toEqual(saved);
  await api.delete('/api/me/home');
});
