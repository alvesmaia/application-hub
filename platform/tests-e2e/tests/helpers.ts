import { expect, request, type APIRequestContext, type Page, type BrowserContext } from '@playwright/test';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

export const HUB = process.env.HUB || 'http://localhost:8080';
export const HUB_STAGING = process.env.HUB_STAGING || HUB.replace('//localhost', '//staging.localhost');

/** Demo users (HUB_DEMO_USERS=1 in the compose). */
export const USERS = {
  // HUB_DEMO_PASSWORD: single password of the demo users (public dev environment).
  admin: { email: 'admin@demo.local', password: process.env.HUB_DEMO_PASSWORD || process.env.HUB_ADMIN_PASSWORD || 'admin', name: 'Administrador' },
  operador: { email: 'operador@demo.local', password: process.env.HUB_DEMO_PASSWORD || 'operador', name: 'Operador' },
  leitor: { email: 'leitor@demo.local', password: process.env.HUB_DEMO_PASSWORD || 'leitor', name: 'Leitor' },
} as const;
export type Role = keyof typeof USERS;

export const AUTH_DIR = path.join(__dirname, '..', '.auth');
export const WORK_DIR = path.join(__dirname, '..', '.work');
export const stateFile = (role: Role) => path.join(AUTH_DIR, `${role}.json`);

/** Login through the real form (same-origin POST /login); returns the API context with the cookie. */
export async function apiLogin(email: string, password: string): Promise<APIRequestContext> {
  const ctx = await request.newContext({ baseURL: HUB, extraHTTPHeaders: { origin: HUB } });
  const r = await ctx.post('/login', { form: { email, password, next: '/' }, maxRedirects: 0 });
  expect(r.status(), `login ${email}`).toBe(303);
  return ctx;
}

/** API context with the session saved in global-setup (same browser cookie). */
export async function apiAs(role: Role): Promise<APIRequestContext> {
  return request.newContext({ baseURL: HUB, storageState: stateFile(role), extraHTTPHeaders: { origin: HUB } });
}

export async function json(r: import('@playwright/test').APIResponse) {
  const t = await r.text();
  try { return JSON.parse(t); } catch { return t; }
}

/** Waits for a /api/ops job to finish. */
export async function waitJob(api: APIRequestContext, id: number, timeoutMs = 240_000) {
  const t0 = Date.now();
  for (;;) {
    const j = await json(await api.get(`/api/ops/jobs/${id}`));
    if (j && j.done) return j;
    if (Date.now() - t0 > timeoutMs) throw new Error(`job ${id} não terminou: ${JSON.stringify(j).slice(0, 400)}`);
    await new Promise((r) => setTimeout(r, 1000));
  }
}

/** Opens an app page and waits for the fragment content (loader and skeleton have already passed). */
export async function openApp(page: Page, url: string) {
  await page.goto(url);
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#app-root .app-loading')).toHaveCount(0, { timeout: 30_000 });
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 30_000 });
}

/** Accepts the page's native confirm() dialogs. */
export function autoConfirm(page: Page) {
  page.on('dialog', (d) => d.accept());
  // The hub's confirmation (HubConfirm, its own modal): accepts that too.
  void page.addLocatorHandler(page.locator('.hub-confirm-overlay [data-hub-confirm="yes"]'), async (b) => { await b.click(); });
}

/**
 * Confirms the hub's modal (HubConfirm) opened by a previous click. The `addLocatorHandler` of `autoConfirm` only runs
 * on actions with a locator; after a click that only waits for a response/`poll` nobody confirms — this loop clicks.
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

/** Generates (once) the large spreadsheet with the ativos app itself (`python -m ativos gen-xlsx`). */
export function bigXlsx(rows: number, prefix: string): string {
  fs.mkdirSync(WORK_DIR, { recursive: true });
  const name = `import-${rows}-${prefix}.xlsx`;
  const file = path.join(WORK_DIR, name);
  if (!fs.existsSync(file)) {
    const gen = [`${CP}ativos`];
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

/** `hub` CLI (Python, `platform-py/cli`): HUB_BIN (relative to platform/) or `hub` on the PATH. */
export function hubBin(): string {
  return process.env.HUB_BIN ? path.resolve(PLATFORM, process.env.HUB_BIN) : 'hub';
}

/** App's local image for `hub pack --image` (by digest): E2E_IMAGE_<APP> or E2E_IMAGE. */
export function appImage(app: string): string {
  const v = process.env[`E2E_IMAGE_${app.toUpperCase()}`] || process.env.E2E_IMAGE;
  if (!v) throw new Error(`defina E2E_IMAGE_${app.toUpperCase()}=<imagem>@<id> (ou E2E_IMAGE)`);
  return v;
}

/** Packs `samples/src/<app>` with another version, pointing to the local image by Image ID. */
/** `.hubapp` of a sample app at `version`; `extra` overrides manifest fields (e.g. `requires`). */
export function packSample(app: string, version: string, extra: Record<string, unknown> = {}): string {
  const src = path.join(PLATFORM, 'samples', 'src', app);
  const dir = path.join(WORK_DIR, `pkg-${app}-${version}`);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.cpSync(src, dir, { recursive: true });
  const mf = path.join(dir, 'manifest.json');
  const m = JSON.parse(fs.readFileSync(mf, 'utf8'));
  Object.assign(m, extra, { version });
  fs.writeFileSync(mf, JSON.stringify(m, null, 2));
  const image = appImage(app);
  const out = path.join(WORK_DIR, `${app}-${version}.hubapp`);
  execFileSync(hubBin(), ['pack', '--dir', dir, '--image', image, '--out', out], { stdio: 'inherit' });
  return out;
}

export function docker(...args: string[]): string {
  return execFileSync('docker', args, { encoding: 'utf8', env: { ...process.env, MSYS_NO_PATHCONV: '1' } }).trim();
}

/** `page.goto` that tolerates the reload the screen itself triggers after an action (reloadWithToast). */
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
 * Names of the stack under test (default: compose `app-hub`). Another project: E2E_CONTAINER_PREFIX/E2E_DATA_NETWORK;
 * E2E_OBJSTORE_PASS=<MinIO password>; E2E_IMAGE_<APP>=<local image>@<id> (one per app) and HUB_BIN=<hub CLI>.
 */
export const CP = process.env.E2E_CONTAINER_PREFIX || 'app-hub-';
export const DATA_NETWORK = process.env.E2E_DATA_NETWORK || 'app-hub_default';
export const OBJSTORE_PASS = process.env.E2E_OBJSTORE_PASS || 'hubadmin-demo-senha';

/** Deployments: opens the app's drawer at /admin/deploys and picks the version (the actions apply to the chosen version). */
export async function openDeployVersion(page: Page, app: string, depId: number | string) {
  await gotoSafe(page, "/admin/deploys");
  await page.locator(`tr[data-app="${app}"]`).click();
  const drawer = page.locator("[data-rowdrawer]");
  await expect(drawer).toBeVisible();
  await drawer.locator("select[data-ops-ver]").selectOption(String(depId));
  return drawer;
}
