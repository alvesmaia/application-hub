// Delivery screenshots (apps identity, rail, loader) → platform/dist/.
import { chromium } from '@playwright/test';
import path from 'node:path';
const HUB = process.env.HUB || 'http://localhost:8080';
const OUT = path.resolve(import.meta.dirname, '..', 'dist');
const only = process.argv[2];
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, storageState: path.join(import.meta.dirname, '.auth', 'admin.json') });
const page = await ctx.newPage();
const setTheme = async (theme) => {
  await page.goto(`${HUB}/applications`);
  await page.evaluate(async (t) => {
    const f = new FormData(); f.set('theme', t);
    await fetch('/account/prefs?next=%2F', { method: 'POST', body: new URLSearchParams(f), redirect: 'manual' });
  }, theme);
};
const openApp = async (app) => {
  await page.goto(`${HUB}/${app}`);
  await page.locator('#app-root .app-sidebar').waitFor({ timeout: 20000 });
  await page.waitForFunction(() => !document.querySelector('.app-loading'));
  await page.waitForTimeout(600);
};
const setRail = async (mode) => {
  for (let i = 0; i < 3 && !(await page.evaluate((m) => document.body.classList.contains(`rail-${m}`), mode)); i++) await page.locator('[data-hub-rail-toggle]').click();
  await page.waitForTimeout(500);
};
if (!only || only === 'apps') {
  for (const theme of ['light', 'dark']) {
    await setTheme(theme);
    for (const app of ['ativos', 'fornecedores', 'servicos']) {
      await openApp(app);
      await setRail('hidden');
      await page.screenshot({ path: path.join(OUT, `apps-identity-${app}-${theme}.png`) });
    }
  }
  await setTheme('light');
}
if (!only || only === 'rail') {
  await openApp('servicos');
  for (const m of ['expanded', 'collapsed', 'hidden']) {
    await setRail(m);
    if (m === 'collapsed') { await page.locator('#hub-rail .rail-group-label').nth(2).hover(); await page.waitForTimeout(400); }
    await page.screenshot({ path: path.join(OUT, `rail-${m}-in-app.png`) });
  }
  await page.mouse.move(700, 400);
  await setRail('hidden');
}
if (!only || only === 'loader') {
  const c2 = await b.newContext({ viewport: { width: 1440, height: 900 }, storageState: path.join(import.meta.dirname, '.auth', 'admin.json') });
  const p2 = await c2.newPage();
  await p2.route('**/_frag/fornecedores**', async (r) => { if (r.request().headers()['x-hub-nav']) await new Promise((x) => setTimeout(x, 4000)); await r.continue(); });
  p2.goto(`${HUB}/fornecedores`).catch(() => {});
  await p2.locator('.app-loading').waitFor({ state: 'visible' });
  await p2.waitForFunction(() => parseInt(document.querySelector('.loader-percentage')?.textContent || '0', 10) >= 49);
  await p2.screenshot({ path: path.join(OUT, 'loader.png') });
  await c2.close();
}
await b.close();
console.log('ok');
