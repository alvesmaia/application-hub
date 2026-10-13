import { test, expect, request, type APIRequestContext } from '@playwright/test';
import { HUB, USERS, stateFile, uniq } from './helpers';

// Onda 11 — API pública v1 e MCP: a documentação renderiza (CSP, sem script inline), um
// token de API criado pela tela chama a API, e um cliente MCP (JSON-RPC por Streamable
// HTTP) usa as ferramentas do hub e as dos apps (agregadas pelo hub, em nome do usuário).
// Compose no ar (HUB_DEMO_USERS=1).

test.use({ storageState: stateFile('admin') });
const RUN = uniq();

async function bearer(token: string): Promise<APIRequestContext> {
  return request.newContext({ baseURL: HUB, extraHTTPHeaders: { authorization: `Bearer ${token}` } });
}

async function createToken(page: import('@playwright/test').Page, name: string, actions: string[], apps: string[] = []) {
  await page.goto('/admin/tokens');
  await page.click('[data-token-new]');
  const m = page.locator('.overlay[data-users-modal] form');
  await m.locator('input[name="name"]').fill(name);
  for (const a of ['upload', 'status']) await m.locator(`input[name="actions"][value="${a}"]`).uncheck();
  for (const a of actions) await m.locator(`input[name="actions"][value="${a}"]`).check();
  for (const a of apps) await m.locator(`input[name="apps"][value="${a}"]`).check();
  await m.locator('button[type="submit"]').click();
  const secret = page.locator('[data-token-secret] [data-token-value]');
  await expect(secret).toHaveText(/^hubt_/);
  return (await secret.textContent())!.trim();
}

test('documentação da API renderiza a partir do OpenAPI 3.1 (sem script inline)', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  const res = await page.goto('/api/docs');
  expect(res!.status()).toBe(200);
  const scriptSrc = (res!.headers()['content-security-policy'] || '').split(';').find((d) => d.trim().startsWith('script-src')) || '';
  expect(scriptSrc).not.toContain("'unsafe-inline'");
  await expect(page.getByRole('heading', { name: /API do Application Hub/ })).toBeVisible();
  const rollback = page.locator('[data-op="deployments_rollback"]');
  await expect(rollback).toBeVisible();
  await expect(rollback.locator('.badge.danger')).toHaveText('destrutiva');
  await rollback.locator('summary').click();
  await expect(rollback.locator('pre').last()).toContainText('Authorization: Bearer');
  expect(errors, errors.join('\n')).toEqual([]);
});

test('token de API criado pela tela chama a API v1 (leitura sim, escrita não)', async ({ page }) => {
  const token = await createToken(page, `API E2E ${RUN}`, ['api:read']);
  const api = await bearer(token);
  const apps = await api.get('/api/v1/apps');
  expect(apps.status()).toBe(200);
  const body = await apps.json();
  expect(body.items.map((a: { id: string }) => a.id)).toEqual(expect.arrayContaining(['ativos', 'fornecedores', 'servicos', 'coletor']));
  expect(body.items[0].api.enabled).toBe(true);
  const deps = await api.get('/api/v1/deployments?limit=2');
  expect(deps.status()).toBe(200);
  expect((await deps.json()).limit).toBe(2);
  const w = await api.post('/api/v1/audit/verify');
  expect(w.status()).toBe(401);
  expect(w.headers()['content-type']).toContain('application/problem+json');
  // API do app pelo gateway com token app-api.
  const appTok = await createToken(page, `APP E2E ${RUN}`, ['app-api'], ['ativos']);
  const appApi = await bearer(appTok);
  const assets = await appApi.get('/apps/ativos/api/v1/assets?q=a');
  expect(assets.status()).toBe(200);
  expect(typeof (await assets.json()).total).toBe('number');
  expect((await appApi.get('/apps/servicos/api/v1/tickets')).status()).toBe(403);
});

test('cliente MCP: ferramentas do hub e dos apps, confirmação de ação destrutiva', async ({ page }) => {
  const token = await createToken(page, `MCP E2E ${RUN}`, ['mcp', 'api:read', 'api:write']);
  const c = await bearer(token);
  let session = '';
  let n = 0;
  const rpc = async (method: string, params: object = {}) => {
    const headers: Record<string, string> = { accept: 'application/json, text/event-stream' };
    if (session) { headers['mcp-session-id'] = session; headers['mcp-protocol-version'] = '2025-06-18'; }
    const r = await c.post('/mcp', { data: { jsonrpc: '2.0', id: ++n, method, params }, headers });
    expect(r.status(), await r.text()).toBe(200);
    session = r.headers()['mcp-session-id'] || session;
    return r.json();
  };
  const init = await rpc('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'e2e', version: '1' } });
  expect(init.result.serverInfo.name).toBe('application-hub');
  expect(session).not.toBe('');
  const tools: { name: string; annotations: { destructiveHint: boolean } }[] = (await rpc('tools/list')).result.tools;
  const names = tools.map((t) => t.name);
  for (const t of ['apps_list', 'deployments_rollback', 'ativos.assets_list', 'servicos.tickets_list']) {
    expect(names, t).toContain(t);
  }
  expect(tools.find((t) => t.name === 'ativos.assets_delete')!.annotations.destructiveHint).toBe(true);
  const list = (await rpc('tools/call', { name: 'ativos.assets_list', arguments: {} })).result;
  expect(list.isError).toBe(false);
  expect(list.structuredContent.total).toBeGreaterThan(0);
  // Mutação de app sem dica (POST no OpenAPI do pacote) também exige confirmação (I3).
  const appTools = tools.filter((t) => t.name.includes('.'));
  expect(appTools.some((t) => t.annotations.destructiveHint && !t.name.endsWith('_delete')), JSON.stringify(appTools.map((t) => t.name))).toBe(true);
  // Destrutiva (padrão confirm): a 1ª chamada só devolve o confirm_token; a 2ª, com ele, executa.
  const args = { id: `9${RUN}`.replace(/\D/g, '').slice(0, 9) || '999999' };
  const first = (await rpc('tools/call', { name: 'deployments_rollback', arguments: args })).result;
  expect(first.structuredContent.confirmation_required, JSON.stringify(first)).toBe(true);
  const cft = first.structuredContent.confirm_token;
  const second = (await rpc('tools/call', { name: 'deployments_rollback', arguments: { ...args, confirm_token: cft } })).result;
  expect(second.isError).toBe(true); // executou (e a implantação não existe)
  expect(second.content[0].text).toContain('404');
  // Tudo auditado.
  await page.goto('/admin/audit?scope=platform');
  await expect(page.locator('#app-root')).toContainText('MCP: deployments_rollback');
});
