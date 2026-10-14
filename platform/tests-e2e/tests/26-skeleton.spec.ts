import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { stateFile } from './helpers';

// Component skeleton after the loader (the mockup's layout, branch main:
// componentSkeletonView): header; 3 metrics in a row; chart + list side by side
// (stacked on the phone); table at full width. Regression: on the first opening the
// loader container (flex in a row) put all the blocks side by side.
test.use({ storageState: stateFile('admin') });

const DIST = path.join(__dirname, '..', '..', 'dist');

async function skeleton(page: Page) {
  // Holds the fragment: the skeleton stays on screen after the loader.
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
  // metrics in a single row, below the header
  expect(new Set(s.metrics.map((m) => Math.round(m.y))).size).toBe(1);
  expect(s.metrics[0].y).toBeGreaterThan(s.head.y + s.head.height - 1);
  expect(s.metrics[1].x).toBeGreaterThan(s.metrics[0].x + s.metrics[0].width - 1);
  // chart + list side by side, below the metrics
  expect(s.row.y).toBeGreaterThan(s.metrics[0].y + s.metrics[0].height - 1);
  expect(Math.abs(s.chart.y - s.list.y)).toBeLessThan(2);
  expect(s.list.x).toBeGreaterThan(s.chart.x + s.chart.width - 1);
  // table below, at full width (same width as the panel row)
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
  // The click and the DOM read happen in the same evaluation (the held navigation does not block).
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
