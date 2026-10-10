import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import { bigXlsx, openApp, stateFile, uniq } from './helpers';

test.use({ storageState: stateFile('admin') });
test.describe.configure({ mode: 'serial' });

// Planilha "grande" do roteiro: 200 mil linhas (1% com erro) com prefixo único por execução.
const ROWS = Number(process.env.E2E_IMPORT_ROWS || 200_000);
const PREFIX = `E2E${uniq()}-`;

const rows = (page: Page) => page.locator('#app-root table.data-table tbody tr');

test('ativos: busca, ordenação e filtros no servidor @core', async ({ page }) => {
  await openApp(page, '/apps/ativos/assets');
  await expect(page.locator('.page-subtitle')).toContainText('ativos encontrados');
  // Busca
  await page.fill('input[name="q"]', 'MacBook');
  await page.click('.hub-filters button.filter-button');
  await expect(page).toHaveURL(/q=MacBook/);
  await expect.poll(async () => {
    const names = await rows(page).locator('td:first-child').allTextContents();
    return names.length > 0 && names.every((t) => t.includes('MacBook'));
  }).toBe(true);
  // Filtro por status (mantém a busca)
  await page.selectOption('select[name="status"]', 'Manutenção');
  await page.click('.hub-filters button.filter-button');
  await expect(page).toHaveURL(/status=Manuten/);
  await expect.poll(async () => {
    const st = (await rows(page).locator('td:nth-child(5)').allTextContents()).map((t) => t.trim());
    return st.length > 0 && st.every((t) => t === 'Manutenção');
  }).toBe(true);
  // Ordenação por valor (crescente e depois decrescente)
  await page.locator('a.hub-sort', { hasText: 'Valor' }).click();
  await expect(page).toHaveURL(/sort=valor&dir=asc/);
  const money = async () => (await rows(page).locator('td:nth-child(7)').allTextContents())
    .map((v) => Number(v.replace(/[^\d,]/g, '').replace(',', '.')));
  await expect.poll(async () => { const d = await money(); return d.length > 3 && d.every((v, i) => i === 0 || d[i - 1] <= v); }).toBe(true);
  await page.locator('a.hub-sort', { hasText: 'Valor' }).click();
  await expect(page).toHaveURL(/sort=valor&dir=desc/);
  await expect.poll(async () => { const d = await money(); return d.length > 3 && d.every((v, i) => i === 0 || d[i - 1] >= v) && d[0] > d[d.length - 1]; }).toBe(true);
  // Limpar volta à lista completa
  await page.click('.hub-filters a.hub-link');
  await expect(page).toHaveURL(/\/apps\/ativos\/assets$/);
});

test('ativos: mesmo ativo editado em dois contextos → 409 no segundo @core', async ({ browser }) => {
  const a = await browser.newContext({ storageState: stateFile('admin') });
  const b = await browser.newContext({ storageState: stateFile('operador') });
  const pa = await a.newPage();
  const pb = await b.newPage();
  const id = 22;
  await openApp(pa, `/apps/ativos/assets?edit=${id}`);
  await openApp(pb, `/apps/ativos/assets?edit=${id}`);
  const version = await pa.locator(`form[action="/apps/ativos/itens/${id}"] input[name="version"]`).inputValue();
  expect(await pb.locator(`form[action="/apps/ativos/itens/${id}"] input[name="version"]`).inputValue()).toBe(version);
  await pa.fill('#asset-editor input[name="name"]', `Notebook editado por A ${uniq()}`);
  const saveA = pa.waitForResponse((r) => r.url().includes(`/_frag/ativos/itens/${id}`) && r.request().method() === 'POST');
  await pa.locator(`form[action="/apps/ativos/itens/${id}"] button[type="submit"]`).first().click();
  expect((await saveA).status()).toBe(200);
  await pb.fill('#asset-editor input[name="name"]', 'Notebook editado por B');
  const saveB = pb.waitForResponse((r) => r.url().includes(`/_frag/ativos/itens/${id}`) && r.request().method() === 'POST');
  await pb.locator(`form[action="/apps/ativos/itens/${id}"] button[type="submit"]`).first().click();
  expect((await saveB).status()).toBe(409);
  await expect(pb.locator('#app-root')).toContainText('Este ativo foi alterado por outra pessoa');
  await expect(pb.locator('#app-root')).toContainText(`Você editou a versão ${version}`);
  await a.close();
  await b.close();
});

test(`ativos: importar planilha grande (${ROWS} linhas) com progresso e relatório de erros`, async ({ page }) => {
  test.setTimeout(600_000);
  const file = bigXlsx(ROWS, PREFIX);
  await openApp(page, '/apps/ativos/assets?import=1');
  await page.setInputFiles('#f-file', file);
  await page.locator('#import-panel button[type="submit"]').click();
  // Progresso (tarefa durável; o painel é consultado a cada 1 s)
  await expect(page.locator('#import-panel .hub-progress')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('#import-panel .eyebrow')).toHaveText('IMPORTAÇÃO CONCLUÍDA', { timeout: 540_000 });
  const report = page.locator('#import-panel a[href*="erros.csv"]');
  await expect(report).toBeVisible();
  const [dl] = await Promise.all([page.waitForEvent('download'), report.click()]);
  const csv = fs.readFileSync((await dl.path())!, 'utf8');
  const lines = csv.trim().split(/\r?\n/);
  expect(lines.length).toBeGreaterThan(10);
  test.info().annotations.push({ type: 'import', description: `${ROWS} linhas · ${lines.length - 1} erros no relatório` });
  // As válidas estão na lista
  await openApp(page, `/apps/ativos/assets?q=${encodeURIComponent(PREFIX)}`);
  await expect(page.locator('.page-subtitle')).not.toContainText(/^0 ativos/);
});

test('ativos: exportar CSV e XLSX do resultado filtrado', async ({ page }) => {
  test.setTimeout(300_000);
  await openApp(page, `/apps/ativos/assets?q=${encodeURIComponent(PREFIX)}&status=Dispon%C3%ADvel`);
  const csvLink = page.locator('a.secondary-button', { hasText: 'Exportar CSV' });
  await expect(csvLink).toHaveAttribute('href', /export\.csv\?.*q=/);
  const [csvDl] = await Promise.all([page.waitForEvent('download'), csvLink.click()]);
  const csv = fs.readFileSync((await csvDl.path())!, 'utf8');
  const csvLines = csv.trim().split(/\r?\n/);
  expect(csvLines.length).toBeGreaterThan(1);
  expect(csvLines.slice(1).every((l) => l.includes(PREFIX))).toBe(true);

  await page.locator('form[action="/apps/ativos/itens/export.xlsx"] button[type="submit"]').click();
  await expect(page.locator('#export-panel .eyebrow')).toHaveText('EXPORTAÇÃO CONCLUÍDA', { timeout: 240_000 });
  const [xlsxDl] = await Promise.all([page.waitForEvent('download'), page.locator('#export-panel a', { hasText: 'Baixar planilha' }).click()]);
  const buf = fs.readFileSync((await xlsxDl.path())!);
  expect(buf.subarray(0, 2).toString()).toBe('PK'); // zip (OOXML)
  expect(buf.length).toBeGreaterThan(5_000);
  test.info().annotations.push({ type: 'export', description: `CSV ${csvLines.length - 1} linhas · XLSX ${buf.length} bytes` });
});
