import { test, expect, request, type Page, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { HUB, PLATFORM, USERS, WORK_DIR, apiAs, apiLogin, appImage, autoConfirm, hubBin, json, openDeployVersion, stateFile, uniq } from './helpers';

// Onda 12 (2.1) — anti-phishing com os apps na MESMA origem do hub:
//  (a) um fragmento hostil (rota SÓ DE TESTE do compose de demonstração,
//      HUB_DEMO_E2E_HOSTILE=1) com <script>, onerror=, links javascript:, formulários para
//      /login, /account e /oauth → nada executa, nada é enviado;
//  (b) script de app aprovado roda (hash certo, SRI); o adulterado é bloqueado;
//  (c) o pipeline mostra "REVISE: App traz JavaScript próprio" e a implantação só é
//      aprovada depois da revisão de segurança (separada, auditada);
//  (d) a frase de segurança aparece no consentimento OAuth e na aprovação de ação do
//      agente (step-up) — e nunca numa requisição que não seja navegação de topo;
//  + os comportamentos declarativos data-hub-* que substituíram o JS dos apps.
// Compose no ar (HUB_DEMO_USERS=1).

test.use({ storageState: stateFile('admin') });
test.describe.configure({ mode: 'serial' });
const RUN = uniq();

async function openFragment(page: Page, url: string, ready: string) {
  await page.goto(url);
  await expect(page.locator(ready).first()).toBeVisible({ timeout: 30_000 });
}

test('(a) fragmento hostil: nada executa, formulários para /login, /account e /oauth não saem @core', async ({ page }) => {
  const sent: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (r.method() === 'POST' && /^\/(login|account|oauth)/.test(u.pathname)) sent.push(`${r.method()} ${u.pathname}`);
    if (u.hostname === 'evil.example') sent.push(`${r.method()} ${r.url()}`);
  });
  const dialogs: string[] = [];
  page.on('dialog', (d) => { dialogs.push(d.message()); d.dismiss().catch(() => {}); });
  const res = await page.goto('/apps/ativos/e2e-hostil');
  const csp = res!.headers()['content-security-policy'] || '';
  expect(csp).toContain("require-trusted-types-for 'script'");
  const scriptSrc = csp.split(';').find((d) => d.trim().startsWith('script-src'))!;
  expect(scriptSrc).not.toContain('_frag');
  expect(scriptSrc).not.toContain('unsafe');
  const formAction = csp.split(';').find((d) => d.trim().startsWith('form-action'))!;
  expect(formAction).not.toMatch(/\/login|\/oauth|agent-approval|password/);
  await expect(page.locator('[data-e2e-ok]')).toBeVisible({ timeout: 30_000 });
  const state = await page.evaluate(() => {
    const root = document.getElementById('app-root')!;
    return {
      pwned: (window as any).__pwned,
      dangerous: root.querySelectorAll('script, iframe, object, embed, base, meta, link, style, animate, set').length,
      handlers: [...root.querySelectorAll('*')].filter((el) => [...el.attributes].some((a) => /^on/i.test(a.name))).length,
      jsUrls: [...root.querySelectorAll('[href], [src], [action], [formaction], [data]')].filter((el) => [...el.attributes].some((a) => /^\s*javascript:/i.test(a.value))).length,
      appRoots: document.querySelectorAll('#app-root').length,
      headerVisible: getComputedStyle(document.querySelector('.platform-header')!).display !== 'none',
      blocked: [...root.querySelectorAll('[data-hub-blocked]')].map((e) => e.id).sort(),
      baseHref: document.baseURI,
    };
  });
  expect(state.pwned).toBeUndefined();
  expect(state.dangerous).toBe(0);
  expect(state.handlers).toBe(0);
  expect(state.jsUrls).toBe(0);
  expect(state.appRoots).toBe(1);
  expect(state.headerVisible).toBe(true);
  expect(state.baseHref.startsWith(HUB)).toBe(true);
  expect(state.blocked).toEqual(expect.arrayContaining(['e2e-steal', 'e2e-steal-account', 'e2e-external', 'e2e-oauth-btn']));
  for (const sel of ['#e2e-js-link', '#e2e-js-link2', '#e2e-click']) await page.locator(sel).click();
  for (const sel of ['#e2e-steal-btn', '#e2e-steal-account-btn', '#e2e-oauth-btn', '#e2e-external-btn']) await page.locator(sel).click();
  await page.waitForTimeout(800);
  expect(await page.evaluate(() => (window as any).__pwned)).toBeUndefined();
  expect(sent).toEqual([]);
  expect(dialogs).toEqual([]);
  expect(new URL(page.url()).pathname).toBe('/apps/ativos/e2e-hostil');

  // Aberto direto no navegador (link malicioso para o fragmento): documento em sandbox.
  const direct = await page.goto('/_frag/ativos/e2e-hostil');
  expect(direct!.headers()['content-security-policy']).toMatch(/; sandbox$/);
  expect(await page.evaluate(() => (window as any).__pwned)).toBeUndefined();
  await page.locator('#e2e-steal-btn').click();
  await page.waitForTimeout(500);
  // (No documento em sandbox a CSP ainda barra estilos externos; só envios importam aqui.)
  expect(sent.filter((x) => x.startsWith('POST'))).toEqual([]);
});

test('comportamentos data-hub-*: modal, toast, filtro, gráfico e menu sem JavaScript do app', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await openFragment(page, '/apps/ativos/movements', '[data-hub-filter]');
  // Nenhum script do app carregado.
  const scripts = await page.evaluate(() => [...document.scripts].map((s) => s.src).filter(Boolean));
  expect(scripts.filter((s) => s.includes('/_frag/'))).toEqual([]);
  const rows = page.locator('#app-root .data-table tbody tr');
  const total = await rows.count();
  expect(total).toBeGreaterThan(1);
  const first = ((await rows.first().locator('td').first().textContent()) || '').trim();
  await page.locator('[data-hub-filter]').fill(first.slice(0, 12));
  await expect.poll(async () => (await rows.evaluateAll((rs) => rs.filter((r) => !(r as HTMLElement).hidden).length))).toBeLessThan(total);
  await page.locator('[data-hub-filter]').fill('');
  // Modal declarativo (template do fragmento) + formulário que fecha e mostra o toast.
  await page.locator('[data-hub-modal="#modal-asset"]').first().click();
  const modal = page.locator('.overlay .modal');
  await expect(modal.getByRole('heading', { name: 'Cadastrar ativo' })).toBeVisible();
  await modal.locator('input').first().fill(`Notebook ${RUN}`);
  await modal.locator('button[type="submit"]').click();
  await expect(modal).toHaveCount(0);
  await expect(page.locator('#toast')).toContainText('Ativo cadastrado com sucesso.');
  // Gráfico do hub (TimeSeriesChart) montado a partir do data-hub-charts.
  await openFragment(page, '/apps/ativos/dashboard', '[data-tsc]');
  const cat = page.locator('.tsc-cat-row').first();
  await cat.click();
  await expect(cat).toHaveAttribute('aria-pressed', 'true');
  // Busca na base de conhecimento (filtro com vazio) — servicos.
  await openFragment(page, '/apps/servicos/knowledge', '[data-hub-filter-item]');
  await page.locator('[data-hub-filter="#kb-page"]').fill('zzz-nada-aqui');
  await expect(page.locator('.app3-kb-empty')).toBeVisible();
  await page.locator('[data-hub-filter="#kb-page"]').fill('reembolso');
  await expect(page.locator('[data-hub-filter-item]:visible')).toHaveCount(1);
  // Toast declarativo.
  await page.locator('[data-hub-filter-item]:visible').first().click();
  await expect(page.locator('#toast')).toContainText('abertos');
  expect(errors, errors.join('\n')).toEqual([]);
});

// ---- (b)/(c): pacote de teste com JavaScript próprio ----------------------------------
const APPROVED_JS = "document.documentElement.dataset.e2eApproved = 'ran';\n";
// A versão revisada no pacote; o app serve outra (adulterada) → SRI bloqueia.
// Único por execução: sempre há um hash novo pendente (aprovação vale por app+arquivo+hash).
const TAMPERED_PKG_JS = `document.documentElement.dataset.e2eTampered = 'revisado-${RUN}';\n`;
const sri = (s: string) => `sha384-${createHash('sha384').update(s).digest('base64')}`;

function scriptsPackage(app: string, version: string): string {
  const src = path.join(PLATFORM, 'samples', 'src', app);
  const dir = path.join(WORK_DIR, `pkg-js-${app}-${version}`);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.cpSync(src, dir, { recursive: true });
  fs.mkdirSync(path.join(dir, 'ui', 'assets'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'ui', 'assets', 'e2e-approved.js'), APPROVED_JS);
  fs.writeFileSync(path.join(dir, 'ui', 'assets', 'e2e-tampered.js'), TAMPERED_PKG_JS);
  const mf = path.join(dir, 'manifest.json');
  const m = JSON.parse(fs.readFileSync(mf, 'utf8'));
  m.version = version;
  m.scripts = [
    { path: 'ui/assets/e2e-approved.js', reason: 'Teste E2E: script aprovado com o hash certo' },
    { path: 'ui/assets/e2e-tampered.js', reason: 'Teste E2E: o app serve outra versão (SRI)' },
  ];
  fs.writeFileSync(mf, JSON.stringify(m, null, 2));
  const image = appImage(app);
  const out = path.join(WORK_DIR, `js-${app}-${version}.hubapp`);
  execFileSync(hubBin(), ['pack', '--dir', dir, '--image', image, '--out', out], { stdio: 'inherit' });
  return out;
}

async function deployments(api: APIRequestContext) {
  const r = await api.get('/api/ops/deployments');
  expect(r.status(), await r.text()).toBe(200);
  const list = await r.json();
  return (Array.isArray(list) ? list : list.deployments || list.items) as any[];
}

test('(c)+(b) JavaScript próprio: REVISE no pipeline, revisão separada e auditada, SRI no navegador', async ({ page }) => {
  test.setTimeout(480_000);
  autoConfirm(page);
  const APP = 'servicos';
  const VERSION = `3.9.${Math.floor(Date.now() / 1000) % 1_000_000}`;
  let api = await apiAs('admin');
  // Separação de funções (M2): quem envia é outro administrador; a Ana revisa.
  const email = `publicador-${RUN.toLowerCase()}@demo.local`;
  const nu = await api.post('/api/admin/users', { headers: { 'content-type': 'application/json' }, data: { email, name: `Publicador ${RUN}`, roles: ['Administrador'], password: 'Publicador-Teste-123', must_change_password: false } });
  expect(nu.status(), await nu.text()).toBeLessThan(300);
  const pub = await apiLogin(email, 'Publicador-Teste-123');
  // O login de outra conta neste processo derruba a sessão salva do admin (o hub troca a
  // sessão que chega no login): entra de novo como admin, na API e no navegador.
  api = await apiLogin(USERS.admin.email, USERS.admin.password);
  await page.goto('/login');
  await page.locator('#email').fill(USERS.admin.email);
  await page.locator('#password').fill(USERS.admin.password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'));
  await page.context().storageState({ path: stateFile('admin') });
  const before = (await deployments(api)).find((d) => d.app === APP && d.env === 'Produção' && d.status === 'Implantado');
  const up = await pub.post('/api/ops/packages?env=producao&wait=true', {
    headers: { 'x-hub-request': '1' },
    multipart: { package: { name: `js-${APP}-${VERSION}.hubapp`, mimeType: 'application/zip', buffer: fs.readFileSync(scriptsPackage(APP, VERSION)) } },
  });
  expect(up.status(), await up.text()).toBe(201);
  const job = await up.json();
  const fe = (job.steps as any[]).find((s) => s.name === 'Front-end');
  expect(fe.state).toBe('warn');
  expect(fe.detail).toContain('REVISE: App traz JavaScript próprio');
  expect(fe.detail).toContain(sri(APPROVED_JS));
  const dep = (await deployments(api)).find((d) => d.app === APP && d.version === VERSION && d.env === 'Produção');
  expect(dep.status).toMatch(/Aguardando/);
  expect(dep.submitted_by).toBe(`Publicador ${RUN}`);
  // Quem enviou não aprova o próprio JavaScript (recusa auditada).
  const self = await pub.post(`/api/ops/deployments/${dep.id}/scripts/approve`, { headers: { 'content-type': 'application/json', 'x-hub-request': '1' }, data: {} });
  expect(self.status()).toBe(400);
  expect(await self.text()).toContain('Separação de funções');
  // Renomear o publicador com o nome de quem revisa não burla (comparação por id).
  const pubId = (await nu.json()).id as string;
  const ren = await api.put(`/api/admin/users/${pubId}`, { headers: { 'content-type': 'application/json' }, data: { name: USERS.admin.name } });
  expect(ren.status(), await ren.text()).toBeLessThan(300);
  const self2 = await pub.post(`/api/ops/deployments/${dep.id}/scripts/approve`, { headers: { 'content-type': 'application/json', 'x-hub-request': '1' }, data: {} });
  expect(self2.status()).toBe(400);
  expect(await self2.text()).toContain('Separação de funções');
  await pub.dispose();
  expect(dep.scripts.map((s: any) => s.sha384)).toEqual([sri(APPROVED_JS), sri(TAMPERED_PKG_JS)]);
  // Bloqueia: sem a revisão de segurança a implantação não é aprovada.
  const blocked = await api.post(`/api/ops/deployments/${dep.id}/approve?wait=true`, { headers: { 'content-type': 'application/json' }, data: {} });
  expect(blocked.status()).toBe(400);
  expect(await blocked.text()).toContain('JavaScript próprio');
  // Revisão pela tela (botão separado da aprovação).
  const drawer = await openDeployVersion(page, 'servicos', dep.id);
  const review = drawer.locator(`[data-ops-scripts-review="${dep.id}"]`);
  await expect(review).toContainText('Revisar: o app traz JavaScript próprio');
  await expect(review).toContainText('ui/assets/e2e-tampered.js');
  await expect(review).toContainText(sri(TAMPERED_PKG_JS));
  await drawer.locator(`[data-ops-approve-scripts="${dep.id}"]`).click(); // botão fica nas ações da versão (gaveta), fora do quadro de revisão
  await expect(page.locator('#toast')).toContainText('JavaScript próprio aprovado');
  await expect(page.locator(`[data-ops-scripts-review="${dep.id}"]`)).toHaveCount(0);
  await page.goto('/admin/audit?scope=servicos');
  await expect(page.locator('#app-root')).toContainText('JavaScript próprio aprovado');
  // Agora a implantação é aprovada (blue/green real).
  const ok = await api.post(`/api/ops/deployments/${dep.id}/approve?wait=true`, { headers: { 'content-type': 'application/json' }, data: {} });
  expect(ok.status(), await ok.text()).toBe(200);
  await expect.poll(async () => (await deployments(api)).find((d) => d.id === dep.id)?.status, { timeout: 240_000 }).toBe('Implantado');

  // (b) Navegador: o aprovado roda; o adulterado (servido ≠ revisado) é recusado pelo SRI.
  const consoleErrors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  const res = await page.goto('/apps/servicos/dashboard');
  const csp = res!.headers()['content-security-policy'] || '';
  expect(csp).toContain(`'${sri(APPROVED_JS)}'`);
  expect(csp).toContain('/_frag/servicos/assets/e2e-approved.js');
  await expect(page.locator('html')).toHaveAttribute('data-e2e-approved', 'ran', { timeout: 30_000 });
  await page.waitForTimeout(800);
  expect(await page.locator('html').getAttribute('data-e2e-tampered')).toBeNull();
  expect(consoleErrors.join('\n')).toMatch(/integrity|e2e-tampered/i);

  // Volta à versão anterior (sem scripts) para não afetar outros cenários.
  if (before) {
    const rb = await api.post(`/api/ops/deployments/${dep.id}/rollback?wait=true`, { headers: { 'content-type': 'application/json' }, data: {} });
    expect(rb.status(), await rb.text()).toBe(200);
  }
  await api.dispose();
});

// ---- (d) telas de credencial de topo (a frase de segurança foi removida) -----------------
test('(d) consentimento OAuth é tela de topo sem script', async ({ page }) => {
  // Recurso removido: a rota antiga não existe mais e nenhuma tela mostra a faixa.
  const gone = await page.goto('/account/security-phrase');
  expect(gone!.status()).toBe(404);
  // Consentimento OAuth.
  const anon = await request.newContext({ baseURL: HUB });
  const REDIRECT = 'http://127.0.0.1:53682/e2e-callback';
  const reg = await anon.post('/oauth/register', { data: { client_name: `Consent E2E ${RUN}`, redirect_uris: [REDIRECT], token_endpoint_auth_method: 'none' } });
  expect(reg.status()).toBe(201);
  const cid = (await reg.json()).client_id as string;
  const challenge = createHash('sha256').update(randomBytes(48).toString('base64url')).digest('base64url');
  const q = new URLSearchParams({ response_type: 'code', client_id: cid, redirect_uri: REDIRECT, code_challenge: challenge, code_challenge_method: 'S256', state: 'x', scope: 'mcp', resource: `${HUB}/mcp` });
  const consent = await page.goto(`/oauth/authorize?${q}`);
  expect(consent!.headers()['content-security-policy']).toContain("frame-ancestors 'none'");
  expect(consent!.headers()['cross-origin-opener-policy']).toBe('noopener-allow-popups');
  await expect(page.locator('[data-hub-phrase], [data-hub-phrase-missing]')).toHaveCount(0);
  await anon.dispose();
});

// ---- revisão de segurança da 2.1 --------------------------------------------------------
test('H1: data-hub-* com seletor malicioso não alcança o shell (só ids dentro do app) @core', async ({ page }) => {
  await openFragment(page, '/apps/ativos/movements', '[data-hub-filter]');
  const r = await page.evaluate(async () => {
    const root = document.getElementById('app-root')!;
    const header = document.querySelector('.platform-header')!;
    const before = header.outerHTML;
    const box = document.createElement('div');
    // Mesmo caminho do HTML do app: sanitizador + hub-kit.
    (window as any).HubSafe.setHTML(box, `
      <button id="h1a" data-hub-get="/apps/ativos/movements" data-hub-target=", .platform-header" data-hub-swap="outerHTML">a</button>
      <button id="h1b" data-hub-get="/apps/ativos/movements" data-hub-target=", body" data-hub-swap="innerHTML">b</button>
      <button id="h1c" data-hub-get="/apps/ativos/movements" data-hub-target="#x, header" data-hub-swap="outerHTML">c</button>
      <button id="h1d" data-hub-get="/apps/ativos/movements" data-hub-target="#app-root" data-hub-swap="innerHTML">d</button>
      <div data-hub-tabs><button id="h1e" data-hub-tab=", .platform-header">e</button></div>
      <input id="h1f" data-hub-filter=", .platform-header" data-hub-filter-empty=", body">
      <button id="h1g" data-hub-copy-from=", input">g</button>`, { app: 'ativos' });
    root.querySelector('.workspace-content')!.prepend(box);
    (window as any).HubKit.mount(box);
    for (const id of ['h1a', 'h1b', 'h1c', 'h1d', 'h1e', 'h1g']) (document.getElementById(id) as HTMLElement).click();
    const f = document.getElementById('h1f') as HTMLInputElement;
    f.value = 'x'; f.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise((res) => setTimeout(res, 1500));
    const h = document.querySelector('.platform-header') as HTMLElement | null;
    return {
      same: !!h && h.outerHTML === before,
      hidden: !h || h.hidden || getComputedStyle(h).display === 'none',
      bodyOk: !!document.getElementById('app-root') && !!document.querySelector('.platform-rail'),
      rootKept: !!document.querySelector('#app-root .workspace'),
    };
  });
  expect(r).toEqual({ same: true, hidden: false, bodyOk: true, rootKept: true });
});

test('M1: position:fixed do app não cobre o header nem o rail do hub @core', async ({ page }) => {
  await openFragment(page, '/apps/ativos/movements', '[data-hub-filter]');
  const r = await page.evaluate(() => {
    const root = document.getElementById('app-root')!;
    const d = document.createElement('div');
    (window as any).HubSafe.setHTML(d, '<div id="cover" style="position:fixed;inset:0;z-index:2147483647;background:red">capa</div><div id="pop" popover="auto">x</div><button id="popbtn" popovertarget="pop">p</button>', { app: 'ativos' });
    root.querySelector('.workspace-content')!.append(d);
    const header = document.querySelector('.platform-header')!.getBoundingClientRect();
    const hx = header.left + header.width / 2, hy = header.top + header.height / 2;
    const topAtHeader = document.elementFromPoint(hx, hy);
    const cs = getComputedStyle(root);
    return {
      header: !!topAtHeader && !!topAtHeader.closest('.platform-header'),
      isolated: cs.isolation === 'isolate' && cs.zIndex === '0',
      popover: document.getElementById('pop')!.hasAttribute('popover') || document.getElementById('popbtn')!.hasAttribute('popovertarget'),
    };
  });
  expect(r).toEqual({ header: true, isolated: true, popover: false });
  // O CSS do app não desfaz a contenção (estilo inline !important do hub).
  await page.addStyleTag({ content: '#app-root#app-root#app-root{isolation:auto!important;z-index:auto!important;position:static!important}.platform-header{display:none!important;z-index:-1!important}' });
  const still = await page.evaluate(() => ({
    isolated: getComputedStyle(document.getElementById('app-root')!).isolation === 'isolate',
    header: getComputedStyle(document.querySelector('.platform-header')!).display !== 'none',
  }));
  expect(still).toEqual({ isolated: true, header: true });
});

test('M3: um script da página do app não lê a tela de credencial aberta com window.open', async ({ page }) => {
  const res = await page.goto('/account/password');
  expect(res!.headers()['cross-origin-opener-policy']).toBe('noopener-allow-popups');
  await openFragment(page, '/apps/ativos/movements', '[data-hub-filter]');
  const popup = page.waitForEvent('popup');
  const read = await page.evaluate(async () => {
    const w = window.open('/account/password', '_blank');
    await new Promise((r) => setTimeout(r, 2500));
    try { return { text: w && w.document && w.document.body ? w.document.body.innerText : null, closed: w ? w.closed : null }; }
    catch (e) { return { error: String(e) }; }
  });
  const p = await popup;
  await expect(p.locator('input[type="password"]').first()).toBeVisible();
  // Sem acesso ao documento da tela de credencial (COOP corta o vínculo com quem abriu).
  expect((read as { text?: string | null }).text ?? null).toBeNull();
  await p.close();
});

test('M1 (CSS): folha do app com regras contra o shell não esconde nem troca o header', async ({ page }) => {
  await openFragment(page, '/apps/ativos/movements', '[data-hub-filter]');
  // O CSS do app nunca entra como <link> global: folha construída com @scope.
  const real = await page.evaluate(() => ({
    links: document.querySelectorAll('link[href*="/_frag/"]').length,
    adopted: document.adoptedStyleSheets.length,
  }));
  expect(real.links).toBe(0);
  expect(real.adopted).toBeGreaterThan(0);
  const header = page.locator('.platform-header');
  const box = await header.boundingBox();
  const shot = await header.screenshot({ animations: 'disabled', caret: 'hide' });
  await page.evaluate(() => {
    const css = `.platform-header{transform:scale(0)!important;height:0!important;clip-path:inset(50%)!important;filter:opacity(0)!important;content-visibility:hidden!important}
      .platform-header *{visibility:hidden!important} body::before{content:"Sessão expirada — entre novamente"!important;position:fixed;inset:0;z-index:99999;background:#fff}
      html{filter:blur(8px)!important} #app-root ~ *{display:none!important} :root .platform-header{display:none!important}`;
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, (window as any).HubSafe.scopedSheet(css)];
  });
  const after = await page.evaluate(() => {
    const h = document.querySelector('.platform-header') as HTMLElement;
    const cs = getComputedStyle(h);
    return {
      transform: cs.transform, display: cs.display, visibility: getComputedStyle(h.querySelector('.brand-name')!).visibility,
      before: getComputedStyle(document.body, '::before').content, filter: getComputedStyle(document.documentElement).filter,
    };
  });
  expect(after).toEqual({ transform: 'none', display: 'flex', visibility: 'visible', before: 'none', filter: 'none' });
  expect(await header.boundingBox()).toEqual(box);
  // Captura da região do header: igual à de antes (tolerância de 1% para o que o próprio
  // hub atualiza sozinho, como o contador do sino; um header escondido/trocado muda tudo).
  const after2 = await header.screenshot({ animations: 'disabled', caret: 'hide' });
  const ratio = await page.evaluate(async ([a, b]) => {
    const load = async (b64: string) => { const i = new Image(); i.src = `data:image/png;base64,${b64}`; await i.decode(); return i; };
    const [x, y] = [await load(a), await load(b)];
    if (x.width !== y.width || x.height !== y.height) return 1;
    const c = new OffscreenCanvas(x.width, x.height);
    const g = c.getContext('2d')!;
    g.drawImage(x, 0, 0); const d1 = g.getImageData(0, 0, x.width, x.height).data;
    g.clearRect(0, 0, x.width, x.height); g.drawImage(y, 0, 0); const d2 = g.getImageData(0, 0, x.width, x.height).data;
    let diff = 0;
    for (let i = 0; i < d1.length; i += 4) if (Math.abs(d1[i] - d2[i]) + Math.abs(d1[i + 1] - d2[i + 1]) + Math.abs(d1[i + 2] - d2[i + 2]) > 30) diff++;
    return diff / (d1.length / 4);
  }, [shot.toString('base64'), after2.toString('base64')]);
  expect(ratio).toBeLessThan(0.01);
});
