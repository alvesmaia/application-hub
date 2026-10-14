import { chromium, request } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import loginAll from '../tests/global-setup';
import { HUB, stateFile, type Role } from '../tests/helpers';
import { APP_ROUTES, OUT_DIR, ROUTES_FILE, STATIC_ROUTES, isExcluded, patternOf } from './common';

/**
 * Sweep global setup: (1) admin/operator/reader sessions (reuses the specs' global-setup); (2) route
 * discovery — rail/menus/same-origin links up to depth N
 * (SWEEP_DEPTH, default 2) per role + the static list from the hub-web route table;
 * deduplicates by pattern (`/x/:id`) and writes `routes.json` to the report directory.
 */
export default async function setup() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  await loginAll();
  const depth = Number(process.env.SWEEP_DEPTH || 2);
  const roles: Role[] = process.env.SWEEP_MODE === 'quick' ? ['admin'] : ['admin', 'operador', 'leitor'];
  const found = new Map<string, { path: string; from: string; roles: Set<string> }>();
  const excluded = new Set<string>();
  const add = (p: string, from: string, role: string) => {
    const pat = patternOf(p);
    const cur = found.get(pat);
    if (cur) cur.roles.add(role);
    else found.set(pat, { path: p, from, roles: new Set([role]) });
  };
  // SWEEP_ROUTES (e.g. an app's dev hub): only those routes and what they link under SWEEP_PREFIX.
  const only = (process.env.SWEEP_ROUTES || '').split(',').filter(Boolean);
  const prefix = process.env.SWEEP_PREFIX || '';
  for (const r of only.length ? only : STATIC_ROUTES) add(r, 'tabela-de-rotas', 'static');
  {
    const probe = await request.newContext({ baseURL: HUB, storageState: stateFile('admin') });
    for (const [app, routes] of Object.entries(APP_ROUTES)) {
      const ok = (await probe.get(`/apps/${app}`, { maxRedirects: 0 }).catch(() => null))?.status() === 200;
      if (ok) for (const r of routes) add(r, `tabela-de-rotas (${app})`, 'static');
    }
    await probe.dispose();
  }

  const browser = await chromium.launch();
  for (const role of roles) {
    const ctx = await browser.newContext({ storageState: stateFile(role), viewport: { width: 1440, height: 900 } });
    // Nothing but GET during discovery.
    await ctx.route('**/*', (r) => (['GET', 'HEAD'].includes(r.request().method()) ? r.continue() : r.abort()));
    let frontier = only.length ? [...only] : ['/'];
    const visited = new Set<string>();
    for (let d = 0; d <= depth && frontier.length; d++) {
      const next: string[] = [];
      // 4 tabs in parallel
      const queue = frontier.filter((p) => !visited.has(patternOf(p)));
      queue.forEach((p) => visited.add(patternOf(p)));
      await Promise.all([0, 1, 2, 3].map(async () => {
        const page = await ctx.newPage();
        for (let p = queue.shift(); p !== undefined; p = queue.shift()) {
          try {
            await page.goto(HUB + p, { waitUntil: 'load', timeout: 30_000 });
            await page.waitForFunction(() => !document.querySelector('.app-loading, .component-loading, [aria-busy="true"], .skeleton, .app-skeleton'), null, { timeout: 8_000 }).catch(() => undefined);
            await page.waitForTimeout(300);
            const hrefs: string[] = await page.$$eval('a[href]', (as) => as.map((a) => (a as HTMLAnchorElement).href));
            for (const h of hrefs) {
              const u = new URL(h);
              if (u.origin !== new URL(HUB).origin) continue;
              const pth = u.pathname.replace(/\/$/, '') || '/';
              if (isExcluded(pth) || (prefix && !pth.startsWith(prefix))) { excluded.add(pth); continue; }
              add(pth, p, role);
              if (!visited.has(patternOf(pth))) next.push(pth);
            }
          } catch { /* page that fails to open becomes a finding in the sweep */ }
        }
        await page.close();
      }));
      frontier = [...new Set(next)];
    }
    await ctx.close();
  }
  await browser.close();

  const routes = [...found.entries()].map(([pattern, v]) => ({ pattern, path: v.path, from: v.from, linkedFor: [...v.roles] }))
    .sort((a, b) => a.pattern.localeCompare(b.pattern));
  fs.writeFileSync(ROUTES_FILE, JSON.stringify({ routes, excluded: [...excluded].sort() }, null, 2));
  console.log(`[ui-sweep] ${routes.length} padrões de rota (profundidade ${depth}; ${excluded.size} links excluídos) → ${path.relative(process.cwd(), ROUTES_FILE)}`);
  for (const r of routes) console.log(`  ${r.pattern.padEnd(40)} ${r.linkedFor.join(',')}`);
}
