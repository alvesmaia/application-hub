import { test, expect, type APIRequestContext } from '@playwright/test';
import { apiAs, autoConfirm, docker, gotoSafe, json, newRoleContext, USERS, CP, DATA_NETWORK, OBJSTORE_PASS } from './helpers';

// Onda 12 (2.7) — Aplicações › Conexões → "Provisionar armazenamento": numa ação, o
// bucket do app no MinIO da plataforma, um usuário do MinIO com política restrita ao
// bucket e uma conexão S3 do broker ligada ao app × ambiente (o app nunca recebe a
// chave). Idempotente, auditado; desprovisionar exige digitar o bucket e, com objetos,
// "force" + a senha (reautenticação). Também pela API v1 (operações storage_*).

const MC = process.env.HUB_MC_IMAGE || 'hub-mc:RELEASE.2025-08-13T08-35-41Z';
const ROOT = { user: 'hubadmin', pass: OBJSTORE_PASS };

/** `mc` com a credencial raiz, na rede hub-data (só para conferir de fora). */
function mcRoot(script: string): string {
  return docker('run', '--rm', '--name', `hub-e2e-mc-${Date.now()}`, '--label', 'hub.ci=1', '--network', DATA_NETWORK,
    '--entrypoint', 'sh', MC, '-c',
    `mc alias set r http://${CP}objstore:9000 ${ROOT.user} ${ROOT.pass} >/dev/null && ${script}`);
}

async function available(api: APIRequestContext): Promise<boolean> {
  const v = await json(await api.get('/api/admin/connections/storage'));
  return !!(v && v.available);
}

async function cleanup(api: APIRequestContext, app: string, env: string, bucket: string) {
  await api.post(`/api/admin/connections/storage/${app}/deprovision?env=${env}`, {
    data: { confirm: bucket, force: true, password: USERS.admin.password },
  });
}

test.describe('Onda 12 — Provisionar armazenamento (2.7)', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(240_000);

  test('pela tela: provisiona (bucket + usuário restrito + conexão), é idempotente, audita e desprovisiona com confirmação e senha', async ({ browser }) => {
    const api = await apiAs('admin');
    test.skip(!(await available(api)), 'provisionamento indisponível (compose sem runtime/objstore)');
    await cleanup(api, 'servicos', 'producao', 'app-servicos');
    const ctx = await newRoleContext(browser, 'admin');
    const page = await ctx.newPage();
    autoConfirm(page);
    await gotoSafe(page, '/admin/connections');
    const form = page.locator('[data-conn-storage-form]');
    await form.locator('[data-conn-storage-app]').selectOption('servicos');
    await form.locator('[data-conn-storage-env]').selectOption('producao');
    await form.getByRole('button', { name: 'Provisionar armazenamento' }).click();
    const row = page.locator('tr[data-conn-storage-row="servicos|producao"]');
    await expect(row).toBeVisible({ timeout: 90_000 });
    await expect(row).toContainText('app-servicos');
    // Conexão S3 criada e ligada ao app (sem credencial na página).
    const conn = page.locator('tr[data-conn-row="app-servicos"]');
    await expect(conn).toBeVisible();
    await expect(conn).toContainText('S3 / MinIO');
    await expect(conn).toContainText('app-servicos · ••••••••');
    // "Testar conexão": o hub alcança o bucket com a chave restrita.
    await conn.getByRole('button', { name: 'Testar conexão' }).click();
    await expect(page.locator('[data-conn-result="app-servicos"]')).toContainText('Conexão OK', { timeout: 60_000 });

    // No MinIO: bucket existe e o usuário tem só a política do próprio bucket.
    expect(mcRoot('mc stat r/app-servicos >/dev/null && echo BUCKET-OK')).toContain('BUCKET-OK');
    const info = mcRoot('mc admin user info r app-servicos');
    expect(info).toContain('hub-app-servicos');
    const pol = mcRoot('mc admin policy info r hub-app-servicos');
    expect(pol).toContain('arn:aws:s3:::app-servicos/*');
    expect(pol).not.toContain('arn:aws:s3:::*"');

    // Idempotente: repetir confere tudo e não muda nada.
    const again = await api.post('/api/admin/connections/storage/servicos?env=producao');
    expect(again.status()).toBe(200);
    expect((await json(again)).changed).toBe(false);

    // Auditoria sem credenciais.
    const audit = JSON.stringify(await json(await api.get('/api/admin/audit?scope=servicos')));
    expect(audit).toContain('Armazenamento provisionado');

    // Desprovisionar com objetos: confirmação errada, sem force, senha errada → recusado.
    mcRoot('echo x > /tmp/o && mc cp /tmp/o r/app-servicos/anexo.txt >/dev/null && echo PUT-OK');
    const bad = async (body: Record<string, unknown>, msg: RegExp) => {
      const r = await api.post('/api/admin/connections/storage/servicos/deprovision?env=producao', { data: body });
      expect(r.status()).toBeGreaterThanOrEqual(400);
      expect(JSON.stringify(await json(r))).toMatch(msg);
    };
    await bad({ confirm: 'outro' }, /Confirme digitando o nome do bucket/);
    await bad({ confirm: 'app-servicos' }, /tem objetos/);
    await bad({ confirm: 'app-servicos', force: true, password: 'errada' }, /Senha incorreta/);
    expect(mcRoot('mc stat r/app-servicos >/dev/null && echo STILL')).toContain('STILL');

    // Pela tela, com force + senha: remove conexão, usuários e bucket.
    await gotoSafe(page, '/admin/connections');
    await page.locator('tr[data-conn-storage-row="servicos|producao"]').getByRole('button', { name: 'Desprovisionar' }).click();
    const dlg = page.getByRole('dialog');
    await dlg.getByLabel('Digite o nome do bucket para confirmar').fill('app-servicos');
    await dlg.getByLabel('Apagar também os objetos do bucket (irreversível)').check();
    await dlg.getByLabel('Sua senha (obrigatória para apagar objetos)').fill(USERS.admin.password);
    await dlg.getByRole('button', { name: 'Desprovisionar' }).click();
    await expect(page.locator('tr[data-conn-storage-row="servicos|producao"]')).toHaveCount(0, { timeout: 90_000 });
    await expect(page.locator('tr[data-conn-row="app-servicos"]')).toHaveCount(0);
    expect(mcRoot('mc stat r/app-servicos >/dev/null 2>&1 && echo EXISTS || echo GONE')).toContain('GONE');
    expect(mcRoot('mc admin user info r app-servicos >/dev/null 2>&1 && echo USER || echo NOUSER')).toContain('NOUSER');
    const audit2 = JSON.stringify(await json(await api.get('/api/admin/audit?scope=servicos')));
    expect(audit2).toContain('Armazenamento desprovisionado');
    await ctx.close();
  });

  test('pela API v1: storage_provision/storage_list/storage_deprovision (bucket vazio só com a confirmação)', async () => {
    const api = await apiAs('admin');
    test.skip(!(await available(api)), 'provisionamento indisponível (compose sem runtime/objstore)');
    await cleanup(api, 'ativos', 'staging', 'stg-ativos');
    const doc = await json(await api.get('/api/v1/openapi.json'));
    expect(JSON.stringify(doc)).toContain('storage_deprovision');
    const r = await api.post('/api/v1/apps/ativos/storage?env=staging');
    expect(r.status(), JSON.stringify(await json(r))).toBe(201);
    const list = await json(await api.get('/api/v1/storage?app=ativos'));
    expect(JSON.stringify(list)).toContain('stg-ativos');
    const conns = JSON.stringify(await json(await api.get('/api/v1/connections')));
    expect(conns).toContain('stg-ativos');
    expect(conns).not.toMatch(/"secret"\s*:\s*"[0-9a-f]{16,}/);
    // Vazio: basta a confirmação (sem senha).
    const d = await api.post('/api/v1/apps/ativos/storage/deprovision?env=staging', { data: { confirm: 'stg-ativos' } });
    expect(d.status(), JSON.stringify(await json(d))).toBe(200);
    expect(mcRoot('mc stat r/stg-ativos >/dev/null 2>&1 && echo EXISTS || echo GONE')).toContain('GONE');
  });
});
