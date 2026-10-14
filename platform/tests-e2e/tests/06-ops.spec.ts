import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import { HUB, apiAs, gotoSafe, autoConfirm, docker, json, packSample, stateFile, waitJob, CP, confirmHub, openDeployVersion } from './helpers';

test.use({ storageState: stateFile('admin') });
test.describe.configure({ mode: 'serial' });

const APP = 'fornecedores';
// Unique version per run (versions are immutable in the hub).
const RUN = Math.floor(Date.now() / 1000) % 1_000_000;
const BASE = `1.8.${RUN}`;
const VERSION = `1.9.${RUN}`;
const toast = (page: Page) => page.locator('#toast');

async function deployments() {
  const api = await apiAs('admin');
  const list = await json(await api.get('/api/ops/deployments'));
  await api.dispose();
  return (Array.isArray(list) ? list : list.deployments || list.items) as any[];
}

test(`implantações: importar pacote → passos de validação → promover → aprovar → rollback (${APP})`, async ({ page }) => {
  test.setTimeout(420_000);
  autoConfirm(page);
  // Production baseline through the API (like a CI with a session): the rollback returns to it.
  // (In a new environment the previous version in the initial history points to a fake image.)
  const api = await apiAs('admin');
  const base = await api.post('/api/ops/packages?env=producao&wait=true', {
    headers: { 'x-hub-request': '1' },
    multipart: { package: { name: `${APP}-${BASE}.hubapp`, mimeType: 'application/zip', buffer: fs.readFileSync(packSample(APP, BASE)) } },
  });
  expect(base.status(), await base.text()).toBe(201);
  const baseDep = (await deployments()).find((d) => d.app === APP && d.version === BASE && d.env === 'Produção');
  const ok = await api.post(`/api/ops/deployments/${baseDep.id}/approve?wait=true`, { headers: { 'content-type': 'application/json' }, data: {} });
  expect(ok.status(), await ok.text()).toBe(200);
  // If the app already has a production database (e.g. after DR), staging requires its own database.
  const envs = await json(await api.get('/api/admin/env'));
  const hasProdDb = (Array.isArray(envs) ? envs : envs.items || envs.vars || []).some((v: any) => v.kind === 'connection_string' && (v.bindings || []).some((b: any) => b.app === APP && b.env === 'producao'));
  if (hasProdDb) {
    const pr = await api.post(`/api/ops/provision/${APP}/database?env=staging`, { headers: { 'content-type': 'application/json' }, data: {} });
    expect(pr.status(), await pr.text()).toBe(200);
  }
  await api.dispose();
  const pkg = packSample(APP, VERSION);
  await gotoSafe(page, '/admin/deploys?tab=import'); // import became a tab of the screen
  await page.setInputFiles('[data-ops-file]', pkg);
  await expect(page.locator('[data-ops-file-name]')).toContainText(`${APP}-${VERSION}.hubapp`);
  await page.selectOption('[data-ops-env]', 'staging');
  await page.click('[data-ops-import]');
  // The 7 pipeline steps are painted as the job advances.
  const steps = page.locator('[data-ops-steps] > li');
  await expect(steps).toHaveCount(7);
  await expect(steps.nth(0)).toHaveClass(/done/, { timeout: 60_000 });
  await expect(page.locator('[data-ops-pipeline-status]')).not.toContainText('Validando', { timeout: 180_000 });
  const stepClasses = await steps.evaluateAll((els) => els.map((e) => e.className));
  expect(stepClasses.filter((c) => /failed/.test(c))).toEqual([]);
  test.info().annotations.push({ type: 'pipeline', description: stepClasses.join(' · ') });
  await expect.poll(async () => (await deployments()).find((d) => d.app === APP && d.version === VERSION && d.env === 'Staging')?.status, { timeout: 180_000 }).toBe('Implantado');

  // Active staging → Promote (goes to production awaiting approval).
  let stg = (await deployments()).find((d) => d.app === APP && d.version === VERSION && d.env === 'Staging');
  expect(stg?.status).toBe('Implantado');
  const dr1 = await openDeployVersion(page, APP, stg.id);
  await dr1.locator(`[data-ops-promote="${stg.id}"]`).click();
  await expect.poll(async () => (await deployments()).find((d) => d.app === APP && d.version === VERSION && d.env === 'Produção')?.status, { timeout: 120_000 }).toMatch(/Aguardando/);
  const prod = (await deployments()).find((d) => d.app === APP && d.version === VERSION && d.env === 'Produção');
  // Approve → blue/green in production.
  const dr2 = await openDeployVersion(page, APP, prod.id);
  await dr2.locator(`[data-ops-approve="${prod.id}"]`).click();
  await expect.poll(async () => (await deployments()).find((d) => d.id === prod.id)?.status, { timeout: 180_000 }).toBe('Implantado');
  // Production serving the new version (image/version label on the container).
  const label = docker('inspect', '-f', '{{index .Config.Labels "hub.version"}}', `${CP}app-${APP}`);
  test.info().annotations.push({ type: 'produção', description: `hub-app-${APP} hub.version=${label}` });
  expect((await page.request.get(`/_frag/${APP}/dashboard`)).status()).toBe(200);

  // Rollback → previous version in production.
  const dr3 = await openDeployVersion(page, APP, prod.id);
  const tRb = Date.now() - 5000;
  const rb = dr3.locator(`[data-ops-rollback="${prod.id}"]`);
  await expect(rb).toBeVisible({ timeout: 30_000 });
  await rb.click();
  // The hub's confirmation modal is only handled by autoConfirm on actions with a locator; after the click there is only `poll`.
  await confirmHub(page);
  await expect.poll(async () => {
    const d = (await deployments()).find((x) => x.app === APP && x.env === 'Produção' && x.origin === 'Rollback' && x.at >= tRb);
    return d ? `${d.status} ${d.version}` : null;
  }, { timeout: 180_000 }).toBe(`Implantado ${BASE}`);
  test.info().annotations.push({ type: 'rollback', description: `${VERSION} → ${BASE}: ${(await deployments()).find((x) => x.app === APP && x.origin === 'Rollback' && x.at >= tRb)?.runtime}` });
  expect((await page.request.get(`/_frag/${APP}/dashboard`)).status()).toBe(200);
});

test('containers: estado real do engine (parar → iniciar)', async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto('/admin/runtime');
  for (const app of ['ativos', 'fornecedores', 'servicos']) {
    const card = page.locator(`[data-ops-app="${app}"][data-ops-env="producao"]`);
    await expect(card).toContainText(`${CP}app-${app}`);
    await expect(card).toContainText('Em execução');
    expect(docker('inspect', '-f', '{{.State.Running}}', `${CP}app-${app}`)).toBe('true');
  }
  const card = page.locator('[data-ops-app="servicos"][data-ops-env="producao"]');
  await card.locator('details.hub-row-menu > summary').click();
  await card.locator('[data-ops-container="stop"]').click();
  await expect(toast(page)).toContainText('parado', { timeout: 60_000 });
  const stopped = page.locator('[data-ops-app="servicos"][data-ops-env="producao"]');
  await expect(stopped).not.toContainText('Em execução');
  expect(docker('inspect', '-f', '{{.State.Running}}', `${CP}app-servicos`)).toBe('false');
  await stopped.locator('details.hub-row-menu > summary').click();
  await stopped.locator('[data-ops-container="start"]').click();
  await expect(toast(page)).toContainText('iniciado', { timeout: 60_000 });
  await expect(page.locator('[data-ops-app="servicos"][data-ops-env="producao"]')).toContainText('Em execução');
  expect(docker('inspect', '-f', '{{.State.Running}}', `${CP}app-servicos`)).toBe('true');
});

test('backups: backup agora + restaurar com faixa de manutenção', async ({ page, browser }) => {
  test.setTimeout(420_000);
  autoConfirm(page);
  await page.goto('/admin/backups');
  const api = await apiAs('admin');
  const before = Math.max(0, ...(await json(await api.get('/api/ops/backups'))).history.map((b: any) => b.id));
  await page.locator(`details.hub-row-menu:has([data-ops-backup="${APP}"]:not([data-env])) > summary`).click();
  await page.click(`[data-ops-backup="${APP}"]:not([data-env])`);
  await expect(page.locator('[data-ops-progress]')).toBeVisible();
  const newest = async () => (await json(await api.get('/api/ops/backups'))).history.find((b: any) => b.id > before && b.app === APP);
  await expect.poll(async () => (await newest())?.status, { timeout: 240_000 }).toBe('Concluído');
  const rec = await newest();
  test.info().annotations.push({ type: 'backup', description: `${rec.detail} · ${(rec.size / 1048576).toFixed(1)} MiB em ${(rec.duration_ms / 1000).toFixed(1)} s` });

  // Restore: during the restore the "In maintenance" strip appears (another tab) and the API shows the state.
  await page.goto('/admin/backups');
  const other = await (await browser.newContext({ storageState: stateFile('admin') })).newPage();
  let sawBanner = false;
  let sawApi = false;
  let stop = false;
  // Two observers in parallel: the runtime API (every 100 ms) and the Backups screen (another tab).
  const apiWatch = (async () => {
    while (!stop && !sawApi) {
      const rt = await json(await api.get('/api/ops/runtime'));
      if ((rt.apps || []).some((a: any) => a.id === APP && a.maintenance)) sawApi = true;
      await new Promise((r) => setTimeout(r, 100));
    }
  })();
  const pageWatch = (async () => {
    while (!stop && !sawBanner) {
      await other.goto('/admin/backups').catch(() => {});
      if (await other.locator(`[data-hub-maintenance="${APP}"]`).count()) {
        sawBanner = true;
        await other.screenshot({ path: test.info().outputPath('manutencao.png') });
      }
    }
  })();
  const started = page.waitForResponse((r) => r.url().includes(`/api/ops/backups/records/${rec.id}/restore`));
  await page.locator(`details.hub-row-menu:has([data-ops-restore="restore"][data-id="${rec.id}"]) > summary`).click();
  await page.click(`[data-ops-restore="restore"][data-id="${rec.id}"]`);
  await confirmHub(page);
  const job = await (await started).json();
  const final = await waitJob(api, job.id, 300_000);
  stop = true;
  await Promise.all([apiWatch, pageWatch]);
  expect(final.ok, final.message).toBe(true);
  // The job itself records the maintenance step (restoring a small bucket takes ~1 s).
  const jobMaint = JSON.stringify(final.steps || final).includes('manuten');
  expect(sawApi || sawBanner || jobMaint, 'estado de manutenção durante a restauração').toBe(true);
  test.info().annotations.push({ type: 'manutenção', description: `faixa na tela: ${sawBanner} · API: ${sawApi} · passo no job: ${jobMaint}` });
  await expect.poll(async () => (await page.request.get(`/_frag/${APP}/dashboard`)).status(), { timeout: 120_000 }).toBe(200);
  const after = await json(await api.get('/api/ops/runtime'));
  expect((after.apps || []).filter((a: any) => a.id === APP && a.maintenance)).toEqual([]);
  await other.context().close();
  await api.dispose();
});

test('tarefas: cancelar e reprocessar uma exportação em andamento', async ({ page }) => {
  test.setTimeout(300_000);
  // XLSX export of the whole database (long durable task) triggered by the app.
  const r = await page.request.post('/_frag/ativos/itens/export.xlsx', { form: { sort: 'nome', dir: 'asc' }, headers: { origin: HUB } });
  expect(r.status(), await r.text()).toBeLessThan(300);
  const api = await apiAs('admin');
  const findTask = async (states: string[]) => {
    const t = await json(await api.get('/api/ops/tasks'));
    const all = (t.tasks || t.items || t) as any[];
    return all.find((x) => x.app === 'ativos' && String(x.kind).includes('export') && states.includes(x.status));
  };
  await expect.poll(async () => Boolean(await findTask(['running', 'queued'])), { timeout: 60_000 }).toBe(true);
  const task = await findTask(['running', 'queued']);
  // The screen fetches the tasks from the app over the internal network; under load (export of 1 M rows)
  // the query may come back empty once — reloads until the row appears.
  const openRow = async (sel: string) => {
    await expect(async () => {
      await gotoSafe(page, '/admin/tasks?app=ativos');
      await expect(page.locator(sel).first()).toBeVisible({ timeout: 3000 });
    }).toPass({ timeout: 90_000 });
    await page.locator(sel).first().click();
  };
  await openRow(`tr[data-task="${task.id}"] [data-ops-task*="|cancel"]`);
  await expect(toast(page)).toContainText('Cancelamento solicitado');
  await expect.poll(async () => (await findTask(['cancelled']))?.id, { timeout: 120_000 }).toBe(task.id);
  await openRow(`tr[data-task="${task.id}"] [data-ops-task*="|retry"]`);
  await expect(toast(page)).toContainText('Tarefa devolvida à fila');
  await expect.poll(async () => Boolean(await findTask(['queued', 'running', 'done'])), { timeout: 60_000 }).toBe(true);
  // Does not leave the export of 1 million rows running after the test.
  const again = await findTask(['queued', 'running']);
  if (again) await api.post(`/api/ops/tasks/ativos/${again.id}/cancel`, { headers: { 'content-type': 'application/json' }, data: {} });
  await api.dispose();
});
