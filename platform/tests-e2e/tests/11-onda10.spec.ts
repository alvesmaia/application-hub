import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { apiAs, autoConfirm, docker, gotoSafe, json, newRoleContext, openApp, WORK_DIR, CP } from './helpers';

// Wave 10 — Applications › Connections with SharePoint (Graph simulator, profile
// sharepoint-sim of the compose): create the connection on the screen, "Test connection" (token, site,
// library, base folder, write/read/delete the probe), link the app × environment,
// "Apply (restart)" and use it from the app — contract documents (contratos, Python) and
// supplier attachments — without the app receiving the credential. Security: the broker only
// serves the app's token from the app's network, and only the connections linked to it.
//
// Stack up with the simulator:
//   HUB_GRAPH_URL=http://hub-graphsim:8080/v1.0 HUB_GRAPH_LOGIN_URL=http://hub-graphsim:8080 \
//   docker compose --profile sharepoint-sim up -d

const SIM = {
  tenant: '00000000-0000-0000-0000-00000000c0de',
  client: '11111111-2222-3333-4444-555555555555',
  secret: 'segredo-do-simulador',
  site: 'https://contoso.sharepoint.com/sites/Hub',
  drive: 'Documentos',
};

async function simEnabled(api: APIRequestContext): Promise<boolean> {
  const v = await json(await api.get('/api/admin/connections'));
  return v && v.graph && v.graph.default === false;
}

async function removeIfExists(api: APIRequestContext, name: string) {
  await api.delete(`/api/admin/connections/${name}`);
}

async function createViaUi(page: Page, name: string, base: string) {
  await gotoSafe(page, '/admin/connections');
  await page.locator('[data-conn-new-menu] > summary').click();
  await page.getByRole('menuitem', { name: 'SharePoint' }).click();
  const dlg = page.getByRole('dialog');
  await expect(dlg).toBeVisible();
  await dlg.getByLabel('Nome (usado pelos apps)').fill(name);
  await dlg.getByLabel('Tenant ID (diretório do Entra ID)').fill(SIM.tenant);
  await dlg.getByLabel('Client ID (app registration)').fill(SIM.client);
  await dlg.getByLabel('Autenticação').selectOption('secret');
  await dlg.getByLabel('Segredo do cliente').fill(SIM.secret);
  await dlg.getByLabel('Site (URL ou id)').fill(SIM.site);
  await dlg.getByLabel('Biblioteca de documentos (vazio = padrão)').fill(SIM.drive);
  await dlg.getByLabel('Pasta-base (opcional)').fill(base);
  await dlg.getByRole('button', { name: 'Salvar' }).click();
  await expect(page.locator(`tr[data-conn-row="${name}"]`)).toBeVisible();
  // The secret never comes back to the page.
  expect(await page.content()).not.toContain(SIM.secret);
}

async function testViaUi(page: Page, name: string) {
  const row = page.locator(`tr[data-conn-row="${name}"]`);
  await row.getByRole('button', { name: 'Testar conexão' }).click();
  const result = page.locator(`[data-conn-result="${name}"]`);
  await expect(result).toContainText('Conexão OK', { timeout: 60_000 });
  for (const step of ['token', 'site', 'drive', 'folder', 'write', 'read', 'delete']) {
    await expect(result.locator(`li[data-step="${step}"]`)).toHaveClass(/done/);
  }
}

async function bindViaUi(page: Page, name: string, label: string) {
  await gotoSafe(page, '/admin/connections');
  await page.locator(`tr[data-conn-row="${name}"]`).getByRole('button', { name: 'Vínculos' }).click();
  const dlg = page.getByRole('dialog');
  await dlg.getByLabel(label).check();
  await dlg.getByRole('button', { name: 'Salvar' }).click();
  await expect(page.locator(`tr[data-conn-row="${name}"]`)).toContainText(label.split(' · ')[0]);
}

async function applyViaUi(page: Page, app: string) {
  const btn = page.locator(`[data-ops-apply="${app}"]`);
  await expect(async () => {
    await gotoSafe(page, '/admin/connections');
    await expect(btn).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 60_000 });
  await btn.click();
  await expect(page.locator(`[data-ops-apply="${app}"]`)).toHaveCount(0, { timeout: 120_000 });
}

/** Broker response to a request made from inside an app's network (disposable container). */
function probeFrom(network: string, token: string, conn: string): string {
  return docker('run', '--rm', '--name', `hub-e2e-probe-${Date.now()}`, '--network', network, '--entrypoint', 'sh', 'postgres:16-alpine', '-c',
    `wget -S -q -O /dev/null --header "Authorization: Bearer ${token}" http://${CP}web:8081/_hub/storage/${conn}/list 2>&1 | awk '/HTTP\\//{print $2; exit}' || true`);
}

/** App's broker token: read from the container's environment (the API does not reveal secrets). */
async function reveal(_api: APIRequestContext, app: string): Promise<string> {
  return docker("exec", `${CP}app-${app}`, "printenv", "HUB_STORAGE_TOKEN").trim();
}

test.describe('Onda 10 — Conexões (SharePoint pelo simulador)', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(300_000);

  test('fornecedores: anexos pelo SharePoint quando fornecedores-anexos está vinculada (e de volta ao S3)', async ({ browser }) => {
    const api = await apiAs('admin');
    test.skip(!(await simEnabled(api)), 'compose sem o perfil sharepoint-sim');
    await removeIfExists(api, 'fornecedores-anexos');
    const ctx = await newRoleContext(browser, 'admin');
    const page = await ctx.newPage();
    autoConfirm(page);
    await createViaUi(page, 'fornecedores-anexos', 'Hub/Fornecedores');
    await bindViaUi(page, 'fornecedores-anexos', 'Fornecedores · Produção');
    await applyViaUi(page, 'fornecedores');
    try {
      await openApp(page, '/apps/fornecedores/indicators?id=FOR-0284');
      const att = page.locator('#attachments');
      await expect(att).toContainText('Guardados no SharePoint (conexão fornecedores-anexos)');
      const file = path.join(WORK_DIR, 'contrato-social-e2e.pdf');
      const bytes = Buffer.concat([Buffer.from('%PDF-1.4\n'), Buffer.alloc(120_000, 3)]);
      fs.writeFileSync(file, bytes);
      await att.locator('#f-anexo').setInputFiles(file);
      await att.getByRole('button', { name: 'Enviar anexo' }).click();
      await expect(page.locator('#attachments')).toContainText('Anexo enviado', { timeout: 60_000 });
      const [dl] = await Promise.all([
        page.waitForEvent('download'),
        page.locator('#attachments a[download]').first().click(),
      ]);
      expect(fs.readFileSync((await dl.path())!).equals(bytes)).toBe(true);
      await page.locator('#attachments').getByRole('button', { name: 'Excluir' }).first().click();
      await expect(page.locator('#attachments')).toContainText('Anexo excluído', { timeout: 60_000 });
    } finally {
      // Back to the app's object store (DR checks the attachments in the bucket).
      await api.put('/api/admin/connections/fornecedores-anexos/bindings', { data: { bindings: [] } });
      // Without a link the card disappears from the Connections screen: applies through the API (same button as Variables).
      const r = await api.post('/api/ops/runtime/fornecedores/apply', { data: {} });
      expect(r.status(), await r.text()).toBe(200);
      await ctx.close();
    }
    const p2 = await (await newRoleContext(browser, 'admin')).newPage();
    await openApp(p2, '/apps/fornecedores/indicators?id=FOR-0284');
    await expect(p2.locator('#attachments')).toContainText('Guardados no object store do app');
    await p2.context().close();
  });

  test('segurança: token só da rede do próprio app e só para conexões vinculadas; credencial fora de HTML/API', async () => {
    const api = await apiAs('admin');
    test.skip(!(await simEnabled(api)), 'compose sem o perfil sharepoint-sim');
    const mk = (name: string, bindings: object[]) => api.post("/api/admin/connections", {
      data: { kind: "sharepoint", name, tenant_id: SIM.tenant, client_id: SIM.client, auth: "secret", secret: SIM.secret, site: SIM.site, drive: SIM.drive, bindings },
    });
    await api.delete("/api/admin/connections/e2e-propria");
    await mk("e2e-propria", [{ app: "fornecedores", env: "producao" }]);
    try {
    const tok = await reveal(api, "fornecedores");
    // From the suppliers' network, with their token: the linked connection responds.
    expect(probeFrom(`${CP}app-fornecedores`, tok, "e2e-propria")).toBe("200");
    // The same token coming from another app's network: refused (origin).
    expect(probeFrom(`${CP}app-servicos`, tok, 'e2e-propria')).toBe('403');
    // No token / invalid token: 401.
    expect(probeFrom(`${CP}app-servicos`, 'x'.repeat(40), 'e2e-propria')).toBe('401');
    // Existing connection, but not linked to the app: 404 (without revealing that it exists).
    await api.post('/api/admin/connections', {
      data: { kind: 'sharepoint', name: 'e2e-alheia', tenant_id: SIM.tenant, client_id: SIM.client, auth: 'secret', secret: SIM.secret, site: SIM.site, drive: SIM.drive, bindings: [] },
    });
    try {
      expect(probeFrom(`${CP}app-fornecedores`, tok, 'e2e-alheia')).toBe('404');
    } finally {
      await api.delete('/api/admin/connections/e2e-alheia');
    }
    // The API and the page never return the credential.
    const list = await (await api.get('/api/admin/connections')).text();
    expect(list).not.toContain(SIM.secret);
    // Whoever does not have "Edit platform settings" does not see Connections.
    const leitor = await apiAs('leitor');
    expect((await leitor.get('/api/admin/connections')).status()).toBe(403);
    } finally {
      await api.delete('/api/admin/connections/e2e-propria');
    }
  });
});
