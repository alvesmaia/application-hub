import { test, expect, type Page } from '@playwright/test';
import { stateFile } from './helpers';

// Indicador de carregamento dos apps e botão do rail da plataforma (3 modos).
test.use({ storageState: stateFile('admin') });

/** Atrasa as respostas de fragmento do app (rede lenta). */
async function slowFragments(page: Page, app: string, ms: number) {
  await page.route(`**/_frag/${app}**`, async (route) => {
    const req = route.request();
    if (req.method() !== 'GET' || new URL(req.url()).pathname.includes('/assets/')) return route.continue();
    await new Promise((r) => setTimeout(r, ms));
    await route.continue();
  });
}

test('loader: abrir o app mostra o carregador 0→100% em ~3,5 s, depois o skeleton e o conteúdo @core', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: stateFile('admin') });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    const w = window as unknown as { __pct: string[]; __t: Record<string, number> };
    w.__pct = []; w.__t = {};
    new MutationObserver(() => {
      const p = document.querySelector('.app-loading .loader-percentage');
      if (p && w.__pct[w.__pct.length - 1] !== p.textContent) w.__pct.push(p.textContent || '');
      if (!document.querySelector('.app-loading') && w.__pct.length && !w.__t.loaderGone) w.__t.loaderGone = performance.now();
      if (document.querySelector('.component-loading .skeleton-page-heading') && !w.__t.skeleton) w.__t.skeleton = performance.now();
      if (document.querySelector('.app-sidebar') && !document.querySelector('.component-loading') && !w.__t.content) w.__t.content = performance.now();
    }).observe(document, { subtree: true, childList: true, characterData: true, attributes: true });
  });
  await page.goto('/apps/ativos');
  const loader = page.locator('#app-root .app-loading');
  await expect(loader).toBeVisible();
  await expect(loader.locator('.loader-app-name')).toHaveText(/Ativos/);
  await expect(loader.locator('.loader-message')).toHaveText('Preparando sua aplicação');
  await expect(loader.locator('.loader-footer')).toContainText('Carregando workspace');
  await expect(page.locator('.platform-header')).toBeVisible();
  await expect(page.locator('#app-root .component-loading-caption')).toHaveText(/Carregando Gestão de Ativos/, { timeout: 6_000 });
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 6_000 });
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible();
  const { pct, t } = await page.evaluate(() => { const w = window as unknown as { __pct: string[]; __t: Record<string, number> }; return { pct: w.__pct, t: w.__t }; });
  expect(pct[0]).toBe('0%');
  expect(pct[pct.length - 1]).toBe('100%');
  const nums = pct.map((x) => parseInt(x, 10));
  expect(nums.every((n, i) => i === 0 || n >= nums[i - 1]), 'percentual só cresce').toBe(true);
  expect(t.loaderGone, 'carregador por ~3,5 s').toBeGreaterThan(3300);
  expect(t.loaderGone).toBeLessThan(4600);
  expect(t.content - t.skeleton, 'skeleton por ~0,9 s').toBeGreaterThan(800);
  await ctx.close();
});

test('loader: fragmento lento — o skeleton fica até o conteúdo chegar (nunca área vazia)', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: stateFile('admin') });
  const page = await ctx.newPage();
  await slowFragments(page, 'fornecedores', 8000);
  await page.goto('/apps/fornecedores');
  await expect(page.locator('#app-root .component-loading')).toBeVisible({ timeout: 6_000 });
  await page.waitForTimeout(1500);
  await expect(page.locator('#app-root .component-loading')).toBeVisible();
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 15_000 });
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible();
  await ctx.close();
});

test('loader: troca de página no app mostra só o skeleton (~0,7 s) @core', async ({ page }) => {
  await page.goto('/apps/ativos');
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 10_000 });
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible();
  const t0 = Date.now();
  await page.locator('.sidebar-nav a.nav-item[href="/apps/ativos/movements"]').click();
  const busy = page.locator('#app-root .workspace-content.component-loading');
  await expect(busy).toBeVisible();
  await expect(page.locator('#app-root .app-loading')).toHaveCount(0);
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible();
  await expect(busy).toHaveCount(0, { timeout: 10_000 });
  expect(Date.now() - t0).toBeGreaterThanOrEqual(650);
  await expect(page.locator('.sidebar-nav a.nav-item.active[href="/apps/ativos/movements"]')).toBeVisible();
  // Voltar do navegador cancela o skeleton (como no mockup).
  await page.goBack();
  await expect(page).toHaveURL(/\/apps\/ativos$/);
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0);
});

const mode = (page: Page) => page.evaluate(() => ['expanded', 'collapsed', 'hidden'].find((m) => document.body.classList.contains(`rail-${m}`)));

test('rail: 3 modos dentro e fora dos apps, lembrados à parte @core', async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: stateFile('admin'), viewport: { width: 1280, height: 720 } });
  const page = await ctx.newPage();
  const toggle = page.locator('[data-hub-rail-toggle]');
  const rail = page.locator('#hub-rail');

  // Fora dos apps: expandido por padrão.
  await page.goto('/applications');
  expect(await mode(page)).toBe('expanded');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(rail.locator('.rail-name').first()).toBeVisible();

  // Dentro do app: oculto por padrão; o botão abre o rail.
  await page.goto('/apps/ativos');
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 20_000 });
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible();
  expect(await mode(page)).toBe('hidden');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(rail).toBeHidden();
  await toggle.click(); // → expandido
  expect(await mode(page)).toBe('expanded');
  await expect(rail).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible();
  // Sem estouro horizontal a 1280.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  // Sidebar do app começa depois do rail (fim da transição).
  await expect.poll(async () => {
    const sb = await page.locator('#app-root .app-sidebar').boundingBox();
    const rb = await rail.boundingBox();
    return sb && rb ? sb.x - (rb.x + rb.width) : -1;
  }).toBeGreaterThanOrEqual(-1);

  // Ctrl+B → recolhido: só ícones, dica no hover.
  await page.locator('body').press('Control+b');
  expect(await mode(page)).toBe('collapsed');
  await expect.poll(async () => (await rail.boundingBox())?.width ?? 999).toBeLessThanOrEqual(60);
  const home = rail.locator('.rail-item').first();
  await home.hover();
  await expect(home.locator('.rail-tooltip')).toBeVisible();
  // Grupo administrativo vira marcador com ícone.
  await expect(rail.locator('.rail-group-label .rail-group-icon').first()).toBeVisible();

  // Persistência dentro do app (nova página do app abre recolhida).
  await page.goto('/apps/fornecedores');
  await expect(page.locator('#app-root .app-sidebar')).toBeVisible({ timeout: 20_000 });
  expect(await mode(page)).toBe('collapsed');
  // … e fora do app continua a escolha de fora (expandido).
  await page.goto('/applications');
  expect(await mode(page)).toBe('expanded');
  await toggle.click(); // fora: → recolhido
  await toggle.click(); // → oculto
  expect(await mode(page)).toBe('hidden');
  await expect(rail).toBeHidden();
  await page.reload();
  expect(await mode(page)).toBe('hidden');
  await expect(toggle).toHaveAttribute('aria-label', /oculto/);
  await toggle.click(); // → expandido (ciclo)
  expect(await mode(page)).toBe('expanded');
  await page.goto('/apps/ativos');
  expect(await mode(page)).toBe('collapsed');
  await ctx.close();
});
