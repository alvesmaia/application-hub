import { test, expect, request } from '@playwright/test';
import { createHash, randomBytes } from 'node:crypto';
import { HUB, stateFile, uniq } from './helpers';

// Onda 12 (2.2) — OAuth 2.1 do MCP pelo navegador: um cliente registrado dinamicamente
// (como o Langdock) pede acesso; o usuário vê a tela de consentimento (cliente, host de
// retorno, permissões), aprova, o código volta para o cliente e vira token que usa o
// /mcp; a concessão aparece em Conta › Apps autorizados e na Administração, e revogar
// derruba o token. Compose no ar (HUB_DEMO_USERS=1).

test.use({ storageState: stateFile('admin') });
const RUN = uniq();
const REDIRECT = 'http://127.0.0.1:53682/e2e-callback';

test('consentimento OAuth: aprovar, usar o /mcp, ver e revogar em Apps autorizados @core', async ({ page }) => {
  const api = await request.newContext({ baseURL: HUB });
  const reg = await api.post('/oauth/register', {
    data: { client_name: `Langdock E2E ${RUN}`, redirect_uris: [REDIRECT], token_endpoint_auth_method: 'none' },
  });
  expect(reg.status()).toBe(201);
  const cid = (await reg.json()).client_id as string;
  const verifier = randomBytes(48).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  const q = new URLSearchParams({
    response_type: 'code', client_id: cid, redirect_uri: REDIRECT, code_challenge: challenge,
    code_challenge_method: 'S256', state: 'e2e', scope: 'mcp api:write', resource: `${process.env.HUB_PUBLIC_URL || HUB}/mcp`,
  });
  // O navegador volta para o cliente (127.0.0.1:53682, porta fechada; a 9 o Chrome bloqueia): o teste captura a URL de retorno.
  let callback = '';
  // Redirecionamento de um POST não passa por page.route: registra o pedido (a porta está fechada).
  page.on('request', (r) => { if (r.url().startsWith(REDIRECT)) callback = r.url(); });
  const res = await page.goto(`/oauth/authorize?${q}`);
  expect(res!.status()).toBe(200);
  const csp = res!.headers()['content-security-policy'] || '';
  expect(csp).toContain("script-src 'none'");
  expect(csp).toContain('form-action');
  await expect(page.getByRole('heading', { name: 'Conectar um aplicativo ao hub' })).toBeVisible();
  await expect(page.locator('[data-oauth-client]')).toHaveText(`Langdock E2E ${RUN}`);
  await expect(page.locator('[data-oauth-redirect-host]')).toHaveText('127.0.0.1:53682');
  await expect(page.locator('input[name="scope"][value="api:write"]')).toBeChecked();
  // Só leitura: desmarca "api:write".
  await page.locator('input[name="scope"][value="api:write"]').uncheck();
  await page.locator('[data-oauth-approve]').click();
  await expect.poll(() => callback).toContain('code=');
  const cb = new URL(callback);
  expect(cb.searchParams.get('state')).toBe('e2e');
  expect(cb.searchParams.get('iss')).toBe(process.env.HUB_PUBLIC_URL || HUB);

  const tok = await api.post('/oauth/token', {
    form: { grant_type: 'authorization_code', code: cb.searchParams.get('code')!, redirect_uri: REDIRECT, client_id: cid, code_verifier: verifier, resource: `${process.env.HUB_PUBLIC_URL || HUB}/mcp` },
  });
  expect(tok.status()).toBe(200);
  const t = await tok.json();
  expect(t.scope).toBe('mcp');
  const mcp = await request.newContext({ baseURL: HUB, extraHTTPHeaders: { authorization: `Bearer ${t.access_token}` } });
  const list = async () => mcp.post('/mcp', {
    headers: { accept: 'application/json', 'mcp-protocol-version': '2026-07-28', 'mcp-method': 'tools/list' },
    data: { jsonrpc: '2.0', id: 1, method: 'tools/list', params: { _meta: { 'io.modelcontextprotocol/protocolVersion': '2026-07-28' } } },
  });
  const l = await list();
  expect(l.status()).toBe(200);
  const names = ((await l.json()).result.tools as { name: string }[]).map((x) => x.name);
  expect(names).toContain('apps_list');
  expect(names).not.toContain('deployments_rollback');

  // Acesso › Credenciais lista o cliente e a concessão.
  await page.goto('/admin/tokens#oauth');
  await expect(page.locator('[data-oauth-client-row]', { hasText: `Langdock E2E ${RUN}` })).toBeVisible();
  await page.locator('[data-ptab="grants"]').click();
  await expect(page.locator('[data-oauth-grant-row]', { hasText: `Langdock E2E ${RUN}` }).first()).toBeVisible();

  // Conta › Apps autorizados (menu do usuário) → revogar.
  await page.goto('/');
  await page.locator('details.hub-user-menu > summary').click();
  await page.locator('.hub-user-popover').getByRole('link', { name: 'Minha conta' }).click();
  await page.locator('nav.hub-tabbar').getByRole('link', { name: 'Acessos' }).click();
  await expect(page).toHaveURL(/\/account\/access$/);
  const row = page.locator('[data-oauth-grant]', { hasText: `Langdock E2E ${RUN}` });
  await expect(row).toBeVisible();
  await expect(row).toContainText('127.0.0.1:53682');
  await expect(row).toContainText('Não verificado');
  await row.locator('[data-oauth-revoke]').click();
  await page.locator('#revoke-app [data-acct-ok]').click();
  await expect(page.getByText('Acesso revogado.')).toBeVisible();
  await expect(page.locator('[data-oauth-grant]', { hasText: `Langdock E2E ${RUN}` })).toHaveCount(0);
  expect((await list()).status()).toBe(401);
});

test('pedido de autorização inválido mostra erro e nunca redireciona para host não registrado @core', async ({ page }) => {
  const api = await request.newContext({ baseURL: HUB });
  const reg = await api.post('/oauth/register', { data: { client_name: `E2E inválido ${RUN}`, redirect_uris: [REDIRECT], token_endpoint_auth_method: 'none' } });
  const cid = (await reg.json()).client_id as string;
  const q = new URLSearchParams({ response_type: 'code', client_id: cid, redirect_uri: 'https://evil.example/cb', code_challenge: 'a'.repeat(43), code_challenge_method: 'S256' });
  const res = await page.goto(`/oauth/authorize?${q}`);
  expect(res!.status()).toBe(400);
  await expect(page.locator('[data-oauth-error]')).toContainText('redirect_uri');
  expect(page.url()).toContain('/oauth/authorize');
});
