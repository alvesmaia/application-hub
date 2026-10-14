// PNG icons for the hub PWA (manifest /manifest.webmanifest and apple-touch-icon), generated
// from the logo (assets/shell/logo.svg). Run from platform/tests-e2e:
//   node ../scripts/gen-pwa-icons.mjs
import { createRequire } from 'node:module';
const { chromium } = createRequire(path.resolve(process.cwd(), 'package.json'))('@playwright/test');
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const assets = path.resolve(here, '../assets/shell');
const svg = fs.readFileSync(path.join(assets, 'logo.svg'), 'utf8');
const b = await chromium.launch();
const p = await b.newPage();
for (const [name, size, pad, bg] of [['icon-192.png', 192, 0, 'transparent'], ['icon-512.png', 512, 0, 'transparent'], ['icon-maskable-512.png', 512, 0.2, '#0a2848'], ['apple-touch-icon.png', 180, 0.12, '#0a2848']]) {
  await p.setViewportSize({ width: size, height: size });
  const inner = Math.round(size * (1 - 2 * pad));
  await p.setContent(`<html><body style="margin:0;background:${bg};display:grid;place-items:center;width:${size}px;height:${size}px">${svg.replace('<svg ', `<svg width="${inner}" height="${inner}" `)}</body></html>`);
  await p.screenshot({ path: path.join(assets, name), omitBackground: bg === 'transparent' });
  console.log(name);
}
await b.close();
