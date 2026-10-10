import { test, expect, type Page } from '@playwright/test';
import { stateFile, HUB } from './helpers';

// Celular: Usuários como lista de cartões (padrão `hub-table-cards`), folha de tela cheia,
// gaveta do shell no lugar do rail e navegação do app como gaveta.
test.use({ storageState: stateFile('admin') });

const created: string[] = [];

// Limpeza: desativa e exclui pela API os usuários criados aqui (marca `e2e-`, a mesma do global-teardown).
test.afterAll(async ({ playwright }) => {
  const api = await playwright.request.newContext({ baseURL: HUB, storageState: stateFile('admin'), extraHTTPHeaders: { origin: HUB } });
  try {
    const r = await api.get('/api/admin/users');
    if (!r.ok()) return;
    for (const u of (await r.json()) as { id: string; email: string; active: boolean }[]) {
      if (!created.includes(u.email)) continue;
      if (u.active) await api.put(`/api/admin/users/${u.id}`, { data: { active: false } });
      await api.delete(`/api/admin/users/${u.id}`, { data: {} });
    }
  } finally {
    await api.dispose();
  }
});

const MOBILE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true };

async function noHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
}

test.describe('celular 390×844', () => {
  test.use(MOBILE);

  test('Usuários: cartões, menu "⋯" dentro da tela, folha de criação @core', async ({ page }) => {
    await page.goto('/admin/users');
    await noHorizontalOverflow(page);
    const card = page.locator('.hub-table-cards tbody tr').first();
    await expect(card).toBeVisible();
    expect(await card.evaluate((el) => getComputedStyle(el).display)).toBe('grid');
    await expect(page.locator('.hub-table-cards thead')).not.toBeInViewport();
    await expect(card.locator('.hub-avatar')).toBeVisible();
    await expect(card.locator('.hub-truncate')).toHaveAttribute('title', /@/);
    await expect(card.locator('[data-label="Status"] .status-pill')).toBeVisible();

    // Menu "⋯" da última linha (rolada até o fim da tela): alvo ≥ 40px, lista dentro da tela.
    const last = page.locator('.hub-table-cards tbody tr').last();
    const summary = last.locator('.hub-row-menu summary');
    await summary.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollBy(0, -(window.innerHeight - 120)));
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const sb = (await summary.boundingBox())!;
    expect(sb.width).toBeGreaterThanOrEqual(40);
    expect(sb.height).toBeGreaterThanOrEqual(40);
    await summary.click();
    const list = last.locator('.hub-row-menu-list');
    await expect(list).toBeVisible();
    const lb = (await list.boundingBox())!;
    expect(lb.x).toBeGreaterThanOrEqual(0);
    expect(lb.x + lb.width).toBeLessThanOrEqual(390);
    expect(lb.y).toBeGreaterThanOrEqual(0);
    expect(lb.y + lb.height).toBeLessThanOrEqual(844);
    await expect(list.getByRole('button', { name: 'Editar' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(list).toBeHidden();

    // Novo usuário: folha de tela cheia com ações fixas no rodapé; envia de verdade.
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.locator('[data-user-new]').click();
    const sheet = page.locator('.overlay[data-users-modal] .modal.hub-sheet');
    await expect(sheet).toBeVisible();
    const box = (await sheet.boundingBox())!;
    expect(Math.round(box.width)).toBe(390);
    expect(Math.round(box.height)).toBe(844);
    const name = sheet.locator('input[name="name"]');
    expect((await name.boundingBox())!.height).toBeGreaterThanOrEqual(40);
    const stamp = Date.now().toString(36);
    created.push(`e2e-mobile-${stamp}@demo.local`);
    await name.fill(`Usuária Mobile ${stamp}`);
    await sheet.locator('input[name="email"]').fill(`e2e-mobile-${stamp}@demo.local`);
    const submit = sheet.getByRole('button', { name: 'Criar usuário' });
    await expect(submit).toBeInViewport();
    await submit.click();
    await expect(page.locator('.hub-table-cards').getByText(`e2e-mobile-${stamp}@demo.local`)).toBeVisible();
  });

  test('shell: sem rail, menu ☰ à direita abre painel de cima para baixo, navegação e foco @core', async ({ page }) => {
    await page.goto('/admin/users');
    await expect(page.locator('#hub-rail')).toBeHidden();
    await expect(page.locator('[data-hub-rail-toggle]')).toBeHidden();
    await expect(page.locator('.hub-search-trigger')).toBeHidden();
    await expect(page.getByRole('button', { name: 'Buscar', exact: true })).toBeVisible();
    const burger = page.locator('[data-hub-drawer-toggle]');
    await expect(burger).toHaveAttribute('aria-label', 'Abrir menu');
    // ☰ no canto direito, depois de busca e notificações; avatar dentro do menu.
    const bb = (await burger.boundingBox())!;
    expect(bb.x + bb.width).toBeGreaterThan(370);
    expect(bb.width).toBeGreaterThanOrEqual(44);
    await expect(page.locator('.platform-header .hub-user-menu')).toBeHidden();
    await burger.click();
    await expect(burger).toHaveAttribute('aria-label', 'Fechar menu');
    await expect(burger).toHaveAttribute('aria-expanded', 'true');
    const rail = page.locator('#hub-rail');
    await expect(rail).toBeVisible();
    await expect(rail.locator('.hub-drawer-user')).toBeVisible();
    // Painel de largura total logo abaixo do header.
    const rb = (await rail.boundingBox())!;
    const hb = (await page.locator('.platform-header').boundingBox())!;
    expect(Math.round(rb.x)).toBe(0);
    expect(Math.round(rb.width)).toBe(390);
    expect(Math.abs(rb.y - (hb.y + hb.height))).toBeLessThanOrEqual(1);
    await expect(rail.getByRole('button', { name: /Sair/ })).toBeAttached();
    await expect(rail.locator('a[aria-current="page"]')).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(rail).toBeHidden();
    await expect(burger).toBeFocused();
    // ✕ e fundo fecham; tocar num item navega e fecha.
    await burger.click();
    await burger.click();
    await expect(rail).toBeHidden();
    await burger.click();
    await page.locator('.hub-drawer-backdrop').dispatchEvent('click');
    await expect(rail).toBeHidden();
    await burger.click();
    await rail.getByRole('link', { name: 'Aplicações', exact: true }).click();
    await expect(page).toHaveURL(/\/applications$/);
    await expect(page.locator('#hub-rail')).toBeHidden();
    await noHorizontalOverflow(page);
  });

  test('app: barra "App · Seção" abre as seções como painel, sem estouro horizontal @core', async ({ page }) => {
    await page.goto('/apps/ativos');
    await expect(page.locator('#app-root .app-sidebar')).toBeAttached({ timeout: 30_000 });
    await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 30_000 });
    await expect(page.locator('#app-root .app-sidebar')).not.toBeInViewport();
    await noHorizontalOverflow(page);
    const bar = page.locator('[data-hub-menu="open"]').first();
    await expect(bar).toContainText('·');
    await bar.click();
    await expect(bar).toHaveAttribute('aria-expanded', 'true');
    const sb = page.locator('#app-root .app-sidebar');
    await expect(sb).toBeInViewport();
    const barBox = (await bar.boundingBox())!;
    const sbBox = (await sb.boundingBox())!;
    expect(Math.abs(sbBox.y - (barBox.y + barBox.height))).toBeLessThanOrEqual(2);
    await page.keyboard.press('Escape');
    await expect(sb).toBeHidden();
    await bar.click();
    await sb.locator('.nav-item').nth(1).click();
    await expect(sb).toBeHidden();
    await noHorizontalOverflow(page);
  });

  test('app: o documento rola (puxar para atualizar), sem trava depois de abrir e fechar menus @core', async ({ page }) => {
    await page.goto('/apps/ativos');
    await expect(page.locator('#app-root .app-sidebar')).toBeAttached({ timeout: 30_000 });
    await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 30_000 });
    // Conteúdo longo garantido (a lista do Ativos já é longa; reforça se precisar).
    await page.evaluate(() => { const c = document.querySelector('.workspace-content') as HTMLElement; if (c && document.documentElement.scrollHeight <= innerHeight) c.style.minHeight = '2000px'; });
    const state = () => page.evaluate(() => {
      const se = document.scrollingElement as HTMLElement;
      const cs = (e: Element) => getComputedStyle(e);
      const wc = document.querySelector('.workspace-content') as HTMLElement;
      return {
        scroller: se === document.documentElement,
        long: se.scrollHeight > innerHeight,
        htmlOB: cs(document.documentElement).overscrollBehaviorY,
        bodyOB: cs(document.body).overscrollBehaviorY,
        bodyPos: cs(document.body).position,
        bodyOv: cs(document.body).overflowY,
        innerScroll: wc ? ['auto', 'scroll'].includes(cs(wc).overflowY) : false,
        wsPos: cs(document.querySelector('.workspace')!).position,
      };
    });
    const ok = async () => {
      const s = await state();
      expect(s.scroller && s.long).toBe(true);
      expect(s.htmlOB).toBe('auto');
      expect(s.bodyOB).toBe('auto');
      expect(s.bodyPos).not.toBe('fixed');
      expect(s.bodyOv).not.toBe('hidden');
      expect(s.innerScroll).toBe(false);
      expect(s.wsPos).not.toBe('fixed');
    };
    await ok();
    await page.evaluate(() => window.scrollTo(0, 400));
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(100);
    await page.evaluate(() => window.scrollTo(0, 0));
    // Menu ☰ do shell: trava só enquanto aberto.
    const burger = page.locator('[data-hub-drawer-toggle]');
    await burger.click();
    expect((await state()).bodyOv).toBe('hidden');
    await burger.click();
    await ok();
    // Menu do app (barra "App · Seção").
    const bar = page.locator('[data-hub-menu="open"]').first();
    await bar.click();
    expect((await state()).bodyOv).toBe('hidden');
    await page.keyboard.press('Escape');
    await ok();
    // Abrir o ☰ e navegar: a trava some.
    await burger.click();
    await page.locator('#hub-rail').getByRole('link', { name: 'Aplicações', exact: true }).click();
    await expect(page).toHaveURL(/\/applications$/);
    expect(await page.evaluate(() => document.body.classList.contains('hub-drawer-open'))).toBe(false);
  });
});

test('Usuários a 1440: tabela inalterada @core', async ({ page }) => {
  await page.goto('/admin/users');
  await expect(page.locator('.hub-table-cards thead')).toBeVisible();
  expect(await page.locator('.hub-table-cards tbody tr').first().evaluate((el) => getComputedStyle(el).display)).toBe('table-row');
  await expect(page.locator('.hub-table-cards th.hub-col-secondary')).toBeVisible();
  await expect(page.locator('.hub-table-cards .hub-avatar').first()).toBeHidden();
  await expect(page.locator('#hub-rail')).toBeVisible();
  await expect(page.locator('[data-hub-drawer-toggle]')).toBeHidden();
});

test('Usuários a 768: tabela sem a coluna secundária e sem estouro', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.goto('/admin/users');
  await expect(page.locator('.hub-table-cards th.hub-col-secondary')).toBeHidden();
  await noHorizontalOverflow(page);
});

// Menu ☰ de cima para baixo em toda tela < 1024px e no celular em paisagem; rail só ≥ 1024.
const NARROW: { name: string; w: number; h: number; touch?: boolean; shot?: string }[] = [
  ...[360, 390, 430, 600, 767, 768, 800, 900, 1000, 1023].map((w) => ({ name: `${w}`, w, h: 900, shot: w === 390 || w === 800 ? `mobile-menu-${w}` : undefined })),
  { name: 'paisagem 844×390', w: 844, h: 390, touch: true, shot: 'mobile-menu-landscape' },
];
for (const v of NARROW) {
  test.describe(`menu ☰ a ${v.name}`, () => {
    test.use({ viewport: { width: v.w, height: v.h }, isMobile: !!v.touch, hasTouch: !!v.touch });
    test(`sem rail; painel de cima para baixo, largura total @core`, async ({ page }) => {
      await page.goto('/admin/users');
      const rail = page.locator('#hub-rail');
      await expect(rail).toBeHidden();
      await expect(page.locator('[data-hub-rail-toggle]')).toBeHidden();
      const burger = page.locator('[data-hub-drawer-toggle]');
      await expect(burger).toBeVisible();
      await burger.click();
      await expect(rail).toBeVisible();
      expect(await rail.evaluate((el) => getComputedStyle(el).animationName)).toBe('hub-panel-down');
      await page.waitForTimeout(300);
      expect(await rail.evaluate((el) => getComputedStyle(el).transform)).toBe('none');
      const rb = (await rail.boundingBox())!;
      const hb = (await page.locator('.platform-header').boundingBox())!;
      expect(Math.round(rb.x)).toBe(0);
      expect(Math.round(rb.width)).toBe(v.w);
      expect(Math.abs(rb.y - (hb.y + hb.height))).toBeLessThanOrEqual(1);
      if (v.shot) await page.screenshot({ path: `../dist/${v.shot}.png` });
      await page.keyboard.press('Escape');
      await expect(rail).toBeHidden();
      await noHorizontalOverflow(page);
    });
  });
}
for (const w of [1024, 1280]) {
  test(`rail visível a ${w} @core`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: 900 });
    await page.goto('/admin/users');
    await expect(page.locator('#hub-rail')).toBeVisible();
    await expect(page.locator('[data-hub-rail-toggle]')).toBeVisible();
    await expect(page.locator('[data-hub-drawer-toggle]')).toBeHidden();
  });
}

test('app a 800: barra "App · Seção" no lugar do sidebar em coluna', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 900 });
  await page.goto('/apps/ativos');
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 30_000 });
  const sb = page.locator('#app-root .app-sidebar');
  await expect(sb).toBeHidden();
  const bar = page.locator('[data-hub-menu="open"]').first();
  await bar.click();
  await expect(sb).toBeVisible();
  const b = (await sb.boundingBox())!;
  expect(Math.round(b.x)).toBe(0);
  expect(Math.round(b.width)).toBe(800);
  await noHorizontalOverflow(page);
});
