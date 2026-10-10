import path from 'node:path';

export const HUB = process.env.HUB || 'http://localhost:8080';
/** Diretório do relatório (o run.mjs define SWEEP_OUT; sem ele, uma pasta local). */
export const OUT_DIR = process.env.SWEEP_OUT || path.join(__dirname, '..', 'test-results', 'ui-sweep');
export const ROUTES_FILE = path.join(OUT_DIR, 'routes.json');
export const FINDINGS_DIR = path.join(OUT_DIR, 'findings');
export const SHOTS_DIR = path.join(OUT_DIR, 'shots');

/**
 * (Fora: /account/env, /account/staging e /account/prefs exigem parâmetros ou só aceitam POST;
 * /apps, /connections, /admin/applications e /admin/oauth são prefixos/redirecionamentos.)
 * Páginas GET sem parâmetro da tabela de rotas do hub-web (hub Python, `platform-py/hub`) —
 * cobre o que nenhum link alcança. Os apps entram pela descoberta (rail/sidebar).
 */
export const STATIC_ROUTES = [
  '/', '/inbox', '/search', '/status', '/applications',
  '/account', '/account/security', '/account/access', '/account/password',
  '/admin', '/admin/audit', '/admin/backups', '/admin/connections',
  '/admin/deploys', '/admin/env', '/admin/health', '/admin/logs',
  '/admin/users?tab=roles', '/admin/runtime', '/admin/tasks', '/admin/tokens', '/admin/users',
  '/developers', '/developers/agente', '/developers/cli', '/developers/sdk', '/developers/contrato', '/developers/guias/primeiro-app',
];

/** Telas de app com estado na query que nenhum link de sidebar alcança — entram só se o app
 *  existir na pilha (a pilha leve só tem o ativos). */
export const APP_ROUTES: Record<string, string[]> = {
  // Onda 13 — Coletor: local/pedido/código escolhidos.
  coletor: ['/apps/coletor/escanear?codigo=COL-0001', '/apps/coletor/folha', '/apps/coletor/diagnostico'],
};

/** Links fora da varredura: saída, destrutivos/mutáveis, downloads, APIs, recursos e rotas de teste. */
const EXCLUDE = [
  /^\/logout/, /^\/api\//, /^\/_frag\//, /^\/_hub\//, /^\/assets\//, /^\/auth\//, /^\/oauth\//, /^\/mcp/,
  /^\/(healthz|readyz|metrics)/, /\.(csv|json|xlsx|xls|pdf|zip|tar|gz|txt|ndjson|svg|png)$/i,
  /\/(download|export|logout)(\/|$)/,
  /\/(revoke|delete|remove|stop|start|restart|rotate|deprovision|confirm|decide|read|approve|block-client|unblock-client|remove-client|revoke-grant|test|archive)(\/|$)/,
  /e2e-hostil/, // rota só de teste (HUB_DEMO_E2E_HOSTILE) — HTML hostil de propósito
];
export const isExcluded = (p: string) => EXCLUDE.some((re) => re.test(p));

/** Padrão da rota: segmentos que parecem identificadores viram `:id`. */
export function patternOf(p: string): string {
  return p.split('/').map((s) => (/^\d+$/.test(s) || /^[0-9a-f-]{16,}$/i.test(s) || (/\d/.test(s) && /[a-z]/i.test(s) && s.length >= 12) ? ':id' : s)).join('/') || '/';
}

/** Telas de credencial/consentimento: fora do shell por desenho. */
export const CREDENTIAL_PAGES = [/^\/login/, /^\/account\/password/, /^\/account\/agent-approval/, /^\/oauth\/authorize/, /^\/account\/staging\/confirm/, /step-up|reauth|consent/];

// light = padrão do hub (sem forçar nada); os-dark = padrão do hub com o SO escuro (deve
// continuar claro); dark = escolha explícita; auto = "Automático (sistema)" com o SO escuro.
export type Theme = 'light' | 'dark' | 'os-dark' | 'auto';
export type Finding = {
  route: string; pattern: string; theme: Theme; role: string; viewport: string; layer: string;
  check: string; selector: string; message: string; screenshot?: string; status?: number;
};
