import { test, expect, request } from '@playwright/test';
import { HUB, json, stateFile, uniq } from './helpers';

// `hub login`: the CLI asks for a code, the person approves it in the browser and the CLI gets a session that
// acts as that person on every surface (API, deploy, MCP). The administration cuts every access of someone in
// one step (Users › ⋯ › Revogar todos os acessos).

test.use({ storageState: stateFile('admin') });
const RUN = uniq();
const DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code';

test('login do CLI: código aprovado no navegador dá acesso como a pessoa @core', async ({ page }) => {
  const cli = await request.newContext({ baseURL: HUB });
  const start = await json(await cli.post('/oauth/device_authorization', { form: { client_id: 'hub-cli' } }));
  expect(start.user_code).toMatch(/^[B-Z]{4}-[B-Z]{4}$/);
  const pending = await cli.post('/oauth/token', { form: { grant_type: DEVICE_GRANT, device_code: start.device_code, client_id: 'hub-cli' } });
  expect((await pending.json()).error).toBe('authorization_pending');

  await page.goto(new URL(start.verification_uri_complete).pathname + new URL(start.verification_uri_complete).search);
  await expect(page.locator('#user_code')).toHaveValue(start.user_code);
  await page.locator('[data-oauth-approve]').click();
  await expect(page.locator('[data-oauth-device-result]')).toContainText('Hub CLI está conectado');

  await page.waitForTimeout(5200); // the poll interval the hub asked for
  const tok = await json(await cli.post('/oauth/token', { form: { grant_type: DEVICE_GRANT, device_code: start.device_code, client_id: 'hub-cli' } }));
  expect(tok.access_token).toMatch(/^hubo_/);
  expect(tok.scope.split(' ')).toEqual(expect.arrayContaining(['deploy', 'api:write', 'mcp']));
  const asMe = await request.newContext({ baseURL: HUB, extraHTTPHeaders: { authorization: `Bearer ${tok.access_token}` } });
  expect((await asMe.get('/api/v1/apps')).status()).toBe(200);
  expect((await asMe.get('/api/ops/deployments')).status()).toBe(200);

  // My account › Access lists the CLI session; revoking it ends the CLI's access.
  await page.goto('/account/access');
  const row = page.locator('[data-oauth-grant]', { hasText: 'Hub CLI' }).first();
  await expect(row).toBeVisible();
  await row.locator('[data-oauth-revoke]').click();
  await page.locator('#revoke-app [data-acct-ok]').click();
  await expect(page.getByText('Acesso revogado.')).toBeVisible();
  expect((await asMe.get('/api/v1/apps')).status()).toBe(401);
  await Promise.all([cli.dispose(), asMe.dispose()]);
});

test('administração revoga todos os acessos de uma pessoa @core', async ({ page }) => {
  await page.goto('/admin/users');
  const admin = page.request; // same cookies as the page (a separate context would rotate the session under it)
  const email = `e2e-cli-${RUN.toLowerCase()}@demo.local`;
  const password = `E2e-${RUN}-senha!9`;
  const made = await json(await admin.post('/api/admin/users', { headers: { origin: HUB }, data: { email, name: `Aaa E2E CLI ${RUN}`, roles: ['User'], password, must_change_password: false } }));
  const person = await request.newContext({ baseURL: HUB, storageState: { cookies: [], origins: [] }, extraHTTPHeaders: { origin: HUB } });
  expect((await person.post('/login', { form: { email, password, next: '/' }, maxRedirects: 0 })).status()).toBe(303); // own cookie jar: signing in over the admin's cookie would replace that session
  const tok = await json(await person.post('/api/me/tokens', { data: { name: `e2e ${RUN}`, actions: ['api:read'] } }));
  const bearer = await request.newContext({ baseURL: HUB, extraHTTPHeaders: { authorization: `Bearer ${tok.token}` } });
  expect((await bearer.get('/api/v1/apps')).status()).toBe(200);

  await page.reload();
  const tr = page.locator(`tr[data-user-id="${made.id}"]`);
  await tr.locator('summary.row-action').click();
  await tr.locator(`[data-user-revoke-access="${made.id}"]`).click();
  await page.locator('[data-hub-confirm="yes"]').click();
  await expect(page.getByText('Acessos revogados: 1 token(s), 0 app(s).')).toBeVisible();
  expect((await bearer.get('/api/v1/apps')).status()).toBe(401);
  expect((await person.get('/api/me/tokens')).status()).toBe(401);
  await Promise.all([person.dispose(), bearer.dispose()]);
});
