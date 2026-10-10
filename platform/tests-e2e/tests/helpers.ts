import { expect, request, type APIRequestContext, type Page, type BrowserContext } from '@playwright/test';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

export const HUB = process.env.HUB || 'http://localhost:8080';
export const HUB_STAGING = process.env.HUB_STAGING || HUB.replace('//localhost', '//staging.localhost');

/** Usuários de demonstração (HUB_DEMO_USERS=1 no compose). */
export const USERS = {
  // HUB_DEMO_PASSWORD: senha única dos usuários de demonstração (ambiente público de dev).
  admin: { email: 'admin@demo.local', password: process.env.HUB_DEMO_PASSWORD || process.env.HUB_ADMIN_PASSWORD || 'admin', name: 'Administrador' },
  operador: { email: 'operador@demo.local', password: process.env.HUB_DEMO_PASSWORD || 'operador', name: 'Operador' },
  leitor: { email: 'leitor@demo.local', password: process.env.HUB_DEMO_PASSWORD || 'leitor', name: 'Leitor' },
} as const;
export type Role = keyof typeof USERS;

export const AUTH_DIR = path.join(__dirname, '..', '.auth');
export const WORK_DIR = path.join(__dirname, '..', '.work');
export const stateFile = (role: Role) => path.join(AUTH_DIR, `${role}.json`);

/** Login pelo formulário real (POST /login same-origin); devolve o contexto de API com o cookie. */
export async function apiLogin(email: string, password: string): Promise<APIRequestContext> {
  const ctx = await request.newContext({ baseURL: HUB, extraHTTPHeaders: { origin: HUB } });
  const r = await ctx.post('/login', { form: { email, password, next: '/' }, maxRedirects: 0 });
  expect(r.status(), `login ${email}`).toBe(303);
  return ctx;
}

/** Contexto de API com a sessão salva no global-setup (mesmo cookie do navegador). */
export async function apiAs(role: Role): Promise<APIRequestContext> {
  return request.newContext({ baseURL: HUB, storageState: stateFile(role), extraHTTPHeaders: { origin: HUB } });
}

export async function json(r: import('@playwright/test').APIResponse) {
  const t = await r.text();
  try { return JSON.parse(t); } catch { return t; }
}

/** Espera um job de /api/ops terminar. */
export async function waitJob(api: APIRequestContext, id: number, timeoutMs = 240_000) {
  const t0 = Date.now();
  for (;;) {
    const j = await json(await api.get(`/api/ops/jobs/${id}`));
    if (j && j.done) return j;
    if (Date.now() - t0 > timeoutMs) throw new Error(`job ${id} não terminou: ${JSON.stringify(j).slice(0, 400)}`);
    await new Promise((r) => setTimeout(r, 1000));
  }
}

/** Abre uma página de app e espera o conteúdo do fragmento (loader e skeleton já passaram). */
export async function openApp(page: Page, url: string) {
  await page.goto(url);
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#app-root .app-loading')).toHaveCount(0, { timeout: 30_000 });
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 30_000 });
}

/** Aceita os confirm() nativos da página. */
export function autoConfirm(page: Page) {
  page.on('dialog', (d) => d.accept());
  // Confirmação do hub (HubConfirm, modal próprio): aceita também.
  void page.addLocatorHandler(page.locator('.hub-confirm-overlay [data-hub-confirm="yes"]'), async (b) => { await b.click(); });
}

/**
 * Confirma o modal do hub (HubConfirm) aberto por um clique anterior. O `addLocatorHandler` do `autoConfirm` só roda
 * em ações com locator; depois de um clique que só espera por resposta/`poll` ninguém confirma — este laço clica.
 */
export async function confirmHub(page: Page): Promise<void> {
  const ok = await page.evaluate(async () => {
    for (let i = 0; i < 100; i++) {
      const b = document.querySelector<HTMLElement>('.hub-confirm-overlay [data-hub-confirm="yes"]');
      if (b) { b.click(); return true; }
      await new Promise((r) => setTimeout(r, 100));
    }
    return false;
  });
  if (!ok) throw new Error('modal de confirmação do hub não apareceu');
}

/** Gera (uma vez) a planilha grande com a própria imagem hub-demo-apps (`gen-xlsx`). */
export function bigXlsx(rows: number, prefix: string): string {
  fs.mkdirSync(WORK_DIR, { recursive: true });
  const name = `import-${rows}-${prefix}.xlsx`;
  const file = path.join(WORK_DIR, name);
  if (!fs.existsSync(file)) {
    // Pilha Python (app-hub-*): o próprio app ativos gera (`python -m ativos gen-xlsx`); Rust: hub-demo-apps.
    const gen = CP.startsWith('app-hub-') ? [`${CP}ativos`] : ['--entrypoint', '/usr/local/bin/hub-demo-apps', 'hub-demo-apps'];
    execFileSync('docker', ['run', '--rm', '--user', '0', '--mount', `type=bind,source=${WORK_DIR},target=/w`,
      ...gen, 'gen-xlsx', String(rows), `/w/${name}`, '10', prefix],
    { stdio: 'inherit', env: { ...process.env, MSYS_NO_PATHCONV: '1' } });
  }
  return file;
}

export const uniq = () => `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`.toUpperCase();

export async function newRoleContext(browser: import('@playwright/test').Browser, role: Role): Promise<BrowserContext> {
  return browser.newContext({ storageState: stateFile(role) });
}

export const PLATFORM = path.resolve(__dirname, '..', '..');

/** Binário `hub` (crate hub-cli): HUB_BIN, ou target/{debug,release}/hub[.exe]. */
export function hubBin(): string {
  // Relativo a platform/ (como no CI: HUB_BIN=target/release/hub).
  if (process.env.HUB_BIN) return path.resolve(PLATFORM, process.env.HUB_BIN);
  for (const p of ['target/release/hub', 'target/debug/hub']) {
    for (const ext of ['', '.exe']) {
      const f = path.join(PLATFORM, p + ext);
      if (fs.existsSync(f)) return f;
    }
  }
  throw new Error('binário hub não encontrado: cargo build -p hub-cli (ou HUB_BIN)');
}

/** Empacota `samples/src/<app>` com outra versão, apontando para a imagem local por Image ID. */
export function packSample(app: string, version: string): string {
  const src = path.join(PLATFORM, 'samples', 'src', app);
  const dir = path.join(WORK_DIR, `pkg-${app}-${version}`);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.cpSync(src, dir, { recursive: true });
  const mf = path.join(dir, 'manifest.json');
  const m = JSON.parse(fs.readFileSync(mf, 'utf8'));
  m.version = version;
  fs.writeFileSync(mf, JSON.stringify(m, null, 2));
  const image = process.env[`E2E_IMAGE_${app.toUpperCase()}`] || process.env.E2E_IMAGE || `hub-demo-apps@${execFileSync('docker', ['image', 'inspect', '-f', '{{.Id}}', 'hub-demo-apps'], { encoding: 'utf8' }).trim()}`;
  const out = path.join(WORK_DIR, `${app}-${version}.hubapp`);
  execFileSync(hubBin(), ['pack', '--dir', dir, '--image', image, '--out', out], { stdio: 'inherit' });
  return out;
}

export function docker(...args: string[]): string {
  return execFileSync('docker', args, { encoding: 'utf8', env: { ...process.env, MSYS_NO_PATHCONV: '1' } }).trim();
}

/** `page.goto` que tolera o reload que a própria tela dispara depois de uma ação (reloadWithToast). */
export async function gotoSafe(page: Page, url: string) {
  for (let i = 0; ; i++) {
    try {
      await page.goto(url);
      return;
    } catch (e) {
      if (i >= 4 || !/interrupted by another navigation|ERR_ABORTED/.test(String(e))) throw e;
      await page.waitForLoadState('load').catch(() => {});
    }
  }
}

/**
 * Nomes da pilha em teste. Padrão = pilha Rust (`hub-*`); contra o hub Python (compose `app-hub`) use
 * E2E_CONTAINER_PREFIX=app-hub- E2E_DATA_NETWORK=app-hub_default E2E_OBJSTORE_PASS=<senha do MinIO> E2E_ATIVOS_STACK=python; E2E_IMAGE_<APP>=<imagem local>@<id> (uma por app) e HUB_BIN=<hub.exe do CLI Python>.
 */
export const CP = process.env.E2E_CONTAINER_PREFIX || 'hub-';
export const DATA_NETWORK = process.env.E2E_DATA_NETWORK || 'hub-data';
export const OBJSTORE_PASS = process.env.E2E_OBJSTORE_PASS || 'hubadmin-demo-senha';

/** Implantações: abre a gaveta do app em /admin/deploys e escolhe a versão (as ações valem para a versão escolhida). */
export async function openDeployVersion(page: Page, app: string, depId: number | string) {
  await gotoSafe(page, "/admin/deploys");
  await page.locator(`tr[data-app="${app}"]`).click();
  const drawer = page.locator("[data-rowdrawer]");
  await expect(drawer).toBeVisible();
  await drawer.locator("select[data-ops-ver]").selectOption(String(depId));
  return drawer;
}
