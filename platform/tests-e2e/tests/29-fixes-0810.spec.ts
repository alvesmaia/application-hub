import { test, expect } from '@playwright/test';
import { HUB, apiAs, json, stateFile, uniq } from './helpers';

test.use({ storageState: stateFile('admin') });

test('implantações: uma linha por app; a gaveta escolhe a versão @core', async ({ page }) => {
  await page.goto('/admin/deploys');
  const rows = page.locator('tr[data-detail-row]');
  const n = await rows.count();
  expect(n).toBeGreaterThan(0);
  const apps = await rows.evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.app));
  expect(new Set(apps).size).toBe(n);
  await rows.first().click();
  const sel = page.locator('[data-rowdrawer] select[data-ops-ver]');
  await expect(sel).toBeVisible();
  const opts = await sel.locator('option').count();
  if (opts > 1) {
    const v = await sel.locator('option').nth(1).getAttribute('value');
    await sel.selectOption(v!);
    await expect(page.locator(`[data-rowdrawer] [data-ops-ver-panel="${v}"]`)).toBeVisible();
  }
});

test('auditoria: filtro por aplicação é lista suspensa; variáveis: segredo nunca é revelado @core', async ({ page }) => {
  await page.goto('/admin/audit');
  await expect(page.locator('select[name="scope"]')).toBeVisible();
  await expect(page.locator('.filter-chips')).toHaveCount(0);
  await page.locator('select[name="scope"]').selectOption('platform');
  await expect(page).toHaveURL(/scope=platform/);
  await page.goto('/admin/env');
  await expect(page.locator('[data-env-reveal]')).toHaveCount(0);
  const api = await apiAs('admin');
  const r = await api.post('/api/admin/env/AUTH_CLIENT_ID/reveal', { data: {}, headers: { origin: HUB } });
  expect([403, 404, 405]).toContain(r.status());
  await api.dispose();
});
