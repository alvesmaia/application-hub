// Delivery screenshots (user block in the header and the app name on 2 lines in the sidebar)
// → platform/dist/. Stack up; uses the admin login state of the E2E tests.
//   node shots-header-sidebar.mjs
import { chromium } from '@playwright/test';
import path from 'node:path';
const HUB = process.env.HUB || 'http://localhost:8080';
const OUT = path.resolve(import.meta.dirname, '..', 'dist');
const b = await chromium.launch();
const report = [];
for (const width of [1280, 1440]) {
  const ctx = await b.newContext({ viewport: { width, height: 900 }, storageState: path.join(import.meta.dirname, '.auth', 'admin.json') });
  const page = await ctx.newPage();
  const setTheme = async (theme) => {
    await page.goto(`${HUB}/applications`);
    await page.evaluate(async (t) => {
      const f = new FormData(); f.set('theme', t);
      await fetch('/account/prefs?next=%2F', { method: 'POST', body: new URLSearchParams(f), redirect: 'manual' });
    }, theme);
  };
  for (const theme of ['light', 'dark']) {
    await setTheme(theme);
    await page.goto(`${HUB}/applications`);
    const header = page.locator('.platform-header');
    if (width === 1440) await header.screenshot({ path: path.join(OUT, `header-user-${theme}.png`) });
    await page.locator('.hub-user-menu summary').click();
    const pop = page.locator('.hub-user-popover');
    await pop.waitFor();
    if (width === 1440) await page.screenshot({ path: path.join(OUT, `header-user-menu-${theme}.png`), clip: { x: width - 420, y: 0, width: 420, height: 260 } });
    report.push({ width, theme, button: (await page.locator('.hub-user-menu .user-copy').innerText()).trim(), menu: (await pop.locator('.hub-user-who').innerText()).trim() });
    await page.keyboard.press('Escape');
    for (const app of ['ativos', 'fornecedores', 'servicos']) {
      await page.goto(`${HUB}/${app}`);
      await page.locator('#app-root .app-sidebar .sidebar-app strong').waitFor({ timeout: 20000 });
      await page.waitForFunction(() => !document.querySelector('.app-loading') && !document.querySelector('.component-loading'));
      const s = page.locator('#app-root .app-sidebar .sidebar-app');
      const m = await s.evaluate((el) => {
        const st = el.querySelector('strong');
        const ic = el.querySelector('.app-icon').getBoundingClientRect();
        const tx = st.parentElement.getBoundingClientRect();
        const lh = parseFloat(getComputedStyle(st).lineHeight);
        return { name: st.textContent, title: st.title, lines: Math.round(st.getBoundingClientRect().height / lh), clipped: st.scrollHeight > st.clientHeight + 1, iconMid: Math.round(ic.top + ic.height / 2), textMid: Math.round(tx.top + tx.height / 2) };
      });
      report.push({ width, theme, app, ...m });
      await page.locator('#app-root .app-sidebar').screenshot({ path: path.join(OUT, `sidebar-${app}-${theme}-${width}.png`), clip: undefined });
    }
  }
  await setTheme('light');
  await ctx.close();
}
await b.close();
console.log(JSON.stringify(report, null, 1));
