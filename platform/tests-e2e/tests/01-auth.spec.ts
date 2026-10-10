import { test, expect } from '@playwright/test';
import { USERS, type Role } from './helpers';

// Roteiro: login pelo formulário como admin, operador e leitor; o que cada um vê e o
// que é recusado (403 amigável), inclusive o menu "Administração" do rail.
const ADMIN_AREAS = ['/admin', '/admin/env', '/admin/roles', '/admin/users', '/admin/tokens', '/admin/logs', '/admin/audit', '/admin/health', '/admin/deploys', '/admin/runtime', '/admin/backups', '/admin/tasks'];
const ALLOWED: Record<Role, string[]> = {
  admin: ADMIN_AREAS,
  operador: ADMIN_AREAS.filter((p) => !['/admin/roles', '/admin/users'].includes(p)),
  leitor: [],
};

for (const role of Object.keys(USERS) as Role[]) {
  test(`login como ${role}: permissões visíveis e proibidas @core`, async ({ browser }) => {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    const u = USERS[role];
    await page.goto('/admin/env');
    await expect(page).toHaveURL(/\/login\?next=/);
    await page.fill('#email', u.email);
    await page.fill('#password', u.password);
    await page.click('button.auth-submit');
    // next= devolve à página pedida (ou ao 403 dela).
    await expect(page).toHaveURL(/\/admin\/env$/);
    await page.goto('/applications');
    await expect(page.locator('.hub-user-menu .user-copy strong')).toHaveText(u.name);
    await expect(page.locator('.platform-rail')).toBeVisible();
    const adminToggle = page.locator('#hub-admin-menu');
    if (role === 'leitor') await expect(adminToggle).toHaveCount(0);
    else await expect(adminToggle).toBeVisible();

    for (const p of ADMIN_AREAS) {
      const r = await page.goto(p);
      if (ALLOWED[role].includes(p)) {
        expect(r?.status(), `${role} ${p}`).toBe(200);
        await expect(page.locator('h1.page-title, h1').first()).toBeVisible();
      } else {
        expect(r?.status(), `${role} ${p}`).toBe(403);
        await expect(page.locator('#app-root h1').first()).toHaveText('Acesso negado'); // 403 dentro do shell
      }
    }
    // Todos abrem as aplicações.
    const app = await page.goto('/apps/ativos/dashboard');
    expect(app?.status()).toBe(200);
    // API do plano de controle segue as mesmas regras.
    const api = await page.request.get('/api/admin/users');
    expect(api.status()).toBe(role === 'admin' ? 200 : 403);
    await ctx.close();
  });
}
