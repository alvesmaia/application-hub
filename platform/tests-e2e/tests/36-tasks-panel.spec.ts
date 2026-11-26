import { test, expect } from '@playwright/test';
import { HUB, apiAs, json, stateFile } from './helpers';

// Operations › Tasks: a running number instead of the job id; clicking a row opens the side panel with the
// task's details (the id included) and the app's log while it ran; Esc closes it.
test.use({ storageState: stateFile('admin') });

test('tarefas: clicar na linha abre o painel com detalhes e log @core', async ({ page }) => {
  const api = await apiAs('admin');
  const tasks = async () => { const t = await json(await api.get('/api/ops/tasks')); return (t.tasks || t.items || t) as { id: string }[]; };
  if (!(await tasks()).length) {
    await page.request.post('/_frag/inventory/items/export.xlsx', { form: { sort: 'nome', dir: 'asc' }, headers: { origin: HUB } });
    await expect.poll(async () => (await tasks()).length, { timeout: 60_000 }).toBeGreaterThan(0);
  }
  await page.goto('/admin/tasks');
  const row = page.locator('#tasks-table tbody tr[data-detail-row]').first();
  await expect(row.locator('.task-num')).toHaveText('#1');
  const id = (await row.getAttribute('data-task'))!;
  await expect(row.locator('td').first()).not.toContainText(id); // the id is in the panel, not in the column
  await row.locator('td').nth(2).click();
  const panel = page.locator('[data-rowdrawer]');
  await expect(panel).toBeVisible();
  await expect(panel.locator('[data-rowdrawer-title]')).toContainText('#1');
  await expect(panel).toContainText(id);
  await expect(panel.getByRole('heading', { name: 'Log' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await api.dispose();
});
