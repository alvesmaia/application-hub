import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { HUB, WORK_DIR, autoConfirm, stateFile, uniq } from './helpers';

test.use({ storageState: stateFile('admin') });
test.describe.configure({ mode: 'serial' });

const RUN = uniq();
const VAR_PUB = `E2E_FLAG_${RUN}`;
const VAR_SECRET = `E2E_SECRET_${RUN}`;

const toast = (page: Page) => page.locator('#toast');

async function newVar(page: Page, name: string, kind: string, value: string, bind: [string, string][]) {
  await page.click('[data-admin-add="env"]');
  const modal = page.locator('.overlay .modal');
  await modal.locator('[data-field="name"]').fill(name);
  await modal.locator('[data-field="kind"]').selectOption(kind);
  await modal.locator('[data-field="value"]').fill(value);
  for (const [app, env] of bind) await modal.locator(`[data-field-binding][data-app="${app}"][data-env="${env}"]`).check();
  await modal.locator('button[type="submit"]').click();
  await expect(toast(page)).toContainText('Variável criada');
  await expect(page.locator(`[data-row-menu="env:${name}"]`)).toBeVisible();
}

test('variáveis: criar, segredo nunca revelável, rotacionar e aplicar', async ({ page }) => {
  test.setTimeout(300_000);
  autoConfirm(page);
  await page.goto('/admin/env');
  await newVar(page, VAR_PUB, 'publica', 'on', [['fornecedores', 'staging']]);
  await newVar(page, VAR_SECRET, 'secreta', `s3gr3d0-${RUN}`, [['fornecedores', 'staging']]);
  // Segredo mascarado na listagem e nunca revelável (nem na tela nem pela API): só substituir/rotacionar.
  const row = page.locator('tr[data-item]').filter({ hasText: VAR_SECRET });
  await expect(row.locator('[data-env-value]')).not.toContainText(`s3gr3d0-${RUN}`);
  await expect(page.locator('[data-env-reveal]')).toHaveCount(0);
  const rv = await page.request.post(`/api/admin/env/${VAR_SECRET}/reveal`, { data: {}, headers: { origin: HUB } });
  expect([403, 404, 405]).toContain(rv.status());
  // Rotacionar o token interno de fornecedores → reinício pendente → Aplicar (blue/green).
  await page.click('[data-ops-rotate="fornecedores|token"]');
  await expect(toast(page)).toContainText('rotacionada');
  const apply = page.locator('[data-ops-apply="fornecedores"]').first();
  await expect(apply).toBeVisible();
  await apply.click();
  // Espera o resultado final (não o aviso "Recriando…" de quando começa).
  await expect(toast(page)).toContainText(/recriado\(s\)|Container reiniciado/, { timeout: 180_000 });
  // O app continua servindo depois da troca.
  const r = await page.request.get('/_frag/fornecedores/dashboard');
  expect(r.status()).toBe(200);
});

test('funções e permissões: visualizar matriz @core', async ({ page }) => {
  await page.goto('/admin/users?tab=roles');
  const card = page.locator('.role-card').filter({ hasText: 'Tech Lead' }).first();
  await card.locator('[data-role-toggle]').click();
  await expect(card).toHaveClass(/open/);
  await expect(card.locator('[data-role-perm]').first()).toBeVisible();
  // Administrador é fixo (checkboxes desabilitados).
  await expect(page.locator('.role-card').filter({ hasText: 'Administrador' }).first().locator('[data-role-perm]').first()).toBeDisabled();
});

test('logs em tempo real recebem requisições do gateway', async ({ page }) => {
  await page.goto('/admin/logs');
  await page.click('[data-log-live]');
  await expect(page.locator('[data-log-live]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-log-status]')).toContainText('Conectado');
  const marker = `e2e-log-${RUN.toLowerCase()}`;
  await expect.poll(async () => {
    await page.request.get(`/_frag/servicos/knowledge?${marker}=1`);
    return page.locator('[data-log-terminal]').textContent();
  }, { timeout: 30_000 }).toContain('/servicos/knowledge');
});

test('saúde: "Verificar agora"', async ({ page }) => {
  await page.goto('/admin/health');
  await page.click('[data-health-check]');
  await expect(toast(page)).toContainText(/Todas as aplicações estão disponíveis|indisponível/, { timeout: 30_000 });
  await expect(toast(page)).toContainText('Todas as aplicações estão disponíveis');
});

const NEW_USER = { name: `Usuária E2E ${RUN}`, email: `e2e-${RUN.toLowerCase()}@demo.local`, pw: `Prov-${RUN}-2026` };

test('usuários: criar e redefinir senha (troca obrigatória no primeiro acesso) @core', async ({ page, browser }) => {
  await page.goto('/admin/users');
  await page.click('[data-user-new]');
  const m = page.locator('.overlay[data-users-modal] form');
  await m.locator('input[name="name"]').fill(NEW_USER.name);
  await m.locator('input[name="email"]').fill(NEW_USER.email);
  await m.locator('input[name="password"]').fill(NEW_USER.pw);
  await m.locator('button[type="submit"]').click();
  await expect(toast(page)).toContainText('Usuário criado');
  const row = page.locator('tr', { hasText: NEW_USER.email });
  await expect(row).toBeVisible();
  // Redefinir senha
  await row.locator('.hub-row-menu summary').click(); // ações secundárias no menu ⋯
  await row.locator('[data-user-reset]').click();
  const m2 = page.locator('.overlay[data-users-modal] form');
  const pw2 = `${NEW_USER.pw}-b`;
  await m2.locator('input[name="password"]').fill(pw2);
  await m2.locator('button[type="submit"]').click();
  await expect(toast(page)).toContainText('Senha redefinida');
  // A nova usuária entra com a provisória e é levada a trocar a senha.
  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const p = await ctx.newPage();
  await p.goto('/login');
  await p.fill('#email', NEW_USER.email);
  await p.fill('#password', pw2);
  await p.click('button.auth-submit');
  await expect(p).toHaveURL(/\/account\/password/);
  await ctx.close();
});

test('tokens de API: criar (segredo mostrado uma vez) @core', async ({ page }) => {
  await page.goto('/admin/tokens');
  await page.click('[data-token-new]');
  const m = page.locator('.overlay[data-users-modal] form');
  await m.locator('input[name="name"]').fill(`CI E2E ${RUN}`);
  await m.locator('input[name="actions"][value="upload"]').check();
  await m.locator('button[type="submit"]').click();
  const secret = page.locator('[data-token-secret] [data-token-value]');
  await expect(secret).toHaveText(/^hubt_/);
  const token = (await secret.textContent())!.trim();
  // O token autentica a API de CI (status) e não vale como sessão de página.
  const api = await page.context().request.fetch(`${HUB}/api/ops/deployments`, { headers: { authorization: `Bearer ${token}`, cookie: '' } });
  expect([200, 403]).toContain(api.status());
  fs.mkdirSync(WORK_DIR, { recursive: true });
  fs.writeFileSync(path.join(WORK_DIR, 'token.txt'), token);
  await page.reload();
  await expect(page.locator('tr', { hasText: `CI E2E ${RUN}` })).toBeVisible();
});

test('auditoria mostra as ações feitas acima', async ({ page }) => {
  await page.goto('/admin/audit?scope=platform');
  await expect(page.locator('#app-root')).toContainText(NEW_USER.email);
  await expect(page.locator('#app-root')).toContainText('Token de API criado');
  // Variáveis vinculadas a um app aparecem no escopo do app (e em Todas).
  await page.goto('/admin/audit?scope=fornecedores');
  await expect(page.locator('#app-root')).toContainText(VAR_PUB);
  await expect(page.locator('#app-root')).toContainText(/rotacionad|Rotacion/);
});
