import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { apiAs, gotoSafe, json, packSample, stateFile, WORK_DIR } from './helpers';

// Task 29: manual import = choose the .hubapp → preview → variables/secrets → environment → deploy.
test.use({ storageState: stateFile('admin') });

const APP = 'fornecedores';
const RUN = Math.floor(Date.now() / 1000) % 1_000_000;
const VERSION = `1.7.${RUN}`;
const SECRET = `e2e-segredo-${RUN}-nao-pode-aparecer`;
const VAR_VALUE = `http://e2e-${RUN}.local`;

async function stagingStatus(version: string) {
  const api = await apiAs('admin');
  const list = (await json(await api.get('/api/ops/deployments'))) as any[];
  await api.dispose();
  return list.find((d) => d.app === APP && d.version === version && d.env === 'Staging')?.status;
}

async function ensureStagingDb() {
  // If the app already has a production database (e.g. after DR), staging requires its own (same as 06-ops).
  const api = await apiAs('admin');
  const envs = await json(await api.get('/api/admin/env'));
  const list = Array.isArray(envs) ? envs : envs.items || envs.vars || [];
  if (list.some((v: any) => v.kind === 'connection_string' && (v.bindings || []).some((b: any) => b.app === APP && b.env === 'producao'))) {
    const pr = await api.post(`/api/ops/provision/${APP}/database?env=staging`, { headers: { 'content-type': 'application/json' }, data: {} });
    expect(pr.status(), await pr.text()).toBe(200);
  }
  await api.dispose();
}

test('importar pacote: arquivo inválido mostra o erro e não habilita o botão', async ({ page }) => {
  fs.mkdirSync(WORK_DIR, { recursive: true });
  const bad = path.join(WORK_DIR, `invalido-${RUN}.hubapp`);
  fs.writeFileSync(bad, 'nao-e-zip');
  await gotoSafe(page, '/admin/deploys?tab=import');
  await page.setInputFiles('[data-ops-file]', bad);
  await expect(page.locator('[data-ops-preview-error]')).toContainText('Pacote inválido');
  await expect(page.locator('[data-ops-preview]')).toBeHidden();
  await expect(page.locator('[data-ops-import]')).toBeDisabled();
  await expect(page.locator('#p-import [data-ops-manifest]')).toHaveCount(0); // the validator lives in "Pipeline e CI"
});

test(`importar pacote: prévia → variável e segredo → botão por ambiente → staging (${APP})`, async ({ page }) => {
  test.setTimeout(360_000);
  await ensureStagingDb();
  const pkg = packSample(APP, VERSION, { requires: { variables: ['E2E_IMPORT_URL'], secrets: ['E2E_IMPORT_KEY'] } });
  await gotoSafe(page, '/admin/deploys?tab=import');
  const button = page.locator('[data-ops-import]');
  await expect(button).toBeDisabled();
  await page.setInputFiles('[data-ops-file]', pkg);

  const pv = page.locator('[data-ops-preview]');
  await expect(pv).toBeVisible();
  await expect(pv.locator('[data-pv="app"]')).toContainText(`(${APP})`);
  await expect(pv.locator('[data-pv="version"]')).toHaveText(VERSION);
  const varInput = page.locator('#ops-cfg-variables-E2E_IMPORT_URL');
  const secretInput = page.locator('#ops-cfg-secrets-E2E_IMPORT_KEY');
  await expect(varInput).toHaveAttribute('type', 'text');
  await expect(secretInput).toHaveAttribute('type', 'password');

  await page.selectOption('[data-ops-env]', 'producao');
  await expect(button).toHaveText('Enviar para aprovação (produção)');
  await page.selectOption('[data-ops-env]', 'staging');
  await expect(button).toHaveText('Implantar em staging');
  await expect(pv.locator('[data-pv="state"]')).toContainText(/Atualiza de .+ para|Primeira versão neste ambiente/);
  await expect(button).toBeEnabled();

  await varInput.fill(VAR_VALUE);
  await secretInput.fill(SECRET);
  await button.click();
  await expect(secretInput).toHaveValue(''); // cleared as soon as it was read (sent once, in the upload's `config`)
  await expect.poll(() => stagingStatus(VERSION), { timeout: 240_000 }).toBe('Implantado');

  await gotoSafe(page, '/admin/deploys?tab=import');
  expect(await page.content()).not.toContain(SECRET);
  const api = await apiAs('admin');
  const env = await (await api.get('/api/admin/env')).text();
  const jobs = await (await api.get('/api/ops/deployments')).text();
  await api.dispose();
  expect(env).toContain('E2E_IMPORT_KEY_FORNECEDORES_STAGING'); // the secret arrived (by name)
  expect(env).toContain(VAR_VALUE); // the public variable arrived with its value
  expect(env).not.toContain(SECRET);
  expect(jobs).not.toContain(SECRET);
});
