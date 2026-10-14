import path from 'node:path';

export const HUB = process.env.HUB || 'http://localhost:8080';
/** Report directory (run.mjs sets SWEEP_OUT; without it, a local folder). */
export const OUT_DIR = process.env.SWEEP_OUT || path.join(__dirname, '..', 'test-results', 'ui-sweep');
export const ROUTES_FILE = path.join(OUT_DIR, 'routes.json');
export const FINDINGS_DIR = path.join(OUT_DIR, 'findings');
export const SHOTS_DIR = path.join(OUT_DIR, 'shots');

/**
 * (Left out: /account/env, /account/staging and /account/prefs require parameters or only accept POST;
 * /apps, /connections, /admin/applications and /admin/oauth are prefixes/redirects.)
 * GET pages without a parameter from the hub-web route table (Python hub, `platform-py/hub`) —
 * covers what no link reaches. The apps come in through discovery (rail/sidebar).
 */
export const STATIC_ROUTES = [
  '/', '/inbox', '/search', '/status', '/applications',
  '/account', '/account/security', '/account/access', '/account/password',
  '/admin', '/admin/audit', '/admin/backups', '/admin/connections',
  '/admin/deploys', '/admin/env', '/admin/health', '/admin/logs',
  '/admin/users?tab=roles', '/admin/runtime', '/admin/tasks', '/admin/tokens', '/admin/users',
  '/developers', '/developers/agente', '/developers/cli', '/developers/sdk', '/developers/contrato', '/developers/guias/primeiro-app',
];

/** App screens with state in the query that no sidebar link reaches — included only if the app
 *  exists in the stack (the light stack only has ativos). */
export const APP_ROUTES: Record<string, string[]> = {
  // Wave 13 — Coletor: location/order/code chosen.
  coletor: ['/apps/coletor/escanear?codigo=COL-0001', '/apps/coletor/folha', '/apps/coletor/diagnostico'],
};

/** Links left out of the sweep: exit, destructive/mutable, downloads, APIs, resources and test routes. */
const EXCLUDE = [
  /^\/logout/, /^\/api\//, /^\/_frag\//, /^\/_hub\//, /^\/assets\//, /^\/auth\//, /^\/oauth\//, /^\/mcp/,
  /^\/(healthz|readyz|metrics)/, /\.(csv|json|xlsx|xls|pdf|zip|tar|gz|txt|ndjson|svg|png)$/i,
  /\/(download|export|logout)(\/|$)/,
  /\/(revoke|delete|remove|stop|start|restart|rotate|deprovision|confirm|decide|read|approve|block-client|unblock-client|remove-client|revoke-grant|test|archive)(\/|$)/,
  /e2e-hostil/, // test-only route (HUB_DEMO_E2E_HOSTILE) — hostile HTML on purpose
];
export const isExcluded = (p: string) => EXCLUDE.some((re) => re.test(p));

/** Route pattern: segments that look like identifiers become `:id`. */
export function patternOf(p: string): string {
  return p.split('/').map((s) => (/^\d+$/.test(s) || /^[0-9a-f-]{16,}$/i.test(s) || (/\d/.test(s) && /[a-z]/i.test(s) && s.length >= 12) ? ':id' : s)).join('/') || '/';
}

/** Credential/consent screens: outside the shell by design. */
export const CREDENTIAL_PAGES = [/^\/login/, /^\/account\/password/, /^\/oauth\/authorize/, /^\/account\/staging\/confirm/, /step-up|reauth|consent/];

// light = hub default (forces nothing); os-dark = hub default with the OS in dark (must
// stay light); dark = explicit choice; auto = "Automatic (system)" with the OS in dark.
export type Theme = 'light' | 'dark' | 'os-dark' | 'auto';
export type Finding = {
  route: string; pattern: string; theme: Theme; role: string; viewport: string; layer: string;
  check: string; selector: string; message: string; screenshot?: string; status?: number;
};
