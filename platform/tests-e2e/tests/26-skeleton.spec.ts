import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { stateFile } from './helpers';

// Skeleton de componentes depois do carregador (layout do mockup, branch main:
// componentSkeletonView): cabeçalho; 3 métricas numa linha; gráfico + lista lado a lado
// (empilhados no celular); tabela na largura toda. Regressão: na primeira abertura o
// contêiner do carregador (flex em linha) punha todos os blocos lado a lado.
test.use({ storageState: stateFile('admin') });

const DIST = path.join(__dirname, '..', '..', 'dist');

async function skeleton(page: Page) {
  // Segura o fragmento: o skeleton fica na tela depois do carregador.
  await page.route('**/_frag/ativos/**', async (r) => { await new Promise((x) => setTimeout(x, 5000)); await r.continue().catch(() => undefined); });
  await page.goto('/apps/ativos');
  const box = page.locator('#app-root .component-loading');
  await expect(box).toBeVisible({ timeout: 30_000 });
  await expect(box.locator('.skeleton-table, .table-skeleton')).toBeVisible();
  const r = async (sel: string) => (await box.locator(sel).first().boundingBox())!;
  const all = async (sel: string) => Promise.all((await box.locator(sel).all()).map(async (l) => (await l.boundingBox())!));
  return { box: (await box.boundingBox())!, head: await r('.skeleton-page-heading'), metrics: await all('.metric-skeleton'), row: await r('.skeleton-panel-row'), chart: await r('.chart-skeleton'), list: await r('.list-skeleton'), table: await r('.table-skeleton') };
}

test('skeleton depois do carregador no layout do mockup (computador) @core', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const s = await skeleton(page);
  // métricas numa linha só, abaixo do cabeçalho
  expect(new Set(s.metrics.map((m) => Math.round(m.y))).size).toBe(1);
  expect(s.metrics[0].y).toBeGreaterThan(s.head.y + s.head.height - 1);
  expect(s.metrics[1].x).toBeGreaterThan(s.metrics[0].x + s.metrics[0].width - 1);
  // gráfico + lista lado a lado, abaixo das métricas
  expect(s.row.y).toBeGreaterThan(s.metrics[0].y + s.metrics[0].height - 1);
  expect(Math.abs(s.chart.y - s.list.y)).toBeLessThan(2);
  expect(s.list.x).toBeGreaterThan(s.chart.x + s.chart.width - 1);
  // tabela embaixo, na largura toda (mesma largura da linha de painéis)
  expect(s.table.y).toBeGreaterThan(s.row.y + s.row.height - 1);
  expect(Math.abs(s.table.width - s.row.width)).toBeLessThan(2);
  expect(s.table.width).toBeGreaterThan(s.box.width * 0.6);
  fs.mkdirSync(DIST, { recursive: true });
  await page.screenshot({ path: path.join(DIST, 'skeleton-desktop.png') });
});

test('skeleton depois do carregador no celular: blocos empilhados, sem estouro @core', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const s = await skeleton(page);
  expect(new Set(s.metrics.map((m) => Math.round(m.y))).size).toBe(1);
  expect(s.metrics[0].y).toBeGreaterThan(s.head.y + s.head.height - 1);
  expect(s.chart.y).toBeGreaterThan(s.metrics[0].y + s.metrics[0].height - 1);
  expect(s.table.y).toBeGreaterThan(s.chart.y + s.chart.height - 1);
  for (const b of [s.head, s.row, s.table, ...s.metrics]) expect(b.x + b.width).toBeLessThanOrEqual(391);
  const over = await page.evaluate(() => [...document.querySelectorAll('.chart-skeleton .skeleton-chart-bars')].some((e) => e.getBoundingClientRect().bottom > e.closest('.chart-skeleton')!.getBoundingClientRect().bottom + 1));
  expect(over).toBe(false);
  await page.screenshot({ path: path.join(DIST, 'skeleton-mobile.png') });
});

test('páginas do hub: skeleton ao navegar (sem salto de largura) e paginador (Auditoria) @core', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/admin/runtime');
  const w = (await page.locator('#app-root .page-container').boundingBox())!.width;
  await page.route('**/admin/deploys', async (r) => { await new Promise((x) => setTimeout(x, 2500)); await r.continue().catch(() => undefined); });
  // O clique e a leitura do DOM acontecem na mesma avaliação (a navegação segurada não bloqueia).
  const sk = await page.evaluate(async () => {
    (document.querySelector('a.rail-subitem[href="/admin/deploys"]') as HTMLElement).click();
    await new Promise((r) => setTimeout(r, 150));
    const e = document.querySelector('#app-root .hub-page-skeleton');
    return e ? { width: e.getBoundingClientRect().width, busy: e.getAttribute('aria-busy') } : null;
  });
  expect(sk).not.toBeNull();
  expect(Math.abs(sk!.width - w)).toBeLessThan(2);

  await expect(page).toHaveURL(/\/admin\/deploys/);
  await expect(page.locator('.deploys-table')).toBeVisible();
  const wrap = page.locator('.deploys-table').locator('xpath=..');
  expect(await wrap.evaluate((e) => getComputedStyle(e).overflowX)).toBe('auto');
  await page.goto('/admin/audit?per=10');
  await expect(page.locator('.hub-pager')).toBeVisible();
});
