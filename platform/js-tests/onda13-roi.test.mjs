// Onda 13 — câmera do hub (hub-device.js): só o QUADRO DE MIRA é lido (recorte do vídeo com
// object-fit: cover + código ≥ 90 % dentro) e as escolhas (tipo de código, Interativo/Automático,
// pausa) ficam dentro da camada, avisando o app por data-hub-scan-prefs. Regras puras em node;
// comportamento num Chromium real com a CSP de página de app. Pulado sem Playwright/Chromium.
//   node --test platform/js-tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const platform = path.resolve(here, '..');
const assets = path.join(platform, 'assets/shell');
const require = createRequire(import.meta.url);
const Dev = require(path.join(assets, 'hub-device.js'));

const near = (a, b, eps = 0.5) => assert.ok(Math.abs(a - b) <= eps, `${a} ≈ ${b}`);

test('mira: barras 80 % × 30 %, QR quadrado 60 % do lado menor, centralizados', () => {
  assert.deepEqual(Dev.aimRect(400, 800, false), { x: 40, y: 280, w: 320, h: 240 });
  assert.deepEqual(Dev.aimRect(400, 800, true), { x: 80, y: 280, w: 240, h: 240 });
  assert.deepEqual(Dev.aimRect(1000, 500, true), { x: 350, y: 100, w: 300, h: 300 });
});

test('object-fit: cover — celular em pé (390×844) com vídeo 1280×720', () => {
  const m = Dev.coverMap(1280, 720, 390, 844);
  near(m.scale, 844 / 720, 1e-9);
  near(m.offY, 0, 1e-9);
  near(m.offX, (390 - 1280 * (844 / 720)) / 2, 1e-9); // laterais do vídeo cortadas
  const aim = Dev.aimRect(390, 844, false);
  const r = Dev.roi(1280, 720, 390, 844, aim, 0);
  // Mira centralizada no palco = centro do vídeo; tamanho em px do vídeo = px CSS / escala.
  near(r.sx + r.aim.x + r.aim.w / 2, 640, 1);
  near(r.sy + r.aim.y + r.aim.h / 2, 360, 1);
  near(r.aim.w, 312 / m.scale, 0.01);
  near(r.aim.h, (844 * 0.3) / m.scale, 0.01);
  assert.ok(r.sw < 300 && r.sh < 230, `recorte pequeno (${r.sw}×${r.sh}) — menos CPU`);
  // QR: quadrado de 234 px CSS → ~200 px do vídeo.
  const q = Dev.roi(1280, 720, 390, 844, Dev.aimRect(390, 844, true), 0);
  near(q.aim.w, 234 / m.scale, 0.01);
  near(q.aim.w, q.aim.h, 0.01);
  // Folga de 10 % em cada lado, limitada ao vídeo.
  const f = Dev.roi(1280, 720, 390, 844, aim, 0.1);
  assert.ok(f.sw > r.sw && f.sh > r.sh);
  near(f.aim.x, (f.sw - f.aim.w) / 2, 1.5);
});

test('object-fit: cover — palco deitado (1440×700) e vídeo em pé (720×1280)', () => {
  const m = Dev.coverMap(1280, 720, 1440, 700);
  near(m.scale, 1.125, 1e-9);
  near(m.offX, 0, 1e-9);
  near(m.offY, (700 - 810) / 2, 1e-9); // topo e base cortados
  const r = Dev.roi(1280, 720, 1440, 700, { x: 0, y: 0, w: 1440, h: 700 }, 0.2);
  assert.deepEqual([r.sx, r.sw], [0, 1280], 'limitado à largura do vídeo');
  assert.ok(r.sy >= 0 && r.sy + r.sh <= 720);
  near(r.sy + r.aim.y, 55 / 1.125, 1); // a borda de cima do palco é a linha 48,9 do vídeo
  const p = Dev.roi(720, 1280, 390, 844, Dev.aimRect(390, 844, false), 0);
  const mp = Dev.coverMap(720, 1280, 390, 844);
  near(mp.scale, Math.max(390 / 720, 844 / 1280), 1e-9);
  near(p.sx + p.aim.x + p.aim.w / 2, 360, 1);
  near(p.sy + p.aim.y + p.aim.h / 2, 640, 1);
  assert.equal(Dev.roi(0, 0, 390, 844, Dev.aimRect(390, 844, false)), null, 'vídeo sem quadro');
});

test('código dentro do quadro: ≥ 90 % da caixa (cornerPoints ou boundingBox)', () => {
  const aim = { x: 100, y: 100, w: 200, h: 100 };
  const fora = { rawValue: 'FORA', boundingBox: { x: 0, y: 0, width: 80, height: 40 } };
  const borda = { rawValue: 'BORDA', boundingBox: { x: 250, y: 120, width: 100, height: 40 } }; // 50 % dentro
  const quase = { rawValue: 'QUASE', boundingBox: { x: 105, y: 95, width: 100, height: 100 } }; // 95 %
  const dentro = { rawValue: 'DENTRO', cornerPoints: [{ x: 150, y: 120 }, { x: 250, y: 120 }, { x: 250, y: 180 }, { x: 150, y: 180 }] };
  assert.equal(Dev.pickInside([fora, borda, dentro], aim).rawValue, 'DENTRO');
  assert.equal(Dev.pickInside([fora, quase], aim).rawValue, 'QUASE');
  assert.equal(Dev.pickInside([fora, borda], aim), null);
  assert.equal(Dev.pickInside([{ rawValue: 'SEM-POS' }], aim).rawValue, 'SEM-POS', 'sem posição: o recorte já limitou');
  // Linha de um código 1D (ZXing devolve dois pontos): 1 px de altura.
  assert.equal(Dev.pickInside([{ cornerPoints: [{ x: 120, y: 150 }, { x: 280, y: 150 }] }], aim) !== null, true);
  assert.equal(Dev.pickInside([{ cornerPoints: [{ x: 50, y: 150 }, { x: 280, y: 150 }] }], aim), null);
  near(Dev.insideFraction({ x: 0, y: 0, w: 10, h: 10 }, { x: 5, y: 0, w: 10, h: 10 }), 0.5, 1e-9);
  assert.deepEqual(Dev.cooldownList('0|1000|2000|x|99999'), [0, 1000, 2000]);
  assert.deepEqual(Dev.cooldownList(''), [0, 1000, 2000, 3000, 5000]);
  assert.equal(Dev.readerMode('Automatico'), 'automatico');
  assert.equal(Dev.readerMode('outro'), null);
});

// ---- Chromium com a CSP de app ------------------------------------------------------------
async function browser() {
  let pw;
  try {
    const req = createRequire(path.join(platform, 'tests-e2e', 'package.json'));
    pw = req('playwright');
  } catch (_) { return null; }
  try { return await pw.chromium.launch(); } catch (_) { return null; }
}

const CSP = "default-src 'self'; script-src https://hub.test/assets/; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src https://hub.test/_frag/coletor https://hub.test/_frag/coletor/; form-action https://hub.test/coletor https://hub.test/coletor/; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; require-trusted-types-for 'script'; trusted-types hub-parse hub-svg hub-script default";

const APP = `<div class="workspace" id="workspace"><section class="workspace-content" data-hub-feedback="beep">
<form id="f" method="post" action="/coletor/ler" data-hub-target="#painel" data-hub-offline-queue="Leitura"><input type="hidden" id="fmt" name="formato" value=""><input id="codigo" name="codigo"><button type="submit">ok</button></form>
<button id="scan" type="button" data-hub-scan data-hub-scan-target="#codigo" data-hub-scan-format-target="#fmt" data-hub-scan-continuous data-hub-scan-formats="ean_13,code_128|qr_code,data_matrix" data-hub-scan-modes="barras|qr" data-hub-scan-mode-labels="Código de barras|QR" data-hub-scan-mode="barras" data-hub-scan-reader="interativo" data-hub-scan-cooldown="0" data-hub-scan-cooldowns="0|1000|2000" data-hub-scan-prefs="/apps/coletor/escanear/prefs">ler</button>
<section id="painel"></section>
</section></div>`;

/** Vídeo falso 1280×720: três "códigos" coloridos — vermelho DENTRO do quadro (centro), azul
 *  FORA (acima do quadro, mas visível na tela) e verde na BORDA (metade para fora). O detector
 *  simulado acha os blocos pela cor NA IMAGEM QUE RECEBE e devolve a caixa de cada um. */
const CAMERA = `
  navigator.vibrate = () => true;
  window.AudioContext = undefined;
  const fake = () => { const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d');
    const draw = () => { g.fillStyle = '#808080'; g.fillRect(0, 0, 1280, 720);
      g.fillStyle = '#ff0000'; g.fillRect(600, 330, 80, 60);   // DENTRO (centro)
      g.fillStyle = '#0000ff'; g.fillRect(600, 60, 80, 60);    // FORA (acima da mira)
      g.fillStyle = '#00ff00'; g.fillRect(680, 440, 60, 60); }; // BORDA (base da mira ~468)
    draw(); setInterval(draw, 40);
    return c.captureStream(15); };
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => fake() } });
  window.__srcs = [];
  const COLORS = [['FORA', (r, g, b) => b > 200 && r < 60 && g < 60], ['BORDA', (r, g, b) => g > 200 && r < 60 && b < 60], ['DENTRO', (r, g, b) => r > 200 && g < 60 && b < 60]];
  window.BarcodeDetector = class {
    constructor(o) { this.f = (o && o.formats) || []; }
    static async getSupportedFormats() { return ['ean_13', 'code_128', 'qr_code', 'data_matrix']; }
    async detect(src) {
      const w = src.width, h = src.height;
      window.__srcs.push([w, h]);
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      const g = c.getContext('2d'); g.drawImage(src, 0, 0);
      const d = g.getImageData(0, 0, w, h).data;
      const out = [];
      for (const [name, is] of COLORS) {
        let x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
        for (let y = 0; y < h; y += 2) for (let x = 0; x < w; x += 2) { const i = (y * w + x) * 4; if (is(d[i], d[i + 1], d[i + 2])) { if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y; } }
        if (x1 >= 0) out.push({ rawValue: name, format: this.f[0], boundingBox: { x: x0, y: y0, width: x1 - x0 + 1, height: y1 - y0 + 1 } });
      }
      return out;
    } };
`;

async function appPage(b) {
  const ctx = await b.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const posts = [];
  const violations = [];
  page.on('console', (m) => { if (/Trusted Type|Content Security Policy|refused/i.test(m.text())) violations.push(m.text()); });
  await ctx.addInitScript(CAMERA);
  await page.route('https://hub.test/**', async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    const file = { '/assets/hub-safe.js': 'hub-safe.js', '/assets/hub-kit.js': 'hub-kit.js', '/assets/hub-device.js': 'hub-device.js', '/assets/hub.css': 'hub.css' }[u.pathname];
    if (file) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript; charset=utf-8', body: fs.readFileSync(path.join(assets, file)) });
    if (u.pathname === '/coletor/x') {
      return route.fulfill({ contentType: 'text/html; charset=utf-8', headers: { 'content-security-policy': CSP }, body: `<!doctype html><html><head><link rel="stylesheet" href="/assets/hub.css"></head><body><main id="app-root">${APP}</main><script src="/assets/hub-safe.js"></script><script src="/assets/hub-kit.js"></script><script data-hub-app="coletor" data-hub-frag="/_frag" src="/assets/hub-device.js"></script><script src="/assets/boot.js"></script></body></html>` });
    }
    if (u.pathname === '/assets/boot.js') return route.fulfill({ contentType: 'text/javascript', body: "window.HubKit.mount(document.getElementById('app-root'));" });
    if (u.pathname.startsWith('/_frag/coletor/') && req.method() === 'POST') {
      posts.push({ path: u.pathname, body: req.postData() || '' });
      if (u.pathname.endsWith('/prefs')) return route.fulfill({ status: 204, body: '' });
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: `<section id="painel"><div data-hub-feedback-result="ok">lido ${new URLSearchParams(req.postData() || '').get('codigo')}</div></section>` });
    }
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto('https://hub.test/coletor/x');
  await page.waitForFunction(() => window.HubDevice && window.HubKit);
  return { page, posts, violations };
}

test('Chromium (celular 390×844, vídeo 1280×720): só o código DENTRO do quadro é lido', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page, posts, violations } = await appPage(b);
    await page.click('#scan');
    await page.waitForSelector('.hub-camera[data-decoder="nativo"]');
    await page.waitForFunction(() => document.querySelector('.hub-camera')?.dataset.reads === '1', null, { timeout: 10_000 });
    await page.waitForTimeout(600); // mais quadros: nada além do DENTRO entra
    const reads = posts.filter((p) => p.path === '/_frag/coletor/ler').map((p) => new URLSearchParams(p.body).get('codigo'));
    assert.deepEqual([...new Set(reads)], ['DENTRO']);
    // O detector recebeu o recorte (não o quadro inteiro): bem menor que 1280×720.
    const srcs = await page.evaluate(() => window.__srcs);
    assert.ok(srcs.length > 0 && srcs.every(([w, h]) => w < 640 && h < 300), JSON.stringify(srcs.slice(0, 3)));
    const roi = (await page.getAttribute('.hub-camera', 'data-roi')).split(',').map(Number);
    assert.ok(roi[0] + roi[2] / 2 > 620 && roi[0] + roi[2] / 2 < 660, `recorte centrado no vídeo (${roi})`);
    assert.ok(roi[1] > 120 && roi[1] + roi[3] < 520, `fora do recorte: o azul (y 60–120) — ${roi}`);
    assert.deepEqual(violations, []);
  } finally { await b.close(); }
});

test('Chromium: tipo de código, Interativo/Automático e pausa ficam na barra da câmera e vão para o app', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const { page, posts, violations } = await appPage(b);
    await page.click('#scan');
    await page.waitForSelector('.hub-camera .hub-camera-top');
    const groups = await page.$$eval('.hub-camera-top [role="group"]', (gs) => gs.map((g) => [g.getAttribute('aria-label'), [...g.querySelectorAll('button')].map((x) => `${x.textContent}:${x.getAttribute('aria-pressed')}`)]));
    assert.deepEqual(groups, [['Tipo de código', ['Código de barras:true', 'QR:false']], ['Modo do leitor', ['Interativo:true', 'Automático:false']]]);
    assert.ok(await page.isHidden('.hub-camera-pause'), 'pausa só no Automático');
    // Alvos de toque grandes.
    const hs = await page.$$eval('.hub-camera-top button', (bs) => bs.map((x) => x.getBoundingClientRect().height));
    assert.ok(hs.every((h) => h >= 44), `alvos ≥ 44 px (${hs})`);
    const prefs = () => posts.filter((p) => p.path === '/_frag/coletor/escanear/prefs').map((p) => Object.fromEntries(new URLSearchParams(p.body)));
    await page.click('.hub-camera-readers [data-reader="automatico"]');
    await page.waitForSelector('.hub-camera-pause:not([hidden])');
    await page.selectOption('.hub-camera-pause-select', '2000');
    await page.click('.hub-camera-modes [data-mode="qr"]');
    await page.waitForFunction(() => document.querySelector('.hub-camera')?.dataset.mode === 'qr');
    const deadline = Date.now() + 5000;
    while (prefs().length < 3 && Date.now() < deadline) await page.waitForTimeout(50);
    assert.deepEqual(prefs(), [
      { modo: 'barras', leitor: 'automatico', pausa: '0' },
      { modo: 'barras', leitor: 'automatico', pausa: '2000' },
      { modo: 'qr', leitor: 'automatico', pausa: '2000' },
    ]);
    assert.ok((await page.getAttribute('.hub-camera-aim', 'class')).includes('square'));
    // Teclado: Esc fecha; reabrir mostra as escolhas (o botão guardou os valores).
    await page.keyboard.press('Escape');
    await page.waitForSelector('.hub-camera', { state: 'detached' });
    assert.equal(await page.getAttribute('#scan', 'data-hub-scan-reader'), 'automatico');
    await page.click('#scan');
    await page.waitForSelector('.hub-camera .hub-camera-top');
    assert.equal(await page.getAttribute('.hub-camera-modes [data-mode="qr"]', 'aria-pressed'), 'true');
    assert.equal(await page.getAttribute('.hub-camera-readers [data-reader="automatico"]', 'aria-pressed'), 'true');
    assert.equal(await page.inputValue('.hub-camera-pause-select'), '2000');
    assert.deepEqual(violations, []);
  } finally { await b.close(); }
});
