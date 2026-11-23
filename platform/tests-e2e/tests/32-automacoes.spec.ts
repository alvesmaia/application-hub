import { test, expect } from '@playwright/test';
import { stateFile } from './helpers';

test.use({ storageState: stateFile('admin') });

// App "Automações": the run form lives in a modal (data-hub-post); a valid submission lands on the run page
// (PRG), which must keep polling until the run is terminal — content inserted by the kit polls too.
test('Automações: formulário valida, abre a execução e ela se atualiza até concluir @core', async ({ page }) => {
  await page.goto('/apps/automacoes/catalogo');
  // server-side validation: the 422 answer replaces the form inside the modal, with the field error
  await page.locator('article').filter({ hasText: 'Consultar CEP' }).getByRole('button', { name: 'Executar' }).click();
  const cep = page.getByRole('dialog', { name: 'Consultar CEP' });
  await cep.getByRole('textbox', { name: 'CEP' }).fill('123');
  await cep.getByRole('button', { name: 'Executar' }).click();
  await expect(cep.getByText('Formato inválido.')).toBeVisible();
  await cep.getByRole('button', { name: 'Cancelar' }).click();
  await page.locator('article').filter({ hasText: 'Validar CPF/CNPJ' }).getByRole('button', { name: 'Executar' }).click();
  const dlg = page.getByRole('dialog', { name: 'Validar CPF/CNPJ' });
  await dlg.getByRole('textbox').first().fill('529.982.247-25\n111.111.111-11');
  await dlg.getByRole('button', { name: 'Executar' }).click();
  await expect(page).toHaveURL(/\/apps\/automacoes\/execucoes\/\d+$/);
  await expect(page.locator('#run-body')).toHaveAttribute('data-status', 'concluido', { timeout: 30_000 });
  await expect(page.locator('#run-body')).toContainText('Resultado');
});
