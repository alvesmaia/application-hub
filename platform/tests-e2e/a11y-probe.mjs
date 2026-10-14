// Manual probe (wave 9): login, main pages, axe in both themes, screenshots.
// node probe.mjs [saida]
import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';

const HUB = process.env.HUB || 'http://localhost:8080';
const out = process.argv[2] || 'probe-out';
fs.mkdirSync(out, { recursive: true });
const pages = (process.env.PAGES || '/,/applications,/inbox,/status,/search?q=a,/admin,/admin/analytics,/ativos/assets,/servicos/requests').split(',').map((p) => (p.startsWith('/') ? p : `/${p}`));
const browser = await chromium.launch();
const summary = [];
for (const scheme of (process.env.SCHEMES || 'light,dark').split(',')) {
  for (const locale of (process.env.LOCALES || 'pt-BR').split(',')) {
    const ctx = await browser.newContext({ colorScheme: scheme, locale, viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    page.on('pageerror', (e) => console.log('pageerror', e.message));
    page.on('console', (m) => { if (m.type() === 'error') console.log('console', m.text()); });
    await page.goto(`${HUB}/login`);
    await page.fill('#email', process.env.U || 'admin@demo.local');
    await page.fill('#password', process.env.P || 'admin');
    await page.click('button[type=submit]');
    await page.waitForURL((u) => !u.pathname.startsWith('/login'));
    for (const p of pages) {
      await page.goto(`${HUB}${p}`);
      if (/^\/(ativos|servicos|fornecedores)/.test(p)) {
        await page.locator('#app-root .app-sidebar').waitFor({ timeout: 30000 }).catch(() => {});
        await page.waitForTimeout(500);
      } else {
        await page.waitForTimeout(300);
      }
      await page.keyboard.press('Escape');
      const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
      const bad = r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
      const name = `${scheme}-${locale}-${p.replace(/[^a-z0-9]+/gi, '_')}`;
      await page.screenshot({ path: `${out}/${name}.png`, fullPage: false });
      summary.push({ scheme, locale, page: p, serious: bad.length, all: r.violations.length });
      for (const v of bad) { for (const n of v.nodes) { const m=/foreground color: (#[0-9a-f]+), background color: (#[0-9a-f]+)/.exec(n.failureSummary||''); if(m){const k=m[1]+' on '+m[2]; (globalThis.FG=globalThis.FG||{})[k]=(globalThis.FG[k]||[]); if(globalThis.FG[k].length<3) globalThis.FG[k].push(n.target.join(' '));} }
        if(process.env.VERBOSE) console.log(`[${name}] ${v.impact} ${v.id}: ${v.help} (${v.nodes.length})`);
        if(process.env.VERBOSE) for (const n of v.nodes.slice(0, 4)) console.log('   ', n.target.join(' '), '|', (n.failureSummary || '').split('\n').slice(1, 2).join(' '));
      }
    }
    await ctx.close();
  }
}
await browser.close();
console.table(summary); fs.writeFileSync(out+"/fg.json", JSON.stringify(globalThis.FG||{},null,1));
