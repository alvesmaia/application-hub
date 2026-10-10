import { test, expect } from '@playwright/test';
import path from 'node:path';
import { stateFile } from './helpers';

// F24 — portal do desenvolvedor: navegação entre seções, filtro, cópia de código e «Copiar prompt» (área de
// transferência simulada; o fallback execCommand cobre contexto não seguro). Capturas em platform-py/dist/shots.
test.use({ storageState: stateFile('admin') });
const SHOTS = path.resolve(__dirname, '../../../platform-py/dist/shots');

async function mockClipboard(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    (window as unknown as { __copied: string[] }).__copied = [];
    const push = (t: string) => { (window as unknown as { __copied: string[] }).__copied.push(t); return Promise.resolve(); };
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: push }, configurable: true });
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
  });
}
const copied = (page: import('@playwright/test').Page) => page.evaluate(() => (window as unknown as { __copied: string[] }).__copied);

test('developers: navegação, âncoras, filtro e Markdown cru @core', async ({ page }) => {
  await page.goto('/developers');
  const nav = page.locator('nav.dev-nav');
  await expect(nav.locator('a[aria-current="page"]').first()).toHaveText(/Visão geral/);
  for (const [label, url, heading] of [['Publicar', '/developers/cli/hub-deploy', 'Publicar'], ['Banco de dados', '/developers/sdk/hub-sdk-db', 'Banco de dados'],
    ['Primeiro app', '/developers/guias/primeiro-app', 'Primeiro app'], ['14. Manifesto', '/developers/contrato/14-manifesto', 'Manifesto'],
    ['Instruções para agentes', '/developers/agente', 'Instruções para agentes']] as const) {
    await nav.locator('[data-dev-nav-filter]').fill(label);
    await nav.getByRole('link', { name: label, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${url}$`));
    await expect(page.locator('.dev-doc h1')).toContainText(heading);
    await expect(page.locator('.dev-crumbs [aria-current="page"]')).toHaveText(label);
  }
  await page.goto('/developers/cli');
  await page.locator('.dev-doc a', { hasText: 'Publicar' }).click(); // cada comando é uma página
  await expect(page).toHaveURL(/\/developers\/cli\/hub-deploy$/);
  await expect(page.locator('.dev-doc h1')).toHaveText('Publicar');
  await page.goto('/developers/cli');
  await expect(page.locator('[data-dev-raw]')).toHaveCount(0);
  await expect(page.locator('link[rel="alternate"][type="text/markdown"]')).toHaveAttribute('href', '/dev/docs/cli.md');
  await expect(page.locator('.app-page-header')).toHaveCount(0);
  // filtro da barra lateral: «/» foca; sem acerto mostra o aviso
  await page.locator('body').press('/');
  await expect(page.locator('[data-dev-nav-filter]')).toBeFocused();
  await page.keyboard.type('zzzz-nada');
  await expect(page.locator('[data-dev-nav-empty]')).toBeVisible();
  await page.locator('[data-dev-nav-filter]').fill('banco');
  await expect(page.getByRole('link', { name: 'Banco: SQLite e Postgres' })).toBeVisible();
  await expect(page.locator('nav.dev-nav').getByRole('link', { name: 'SDK', exact: true })).toBeHidden();
  const raw = await page.request.get('/dev/docs/cli.md');
  expect(raw.headers()['content-type']).toContain('text/markdown');
  expect(await raw.text()).toContain('# Referência da CLI');
});

test('developers: «Nesta página» acompanha a rolagem com o trilho ativo', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/developers/guias/tempo-real');
  const path = page.locator('.dev-toc-rail path');
  await expect(page.locator('.dev-toc-rail')).toHaveClass(/(^| )on( |$)/);
  const first = await path.evaluate((p) => (p as SVGPathElement).style.strokeDashoffset);
  await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight)); // fim: o último item
  await expect(page.locator('.dev-toc-link').last()).toHaveAttribute('aria-current', 'true');
  await expect.poll(() => path.evaluate((p) => (p as SVGPathElement).style.strokeDashoffset)).not.toBe(first);
  await page.locator('.dev-toc-link').first().click();
  await expect(page.locator('.dev-toc-link').first()).toHaveAttribute('aria-current', 'true');
});

test('developers: Copiar prompt e copiar bloco de código @core', async ({ page }) => {
  await mockClipboard(page);
  await page.goto('/developers');
  const box = page.locator('[data-hub-copy-prompt]');
  await expect(box.getByRole('img', { name: 'Claude Code' })).toBeVisible();
  for (const n of ['OpenAI Codex', 'GitHub Copilot', 'Cursor', 'OpenCode']) await expect(box.getByRole('img', { name: n })).toBeVisible();
  await expect(box.locator('textarea')).toBeHidden();
  const btn = box.getByRole('button', { name: 'Copiar prompt' });
  await btn.click();
  await expect(box.getByRole('button', { name: 'Prompt copiado' })).toBeVisible();
  await expect(box.locator('[data-hub-copy-prompt-status]')).toHaveText('Prompt copiado');
  const full = (await copied(page))[0];
  expect(full).toMatch(/^Siga as instruções em http\S*\/dev\/docs\/agente\.md para criar e publicar um app neste Application Hub\.$/);
  await page.screenshot({ path: path.join(SHOTS, 'docs-comecar-copiado.png') });
  await expect(box.getByRole('button', { name: 'Copiar prompt' })).toBeVisible({ timeout: 4000 });
  await page.locator('.dev-code').first().getByRole('button', { name: 'Copiar' }).click();
  expect((await copied(page))[1]).toContain('uv tool install hub-cli');
});

test('developers: fallback sem área de transferência (contexto não seguro)', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    (window as unknown as { __exec: number }).__exec = 0;
    document.execCommand = () => { (window as unknown as { __exec: number }).__exec += 1; return true; };
  });
  await page.goto('/developers');
  await page.locator('[data-hub-copy-prompt-button]').click();
  await expect(page.locator('[data-hub-copy-prompt-button]')).toHaveClass(/is-copied/);
  expect(await page.evaluate(() => (window as unknown as { __exec: number }).__exec)).toBe(1);
});

test('developers: capturas claro/escuro', async ({ page }) => {
  for (const theme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: theme });
    for (const [slug, name] of [['', 'comecar'], ['/cli', 'cli'], ['/guias/telas', 'guia-telas']] as const) {
      await page.goto(`/developers${slug}`);
      await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
      await page.screenshot({ path: path.join(SHOTS, `docs-${name}-${theme}.png`), fullPage: false });
    }
  }
});

test.describe('documentação pública (sem sessão)', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('visitante lê /developers e o Markdown; prompt copia o ponteiro @core', async ({ page }) => {
    await mockClipboard(page);
    const r = await page.goto('/developers');
    expect(r?.status()).toBe(200);
    await expect(page.locator('.platform-rail')).toHaveCount(0);
    await expect(page.locator('.hub-user-menu')).toHaveCount(0);
    await expect(page.locator('.platform-header').getByRole('link', { name: 'Entrar' })).toHaveAttribute('href', /\/login\?next=/);
    await expect(page.getByRole('link', { name: 'crie um token' })).toHaveAttribute('href', /\/admin\/tokens$/);
    await expect(page.locator('.dev-code .hl-nb, .dev-code [class^="hl-"]').first()).toBeVisible();
    await page.locator('[data-hub-copy-prompt-button]').click();
    await expect(page.locator('[data-hub-copy-prompt-button]')).toContainText('Prompt copiado');
    expect((await copied(page))[0]).toMatch(/\/dev\/docs\/agente\.md/);
    const md = await page.request.get('/dev/docs/agente.md');
    expect(md.status()).toBe(200);
    expect(md.headers()['content-type']).toContain('text/markdown');
    expect(await md.text()).toContain('hub project create <id-do-app> --stack python-fastapi --yes --json');
  });

  test('tema público: segue o SO e o seletor grava só no navegador', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto('/developers/contrato/14-manifesto');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'auto');
    const bg = () => page.locator('.dev-code').first().evaluate((el) => getComputedStyle(el).backgroundColor);
    const darkBg = await bg();
    await page.locator('[data-dev-theme]').click(); // sol/lua: escuro (SO) → claro
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    expect(await bg()).not.toBe(darkBg);
    await expect(page.locator('.dev-code-lang')).toHaveCount(0);
    await expect(page.locator('.dev-code [class^="hl-"]').first()).toBeAttached();
    await page.setViewportSize({ width: 1440, height: 900 });
    for (const theme of ['light', 'dark'] as const) {
      if ((await page.locator('html').getAttribute('data-theme')) !== theme) await page.locator('[data-dev-theme]').click();
      for (const [kind, sel, url] of [['bash', '.dev-code:has(.hl-nf)', '/developers/cli/hub-deploy'],
        ['json', '.dev-code:has(.hl-nt)', '/developers/contrato/14-manifesto']] as const) {
        await page.goto(url);
        await page.locator(sel).first().evaluate((el) => el.scrollIntoView({ block: 'center' }));
        await page.locator(sel).first().hover();
        await page.screenshot({ path: path.join(SHOTS, `docsdark-${kind}-${theme}-1440.png`) });
      }
    }
    await page.evaluate(() => localStorage.removeItem('hub-public-theme'));
  });

  test('capturas públicas claro/escuro 1440 e 390', async ({ page }) => {
    for (const [w, h] of [[1440, 900], [390, 844]] as const) {
      await page.setViewportSize({ width: w, height: h });
      for (const theme of ['light', 'dark'] as const) {
        await page.emulateMedia({ colorScheme: theme });
        for (const [slug, name] of [['', 'comecar'], ['/agente', 'agente'], ['/cli', 'cli']] as const) {
          await page.goto(`/developers${slug}`);
          await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
          await page.screenshot({ path: path.join(SHOTS, `pubdocs-${name}-${theme}-${w}.png`) });
        }
      }
    }
  });
});
