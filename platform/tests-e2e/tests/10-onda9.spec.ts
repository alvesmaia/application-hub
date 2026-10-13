import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { apiAs, json, newRoleContext, openApp, stateFile, uniq } from './helpers';

// Onda 9 (Fase 3): onboarding guiado, Inbox (notificações e aprovações vindas dos apps),
// busca global federada (⌘K), Início personalizável, analytics de uso, tema escuro,
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
  // Teclado: → avança; o foco fica no diálogo.
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.hub-tour')).toContainText('Passo 2 de');
  await expect(page.locator('.hub-tour-target')).toHaveCount(1);
  // Esc interrompe e guarda o passo; recarregar retoma do mesmo ponto.
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
  // Primeiros passos: abrir um app marca o passo sozinho (servidor).
  await expect(page.locator('[data-widget="onboarding"]')).toBeVisible();
  await openApp(page, '/apps/fornecedores/indicators');
  await page.goto('/');
  await expect(page.locator('li[data-step="open_app"]')).toHaveClass(/done/);
  // O operador (Tech Lead) vê os passos de administrador que pode fazer.
  await expect(page.locator('li[data-step="variable"]')).toBeVisible();
  await expect(page.locator('li[data-step="package"]')).toBeVisible();
  // Tour de novo pelo menu do usuário.
  await page.locator('.hub-user-menu summary').click();
  await page.locator('.hub-user-menu [data-hub-tour-start]').click();
  await expect(page.locator('.hub-tour')).toContainText('Passo 1 de');
  // Esc fecha mesmo com o foco fora do tour; o botão Fechar também.
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

test('inbox: aprovação do servicos de ponta a ponta (outbox → Inbox → decisão pelo canal interno → chamado)', async ({ browser }) => {
  test.setTimeout(180_000);
  const title = `Aprovação E2E ${RUN}`;
  // Operador abre um chamado que precisa de aprovação.
  const octx = await newRoleContext(browser, 'operador');
  const op = await octx.newPage();
  await openApp(op, '/apps/servicos/requests?new=1');
  const form = op.locator('#ticket-new form');
  await form.locator('[name="title"]').fill(title);
  await form.locator('[name="category"]').selectOption('Acessos');
  await form.locator('[name="approval"]').check();
  await form.locator('button[type="submit"]').click();
  await expect(op.locator('#ticket-new')).toContainText('aguardando aprovação na Inbox');
  const proto = ((await op.locator('#ticket-new').innerText()).match(/SD-\d{4}-\d+/) || [])[0];
  expect(proto, 'protocolo do chamado').toBeTruthy();

  // A administradora recebe o pedido na Inbox (contador ao vivo) e aprova.
  const admin = await apiAs('admin');
  await expect
    .poll(async () => (await inboxItems(admin, 'approvals')).find((i) => i.title.includes(proto!))?.approval?.status, { timeout: 60_000 })
    .toBe('pending');
  const actx = await newRoleContext(browser, 'admin');
  const ap = await actx.newPage();
  await ap.goto('/inbox?filter=approvals');
  const item = ap.locator('.hub-inbox-item').filter({ hasText: proto! });
  await expect(item).toBeVisible();
  await expect(item.locator('a').first()).toHaveAttribute('href', new RegExp(`^/apps/servicos/requests\\?protocolo=${proto}`));
  await item.locator('[data-decide-comment]').fill('Liberado pelo E2E');
  await item.locator('button[data-decide="approve"]').click();
  await expect(ap.locator('.hub-inbox-item').filter({ hasText: proto! }).locator('.hub-approval')).toHaveAttribute('data-status', 'approved');
  // O quem pediu não pode decidir o próprio pedido (API).
  const opApi = await apiAs('operador');
  const mine = (await inboxItems(opApi)).find((i) => i.title.includes(proto!));
  expect(mine?.approval?.can_decide ?? false).toBe(false);

  // O chamado mudou no app (a decisão voltou pelo /_hub/approvals) e o operador foi avisado.
  await openApp(op, `/apps/servicos/requests?protocolo=${proto}`);
  await expect(op.locator('#app-root')).toContainText('Em atendimento');
  await expect
    .poll(async () => (await inboxItems(opApi, 'notifications')).some((i) => i.title.includes(`${proto} aprovado`)), { timeout: 60_000 })
    .toBe(true);
  // Ao vivo: o sino do operador mostra não lidas sem recarregar.
  await op.goto('/inbox');
  await expect(op.locator('.hub-inbox-item').filter({ hasText: `${proto} aprovado` })).toBeVisible();
  const n = Number(await op.locator('.hub-notif-menu').getAttribute('data-hub-notifications'));
  expect(n).toBeGreaterThan(0);
  // Marcar como lido e arquivar.
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
  // Colaborador (leitora) sem o app servicos.
  expect((await admin.put('/api/admin/roles/Colaborador/apps', { data: { apps: ['ativos', 'fornecedores', 'coletor'] } })).status()).toBe(200);
  const lctx = await newRoleContext(browser, 'leitor');
  const lp = await lctx.newPage();
  try {
    expect((await lp.goto('/apps/servicos'))?.status()).toBe(403);
    await lp.goto('/applications');
    const card = lp.locator('.hub-locked[data-app="servicos"]');
    await expect(card).toBeVisible();
    await card.locator('[data-request-access]').click();
    await expect(card).toContainText('Acesso solicitado');
    const req = await expect
      .poll(async () => (await inboxItems(admin, 'approvals')).find((i) => i.kind === 'access' && i.app === 'servicos' && i.approval?.status === 'pending')?.id)
      .toBeTruthy();
    void req;
    const id = (await inboxItems(admin, 'approvals')).find((i) => i.kind === 'access' && i.app === 'servicos' && i.approval?.status === 'pending')!.id;
    const r = await admin.post(`/api/me/inbox/${encodeURIComponent(id)}/decide`, { data: { decision: 'approve', comment: 'ok' } });
    expect(r.status(), JSON.stringify(await json(r))).toBe(200);
    expect((await lp.goto('/apps/servicos'))?.status()).toBe(200);
    const leitor = await apiAs('leitor');
    await expect.poll(async () => (await inboxItems(leitor)).some((i) => i.title.includes('Acesso a Central de Serviços liberado'))).toBe(true);
    // Revogar (Inbox do administrador).
    expect((await admin.post('/api/admin/grants/revoke', { data: { item: id } })).status()).toBe(200);
    expect((await lp.goto('/apps/servicos'))?.status()).toBe(403);
    // Preferência de canal: notificações fora do contador do sino.
    await lp.goto('/account#prefs');
    await lp.locator('input[name="notification.in_app"]').uncheck();
    await lp.locator('[data-channel-form] button[type="submit"]').click();
    await expect(lp.locator('#toast')).toContainText('Salvo');
    const p = await json(await leitor.get('/api/me/prefs'));
    expect(p.channels.notification.in_app).toBe(false);
    expect(p.steps).toContain('notifications');
    await prefs(leitor, { channels: { notification: { in_app: true } } });
  } finally {
    await admin.put('/api/admin/roles/Colaborador/apps', { data: { apps: null } });
    await lctx.close();
  }
});

test('busca ⌘K: plataforma + apps federados, teclado, listbox e buscas recentes', async ({ page }) => {
  await page.goto('/applications');
  await page.keyboard.press('Control+k');
  const dialog = page.getByRole('dialog', { name: 'Busca global' });
  await expect(dialog).toBeVisible();
  const input = dialog.getByRole('combobox');
  await expect(input).toBeFocused();
  await input.fill('Norte');
  const listbox = dialog.getByRole('listbox');
  await expect(listbox.getByRole('option', { name: /Norte Tecnologia/ }).first()).toBeVisible({ timeout: 15_000 });
  await expect(listbox.getByRole('group').first()).toBeVisible();
  // Grupo do app fornecedores (resultado federado pelo /_hub/search).
  await expect(dialog.locator('.hub-palette-group', { hasText: 'Rede de Fornecedores' })).toBeVisible();
  // Setas movem a opção ativa (aria-activedescendant).
  await page.keyboard.press('ArrowDown');
  const active = await input.getAttribute('aria-activedescendant');
  expect(active).toMatch(/^hub-po-/);
  // Enter na opção do fornecedor abre o deep link.
  const opt = listbox.getByRole('option', { name: /Norte Tecnologia Fornecedor/ });
  const id = await opt.getAttribute('id');
  for (let i = 0; i < 12 && (await input.getAttribute('aria-activedescendant')) !== id; i++) await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/apps\/fornecedores\/indicators\?id=FOR-0284/);
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 30_000 });
  // Dentro do app a paleta usa o índice local (CSP) e oferece a busca completa.
  await page.keyboard.press('Control+k');
  await page.getByRole('combobox').fill('Ativos');
  await expect(page.getByRole('option', { name: /Gestão de Ativos/ })).toBeVisible();
  await expect(page.getByRole('option', { name: /Ver todos os resultados/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Busca global' })).toHaveCount(0);
  // Buscas recentes (paleta vazia).
  await page.goto('/');
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('option', { name: 'Norte' })).toBeVisible();
  await page.keyboard.press('Escape');
  // Página de resultados (sem JS) e busca sem resultado no analytics.
  await page.goto(`/search?q=zzz-nada-${RUN}`);
  await expect(page.locator('.empty-state')).toContainText('Nenhum resultado');
});

test('início: personalizar (remover, reordenar pelo teclado, adicionar), favoritos e persistência', async ({ page }) => {
  const api = await apiAs('admin');
  await api.delete('/api/me/home');
  await page.goto('/applications');
  const star = page.locator('button[data-fav="servicos"]');
  if ((await star.getAttribute('aria-pressed')) !== 'true') await star.click();
  await expect(star).toHaveAttribute('aria-pressed', 'true');
  await page.goto('/');
  await expect(page.locator('[data-widget="favorites"] a[href="/apps/servicos"]')).toBeVisible();
  await page.locator('[data-home-customize]').click();
  await expect(page.locator('[data-home-editor]')).toBeVisible();
  // Remove "Atalhos".
  await page.locator('[data-widget="shortcuts"] [data-widget-remove]').click();
  await expect(page.locator('[data-widget="shortcuts"]')).toBeHidden();
  // Move "Inbox" para cima pelo teclado.
  const order = async () => page.locator('.hub-widget:not([hidden])').evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.widget));
  const before = await order();
  const up = page.locator('[data-widget="inbox"] [data-widget-up]');
  await up.focus();
  await page.keyboard.press('Enter');
  const after = await order();
  expect(after.indexOf('inbox')).toBe(before.indexOf('inbox') - 1);
  await expect(page.locator('#hub-live')).toContainText('posição');
  await page.locator('[data-home-done]').click();
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).layout).toEqual(after);
  await page.reload();
  expect(await order()).toEqual(after);
  await expect(page.locator('[data-widget="shortcuts"]')).toBeHidden();
  // Adicionar de volta e restaurar o padrão.
  await page.locator('[data-home-customize]').click();
  await page.locator('[data-home-add]').selectOption('shortcuts');
  await page.locator('[data-home-add-btn]').click();
  await expect(page.locator('[data-widget="shortcuts"]')).toBeVisible();
  await page.locator('[data-home-reset]').click();
  await expect.poll(async () => (await json(await api.get('/api/me/prefs'))).layout_source).toBe('default');
});

test('uso: card em Configurações (MAU, uso por app, buscas sem resultado), CSV e links antigos; leitor sem acesso', async ({ page, browser }) => {
  const admin = await apiAs('admin');
  expect((await admin.put('/api/admin/analytics/terms', { data: { raw: false } })).status()).toBe(200);
  expect((await admin.post('/api/me/search', { data: { q: `zzz-nada-${RUN}` } })).status()).toBe(200);
  await page.goto('/admin');
  const card = page.locator('[data-usage-card]');
  await expect(card).toContainText('Usuários ativos (MAU)');
  await expect(card).toContainText('Buscas sem resultado');
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

test('tema escuro e idioma: menu do usuário persiste no servidor; en/es no shell e no ativos', async ({ browser }) => {
  const api = await apiAs('operador');
  const ctx = await newRoleContext(browser, 'operador');
  const page = await ctx.newPage();
  try {
    // Padrão = claro (mesmo com o SO escuro, sem escolha explícita); "auto" segue o SO.
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
    // Configurações › Aparência: 3 opções (radiogroup), aplica na hora, sem recarregar, e grava.
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
    // Fragmento Python traduzido (x-hub-locale do hub).
    await openApp(page, '/apps/ativos/dashboard');
    await expect(page.locator('#app-root .page-title')).toHaveText('Overview');
    await expect(page.locator('#app-root')).toContainText('Back to platform');
    await openApp(page, '/apps/ativos/settings');
    await expect(page.locator('#app-root .page-title')).toHaveText('Settings');
    // Automático: segue o Accept-Language do navegador (`es` saiu em 10/2026: vira en).
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

// ---- Acessibilidade: axe (WCAG 2.2 AA) nas páginas principais, 2 temas × 2 idiomas ----------
const PAGES = ['/', '/applications', '/inbox', '/search?q=norte', '/status', '/admin', '/admin/health', '/admin/connections', '/apps/ativos/assets', '/apps/servicos/requests', '/apps/fornecedores/indicators'];

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  return r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
}

for (const theme of ['light', 'dark'] as const) {
  for (const lang of ['pt-BR', 'en'] as const) {
    test(`axe: 0 violações sérias/críticas — tema ${theme}, idioma ${lang}`, async ({ browser }) => {
      test.setTimeout(300_000);
      const api = await apiAs('admin');
      await prefs(api, { theme, lang });
      const ctx = await newRoleContext(browser, 'admin');
      const page = await ctx.newPage();
      const found: string[] = [];
      try {
        for (const p of PAGES) {
          if (/^\/(ativos|servicos|fornecedores)\//.test(p)) await openApp(page, p);
          else await page.goto(p);
          await expect(page.locator('html')).toHaveAttribute('lang', lang);
          for (const v of await axe(page)) found.push(`${p} ${v.id} (${v.nodes.length}): ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
        }
        // Login (sem sessão) no mesmo tema/idioma do navegador.
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
  // Menu do usuário: Esc fecha e devolve o foco ao botão.
  await page.locator('.hub-user-menu summary').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.hub-user-menu')).toHaveAttribute('open', '');
  await page.keyboard.press('Escape');
  await expect(page.locator('.hub-user-menu')).not.toHaveAttribute('open', '');
  await expect(page.locator('.hub-user-menu summary')).toBeFocused();
  // Troca de seção num app: o foco vai para o título da nova página.
  await openApp(page, '/apps/ativos/dashboard');
  await page.locator('.sidebar-nav a.nav-item[href="/apps/ativos/assets"]').click();
  await expect(page).toHaveURL(/\/apps\/ativos\/assets$/);
  await expect.poll(() => page.evaluate(() => /^H[12]$/.test(document.activeElement?.tagName || '') || document.activeElement?.id === 'app-root')).toBe(true);
});
