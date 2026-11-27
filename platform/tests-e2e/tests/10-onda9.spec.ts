import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { apiAs, json, newRoleContext, openApp, stateFile, uniq } from './helpers';

// Wave 9 (Phase 3): guided onboarding, Inbox (notifications and approvals coming from the apps),
// customizable Home, usage analytics, dark theme,
// idiomas e acessibilidade (axe, WCAG 2.2 AA).
test.use({ storageState: stateFile('admin') });
test.describe.configure({ mode: 'serial' });

const RUN = uniq();

async function prefs(api: APIRequestContext, body: Record<string, unknown>) {
  const r = await api.put('/api/me/prefs', { data: body });
  expect(r.status(), JSON.stringify(await json(r))).toBe(200);
}

async function inboxItems(api: APIRequestContext, filter = 'all') {
  return (await json(await api.get(`/api/me/inbox?filter=${filter}`))).items as Array<Record<string, any>>;
}

test('onboarding: tour no primeiro acesso (teclado, interromper e retomar, pular) e primeiros passos', async ({ browser }) => {
  const api = await apiAs('operador');
  expect((await api.put('/api/me/onboarding', { data: { tour: '' } })).status()).toBe(200);
  const ctx = await newRoleContext(browser, 'operador');
  const page = await ctx.newPage();
  await page.goto('/');
  const tour = page.getByRole('dialog', { name: 'Seu início' });
  await expect(tour).toBeVisible();
  await expect(tour).toContainText('Passo 1 de');
  // Keyboard: → advances; focus stays in the dialog.
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.hub-tour')).toContainText('Passo 2 de');
  await expect(page.locator('.hub-tour-target')).toHaveCount(1);
  // Esc interrupts and saves the step; reloading resumes from the same point.
  await page.keyboard.press('Escape');
  await expect(page.locator('.hub-tour')).toHaveCount(0);
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).tour).toBe('step:1');
  await page.reload();
  await expect(page.locator('.hub-tour')).toContainText('Passo 2 de');
  await page.locator('.hub-tour [data-tour="skip"]').click();
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).tour).toBe('skipped');
  await page.reload();
  await page.waitForTimeout(800);
  await expect(page.locator('.hub-tour')).toHaveCount(0);
  // First steps: opening an app ticks the step by itself (server).
  await expect(page.locator('[data-widget="onboarding"]')).toBeVisible();
  await openApp(page, '/apps/suppliers/indicators');
  await page.goto('/');
  await expect(page.locator('li[data-step="open_app"]')).toHaveClass(/done/);
  // The operator (Power User) sees the administrator steps they can do.
  await expect(page.locator('li[data-step="variable"]')).toBeVisible();
  await expect(page.locator('li[data-step="package"]')).toBeVisible();
  // Tour again through the user menu.
  await page.locator('.hub-user-menu summary').click();
  await page.locator('.hub-user-menu [data-hub-tour-start]').click();
  await expect(page.locator('.hub-tour')).toContainText('Passo 1 de');
  // Esc closes even with focus outside the tour; the Close button too.
  await page.locator('body').focus();
  await page.keyboard.press('Escape');
  await expect(page.locator('.hub-tour')).toHaveCount(0);
  await page.locator('.hub-user-menu summary').click();
  await page.locator('.hub-user-menu [data-hub-tour-start]').click();
  await page.locator('.hub-tour').getByRole('button', { name: 'Fechar' }).click();
  await expect(page.locator('.hub-tour')).toHaveCount(0);
  await api.put('/api/me/onboarding', { data: { tour: 'done' } });
  await ctx.close();
});

test('inbox: aprovação do servicedesk de ponta a ponta (outbox → Inbox → decisão pelo canal interno → chamado)', async ({ browser }) => {
  test.setTimeout(180_000);
  const title = `Aprovação E2E ${RUN}`;
  // Operator opens a request that needs approval.
  const octx = await newRoleContext(browser, 'operador');
  const op = await octx.newPage();
  await openApp(op, '/apps/servicedesk/requests?new=1');
  const form = op.locator('#ticket-new form');
  await form.locator('[name="title"]').fill(title);
  await form.locator('[name="category"]').selectOption('Acessos');
  await form.locator('[name="approval"]').check();
  await form.locator('button[type="submit"]').click();
  await expect(op.locator('#ticket-new')).toContainText('aguardando aprovação na Inbox');
  const proto = ((await op.locator('#ticket-new').innerText()).match(/SD-\d{4}-\d+/) || [])[0];
  expect(proto, 'protocolo do chamado').toBeTruthy();

  // The administrator receives the request in the Inbox (live counter) and approves it.
  const admin = await apiAs('admin');
  await expect
    .poll(async () => (await inboxItems(admin, 'approvals')).find((i) => i.title.includes(proto!))?.approval?.status, { timeout: 60_000 })
    .toBe('pending');
  const actx = await newRoleContext(browser, 'admin');
  const ap = await actx.newPage();
  await ap.goto('/inbox?filter=approvals');
  const item = ap.locator('.hub-inbox-item').filter({ hasText: proto! });
  await expect(item).toBeVisible();
  await expect(item.locator('a').first()).toHaveAttribute('href', new RegExp(`^/apps/servicedesk/requests\\?protocolo=${proto}`));
  await item.locator('[data-decide-comment]').fill('Liberado pelo E2E');
  await item.locator('button[data-decide="approve"]').click();
  await expect(ap.locator('.hub-inbox-item').filter({ hasText: proto! }).locator('.hub-approval')).toHaveAttribute('data-status', 'approved');
  // The requester cannot decide their own request (API).
  const opApi = await apiAs('operador');
  const mine = (await inboxItems(opApi)).find((i) => i.title.includes(proto!));
  expect(mine?.approval?.can_decide ?? false).toBe(false);

  // The request changed in the app (the decision came back through /_hub/approvals) and the operator was notified.
  await openApp(op, `/apps/servicedesk/requests?protocolo=${proto}`);
  await expect(op.locator('#app-root')).toContainText('Em atendimento');
  await expect
    .poll(async () => (await inboxItems(opApi, 'notifications')).some((i) => i.title.includes(`${proto} aprovado`)), { timeout: 60_000 })
    .toBe(true);
  // Live: the operator's bell shows unread items without reloading.
  await op.goto('/inbox');
  await expect(op.locator('.hub-inbox-item').filter({ hasText: `${proto} aprovado` })).toBeVisible();
  const n = Number(await op.locator('.hub-notif-menu').getAttribute('data-hub-notifications'));
  expect(n).toBeGreaterThan(0);
  // Mark as read and archive.
  const note = op.locator('.hub-inbox-item').filter({ hasText: `${proto} aprovado` });
  if (await note.locator('[data-read]').count()) await note.locator('[data-read]').click();
  await note.locator('[data-archive="1"]').click();
  await expect(op.locator('.hub-inbox-item').filter({ hasText: `${proto} aprovado` })).toHaveCount(0);
  await op.goto('/inbox?filter=archived');
  await expect(op.locator('.hub-inbox-item').filter({ hasText: `${proto} aprovado` })).toBeVisible();
  await octx.close();
  await actx.close();
});

test('inbox: pedido de acesso a app fora da lista → administrador aprova → acesso liberado; preferências de canal', async ({ browser }) => {
  const admin = await apiAs('admin');
  // Collaborator (reader) without the servicedesk app.
  expect((await admin.put('/api/admin/roles/User/apps', { data: { apps: ['inventory', 'suppliers', 'collector'] } })).status()).toBe(200);
  const lctx = await newRoleContext(browser, 'leitor');
  const lp = await lctx.newPage();
  try {
    expect((await lp.goto('/apps/servicedesk'))?.status()).toBe(403);
    await lp.goto('/applications');
    const card = lp.locator('.hub-locked[data-app="servicedesk"]');
    await expect(card).toBeVisible();
    await card.locator('[data-request-access]').click();
    await expect(card).toContainText('Acesso solicitado');
    const req = await expect
      .poll(async () => (await inboxItems(admin, 'approvals')).find((i) => i.kind === 'access' && i.app === 'servicedesk' && i.approval?.status === 'pending')?.id)
      .toBeTruthy();
    void req;
    const id = (await inboxItems(admin, 'approvals')).find((i) => i.kind === 'access' && i.app === 'servicedesk' && i.approval?.status === 'pending')!.id;
    const r = await admin.post(`/api/me/inbox/${encodeURIComponent(id)}/decide`, { data: { decision: 'approve', comment: 'ok' } });
    expect(r.status(), JSON.stringify(await json(r))).toBe(200);
    expect((await lp.goto('/apps/servicedesk'))?.status()).toBe(200);
    const leitor = await apiAs('leitor');
    await expect.poll(async () => (await inboxItems(leitor)).some((i) => i.title.includes('Acesso a Central de Serviços liberado'))).toBe(true);
    // Revoke (administrator's Inbox).
    expect((await admin.post('/api/admin/grants/revoke', { data: { item: id } })).status()).toBe(200);
    expect((await lp.goto('/apps/servicedesk'))?.status()).toBe(403);
    // Channel preference: notifications outside the bell counter.
    await lp.goto('/account#prefs');
    await lp.locator('input[name="notification.in_app"]').uncheck();
    await lp.locator('[data-channel-form] button[type="submit"]').click();
    await expect(lp.locator('#toast')).toContainText('Salvo');
    const p = await json(await leitor.get('/api/me/prefs'));
    expect(p.channels.notification.in_app).toBe(false);
    expect(p.steps).toContain('notifications');
    await prefs(leitor, { channels: { notification: { in_app: true } } });
  } finally {
    await admin.put('/api/admin/roles/User/apps', { data: { apps: null } });
    await lctx.close();
  }
});

test('sem busca; tutorial "notificações" leva às preferências e conclui', async ({ page }) => {
  const api = await apiAs('admin');
  // The hub has no search any more: Ctrl+K opens nothing and /search does not exist.
  await page.goto('/');
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('dialog', { name: 'Busca global' })).toHaveCount(0);
  expect((await page.request.get('/search?q=x')).status()).toBe(404);
  await expect(page.locator('li[data-step="search"]')).toHaveCount(0);
  // The "notifications" step leads to My account › Preferences, where saving completes it.
  const step = page.locator('li[data-step="notifications"] a');
  await expect(step).toHaveAttribute('href', '/account#prefs');
  await step.click();
  await expect(page).toHaveURL(/\/account#prefs$/);
  const form = page.locator('[data-channel-form]');
  await expect(form).toBeVisible();
  await form.locator('button[type="submit"]').click();
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).steps).toContain('notifications');
  await page.goto('/');
  await expect(page.locator('li[data-step="notifications"]')).toHaveClass(/done/);
  await api.dispose();
});

test('início: personalizar (remover, reordenar pelo teclado, adicionar), favoritos e persistência', async ({ page }) => {
  const api = await apiAs('admin');
  await api.delete('/api/me/home');
  await page.goto('/applications');
  const star = page.locator('button[data-fav="servicedesk"]');
  if ((await star.getAttribute('aria-pressed')) !== 'true') await star.click();
  await expect(star).toHaveAttribute('aria-pressed', 'true');
  await page.goto('/');
  await expect(page.locator('[data-widget="favorites"] a[href="/apps/servicedesk"]')).toBeVisible();
  await page.locator('[data-home-customize]').click();
  await expect(page.locator('[data-home-editor]')).toBeVisible();
  // Remove "Recentes".
  await page.locator('[data-widget="recents"] [data-widget-remove]').click();
  await expect(page.locator('[data-widget="recents"]')).toBeHidden();
  // Moves "Status" up with the keyboard.
  const order = async () => page.locator('.hub-widget:not([hidden])').evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.widget));
  const before = await order();
  const up = page.locator('[data-widget="status"] [data-widget-up]');
  await up.focus();
  await page.keyboard.press('Enter');
  const after = await order();
  expect(after.indexOf('status')).toBe(before.indexOf('status') - 1);
  await expect(page.locator('#hub-live')).toContainText('posição');
  await page.locator('[data-home-done]').click();
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).layout).toEqual(after);
  await page.reload();
  expect(await order()).toEqual(after);
  await expect(page.locator('[data-widget="recents"]')).toBeHidden();
  // Add back and restore the default.
  await page.locator('[data-home-customize]').click();
  await page.locator('[data-home-add]').selectOption('recents');
  await page.locator('[data-home-add-btn]').click();
  await expect(page.locator('[data-widget="recents"]')).toBeVisible();
  await page.locator('[data-home-reset]').click();
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).layout_source).toBe('default');
});

test('uso: card em Configurações (MAU, uso por app), CSV e links antigos; leitor sem acesso', async ({ page, browser }) => {
  const admin = await apiAs('admin');
  await page.goto('/admin');
  const card = page.locator('[data-usage-card]');
  await expect(card).toContainText('Usuários ativos (MAU)');
  await expect(card).not.toContainText('Buscas sem resultado');
  await expect(card).toContainText('Uso por aplicação');
  await page.goto('/admin/analytics'); // link antigo
  await expect(page).toHaveURL(/\/admin#uso$/);
  const csv = await page.request.get('/api/admin/analytics.csv');
  expect(csv.status()).toBe(200);
  expect(csv.headers()['content-disposition']).toContain('attachment');
  const body = await csv.text();
  expect(body).toContain('dia;usuarios_ativos');
  expect(body).not.toMatch(/@demo\.local/);
  const ctx = await newRoleContext(browser, 'leitor');
  const lp = await ctx.newPage();
  expect((await lp.goto('/admin'))?.status()).toBe(403);
  await ctx.close();
});

test('tema escuro e idioma: menu do usuário persiste no servidor; en/es no shell e no inventory', async ({ browser }) => {
  const api = await apiAs('operador');
  const ctx = await newRoleContext(browser, 'operador');
  const page = await ctx.newPage();
  try {
    // Default = light (even with the OS in dark, with no explicit choice); "auto" follows the OS.
    await prefs(api, { theme: '' });
    const osDark = await browser.newContext({ storageState: stateFile('operador'), colorScheme: 'dark' });
    const pd = await osDark.newPage();
    await pd.goto('/applications');
    await expect(pd.locator('html')).toHaveAttribute('data-theme', 'light');
    const lightBg = await pd.evaluate(() => getComputedStyle(document.body).backgroundColor);
    await prefs(api, { theme: 'auto' });
    await pd.reload();
    await expect(pd.locator('html')).toHaveAttribute('data-theme', 'auto');
    expect(await pd.evaluate(() => getComputedStyle(document.body).backgroundColor)).not.toBe(lightBg);
    await osDark.close();
    await page.goto('/account');
    // Settings › Appearance: 3 options (radiogroup), applies right away, without reloading, and saves.
    const seg = page.locator('.acct-theme-seg[role="radiogroup"]');
    await expect(seg.locator('[role="radio"]')).toHaveCount(3);
    await seg.locator('button[name="theme"][value="dark"]').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(seg.locator('[value="dark"]')).toHaveAttribute('aria-checked', 'true');
    await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).theme).toBe('dark');
    // Setas: dark → auto → (volta) dark.
    await seg.locator('[value="dark"]').press('ArrowRight');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'auto');
    await expect(seg.locator('[value="auto"]')).toBeFocused();
    await seg.locator('[value="auto"]').press('ArrowLeft');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).theme).toBe('dark');
    await page.reload();
    const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(bg).not.toBe('rgb(255, 255, 255)');
    await page.goto('/account');
    await page.locator('#acct-lang-select').selectOption('en');
    // o seletor envia sozinho (data-hub-autosubmit)
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await page.goto('/applications');
    await expect(page.locator('.platform-rail')).toContainText('Home');
    await expect(page.locator('h1.page-title')).toHaveText('Applications');
    // Translated Python fragment (x-hub-locale of the hub).
    await openApp(page, '/apps/inventory/dashboard');
    await expect(page.locator('#app-root .page-title')).toHaveText('Overview');
    await expect(page.locator('#app-root')).toContainText('Back to platform');
    await openApp(page, '/apps/inventory/settings');
    await expect(page.locator('#app-root .page-title')).toHaveText('Settings');
    // Automatic: follows the browser's Accept-Language (`es` was dropped in 10/2026: becomes en).
    await prefs(api, { lang: '' });
    const ctx2 = await browser.newContext({ storageState: stateFile('operador'), locale: 'es-ES' });
    const p2 = await ctx2.newPage();
    await p2.goto('/inbox');
    await expect(p2.locator('html')).toHaveAttribute('lang', 'en');
    await ctx2.close();
  } finally {
    await prefs(api, { theme: '', lang: '' });
    await ctx.close();
  }
});

// ---- Accessibility: axe (WCAG 2.2 AA) on the main pages, 2 themes × 2 languages ----------
const PAGES = ['/', '/applications', '/inbox', '/status', '/admin', '/admin/health', '/admin/connections', '/apps/inventory/assets', '/apps/servicedesk/requests', '/apps/suppliers/indicators'];

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  return r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
}

for (const theme of ['light', 'dark'] as const) {
  for (const lang of ['pt-BR', 'en'] as const) {
    test(`axe: 0 violações sérias/críticas - tema ${theme}, idioma ${lang}`, async ({ browser }) => {
      test.setTimeout(300_000);
      const api = await apiAs('admin');
      await prefs(api, { theme, lang });
      const ctx = await newRoleContext(browser, 'admin');
      const page = await ctx.newPage();
      const found: string[] = [];
      try {
        for (const p of PAGES) {
          if (/^\/(inventory|servicedesk|suppliers)\//.test(p)) await openApp(page, p);
          else await page.goto(p);
          await expect(page.locator('html')).toHaveAttribute('lang', lang);
          for (const v of await axe(page)) found.push(`${p} ${v.id} (${v.nodes.length}): ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
        }
        // Login (no session) in the browser's theme/language.
        const anon = await browser.newContext({ colorScheme: theme, locale: lang });
        const lp = await anon.newPage();
        await lp.goto('/login');
        for (const v of await axe(lp)) found.push(`/login ${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
        await anon.close();
      } finally {
        await prefs(api, { theme: '', lang: '' });
        await ctx.close();
      }
      expect(found, found.join('\n')).toEqual([]);
    });
  }
}

test('acessibilidade: pular para o conteúdo, foco na troca de seção e Esc nos menus', async ({ page }) => {
  await page.goto('/applications');
  await page.keyboard.press('Tab');
  const skip = page.locator('.skip-link');
  await expect(skip).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#app-root')).toBeFocused();
  // User menu: Esc closes and returns focus to the button.
  await page.locator('.hub-user-menu summary').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.hub-user-menu')).toHaveAttribute('open', '');
  await page.keyboard.press('Escape');
  await expect(page.locator('.hub-user-menu')).not.toHaveAttribute('open', '');
  await expect(page.locator('.hub-user-menu summary')).toBeFocused();
  // Section change in an app: focus goes to the title of the new page.
  await openApp(page, '/apps/inventory/dashboard');
  await page.locator('.sidebar-nav a.nav-item[href="/apps/inventory/assets"]').click();
  await expect(page).toHaveURL(/\/apps\/inventory\/assets$/);
  await expect.poll(() => page.evaluate(() => /^H[12]$/.test(document.activeElement?.tagName || '') || document.activeElement?.id === 'app-root')).toBe(true);
});
