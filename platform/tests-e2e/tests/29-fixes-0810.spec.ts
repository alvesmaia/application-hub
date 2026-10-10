import { test, expect } from '@playwright/test';
import { HUB, apiAs, json, stateFile, uniq } from './helpers';

test.use({ storageState: stateFile('admin') });

test('credenciais: Revogar espera o clique; excluir vale também para token ativo @core', async ({ page }) => {
  const api = await apiAs('admin');
  const name = `fx-${uniq()}`;
  const made = await json(await api.post('/api/admin/tokens', { data: { name, apps: [], envs: ['staging'], actions: ['status'], expires_days: 30 }, headers: { 'x-hub-request': '1' } }));
  const id = made.item.id as string;
  const row = page.locator(`tr[data-token-row="${id}"]`);
  await page.goto('/admin/tokens');
  await row.click();
  await page.locator('[data-rowdrawer] [data-token-revoke]').click();
  // O diálogo fica aberto até o clique do usuário; o token segue ativo.
  await page.waitForTimeout(1200);
  await expect(page.locator('.hub-confirm')).toBeVisible();
  const still = (await json(await api.get('/api/admin/tokens'))) as any;
  expect((still.items || still).find((t: any) => t.id === id).active).toBe(true);
  await page.locator('[data-hub-confirm="no"]').click();
  // Excluir (token ativo): confirma e a linha some.
  await page.locator('[data-rowdrawer] [data-token-delete]').click();
  await page.locator('[data-hub-confirm="yes"]').click();
  await expect(row).toHaveCount(0);
  await api.dispose();
});

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
