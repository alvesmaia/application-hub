import { test, expect } from '@playwright/test';
import { stateFile, uniq } from './helpers';

// Administration › Users › Database access: a read account for a dashboard (responsible person, databases,
// validity); the password shows once; a new password and the removal ask for confirmation.

test.use({ storageState: stateFile('admin') });
const NAME = `e2e_${uniq().toLowerCase()}`;

test('conta de leitura do banco: criar, ver a senha uma vez, nova senha e remover @core', async ({ page }) => {
  await page.goto('/admin/users');
  await page.locator('nav.hub-tabbar, .hub-tabbar').getByRole('link', { name: 'Acesso ao banco' }).click();
  await expect(page).toHaveURL(/\/admin\/users\?tab=db$/);
  await expect(page.locator('[data-dbaccess-status]')).toBeVisible();

  await page.locator('[data-dbaccess-new]').click();
  const form = page.locator('.overlay[data-users-modal] form');
  await form.locator('input[name="name"]').fill(NAME);
  await form.locator('select[name="owner"]').selectOption({ index: 0 });
  await form.locator('input[name="dbs"][value="inventory"]').check();
  await form.locator('button[type="submit"]').click();

  const creds = page.locator('.overlay[data-users-modal] form');
  await expect(creds).toContainText('Credenciais da conta de leitura');
  const password = await page.locator('#dbaccess-password').inputValue();
  expect(password.length).toBeGreaterThanOrEqual(24);
  await creds.locator('button[type="submit"]').click();

  const row = page.locator(`tr[data-dbaccess-row="ro_${NAME}"]`);
  await expect(row).toContainText('inventory');
  await expect(row).toContainText('Ativa');
  await expect(page.locator('body')).not.toContainText(password); // shown only once

  await row.locator('summary.row-action').click();
  await row.locator(`[data-dbaccess-password="ro_${NAME}"]`).click();
  await page.locator('[data-hub-confirm="yes"]').click();
  const again = await page.locator('#dbaccess-password').inputValue();
  expect(again).not.toBe(password);
  await page.locator('.overlay[data-users-modal] form button[type="submit"]').click();

  await page.locator(`tr[data-dbaccess-row="ro_${NAME}"] summary.row-action`).click();
  await page.locator(`[data-dbaccess-remove="ro_${NAME}"]`).click();
  await page.locator('[data-hub-confirm="yes"]').click();
  await expect(page.locator(`tr[data-dbaccess-row="ro_${NAME}"]`)).toHaveCount(0);
});
