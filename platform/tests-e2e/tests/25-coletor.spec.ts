import { test, expect, chromium, type Page, type Browser } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { HUB, WORK_DIR, apiAs, json, openApp, stateFile } from './helpers';

// Wave 13 — Coletor app (Python/FastAPI: Scan, Test sheet with Live readings,
// Diagnostics) and the hub's device features (hub-device.js): Chromium's fake camera
// with a .y4m frame → the hub's ZXing; simulated BarcodeDetector (formats of the QR/barcode mode),
// keyboard-wedge reader, offline queue, diagnostics, sheet (decodable SVG) and PWA.

const EAN = '7890000000017'; // item 1 of the catalog (COL-0001)
const VENDOR = path.join(__dirname, '..', '..', 'assets', 'shell', 'vendor', 'zxing.min.js');

/** .y4m frame (640×480, 4:2:0) with the SVG drawn in the center — becomes Chromium's "camera". */
async function makeY4m(svg: string, file: string, bw = 520, bh = 300) {
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.setContent('<canvas id="c" width="640" height="480"></canvas>');
  const rgba: number[] = await p.evaluate(async ([markup, bw, bh]) => {
    const c = document.getElementById('c') as HTMLCanvasElement;
    const ctx = c.getContext('2d')!;
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, 640, 480);
    const img = new Image();
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(markup.replace(/ width="\d+" height="\d+"/, '').replace('<svg ', `<svg width="${bw}" height="${bh}" `));
    await img.decode();
    ctx.drawImage(img, (640 - bw) / 2, (480 - bh) / 2, bw, bh);
    return Array.from(ctx.getImageData(0, 0, 640, 480).data);
  }, [svg, bw, bh] as [string, number, number]);
  await b.close();
  const w = 640, h = 480;
  const y = Buffer.alloc(w * h);
  for (let i = 0; i < w * h; i++) y[i] = Math.round(0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2]);
  const uv = Buffer.alloc((w / 2) * (h / 2) * 2, 128);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, Buffer.concat([Buffer.from(`YUV4MPEG2 W${w} H${h} F10:1 Ip A1:1 C420jpeg\nFRAME\n`), y, uv]));
}

async function codeSvg(page: Page, fmt: string) {
  const html = await page.locator(`#app-root [data-sample-format="${fmt}"] svg`).evaluate((el) => el.outerHTML);
  return html.includes('xmlns=') ? html : html.replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ');
}

/** Browser with a fake camera showing `y4m`, already logged in as admin. */
async function cameraBrowser(y4m: string): Promise<{ b: Browser; page: Page }> {
  const b = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-video-capture=${y4m}`] });
  const ctx = await b.newContext({ storageState: stateFile('admin'), viewport: { width: 390, height: 844 }, locale: 'pt-BR', baseURL: HUB, permissions: ['camera'] });
  return { b, page: await ctx.newPage() };
}

/** Opens an app screen on the phone (the sidebar becomes the top-down menu). */
async function openMobile(page: Page, url: string) {
  await page.goto(url);
  await expect(page.locator('#app-root .workspace-content h1')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#app-root .app-loading')).toHaveCount(0, { timeout: 30_000 });
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 30_000 });
}

async function resetReads() {
  const api = await apiAs('admin');
  await api.post('/_frag/coletor/folha/limpar', { headers: { 'idempotency-key': `e2e-${Date.now()}-${Math.random()}` } });
  await api.post('/_frag/coletor/escanear/modo', { form: { modo: 'barras' }, headers: { 'idempotency-key': `e2e-m-${Date.now()}-${Math.random()}` } });
  // Automatic reader without a pause (physical reader): each different code goes in right away.
  await api.post('/_frag/coletor/sessao/cancelar', { form: { tela: 'folha' }, headers: { 'idempotency-key': `e2e-s-${Date.now()}-${Math.random()}` } });
  await api.post('/_frag/coletor/escanear/leitor', { form: { leitor: 'automatico', pausa: '0' }, headers: { 'idempotency-key': `e2e-l-${Date.now()}-${Math.random()}` } });
  await api.dispose();
}

/** Test sheet opened "on the computer" (another context, same user). */
async function openSheet(browser: Browser) {
  const ctx = await browser.newContext({ storageState: stateFile('admin'), baseURL: HUB, viewport: { width: 1440, height: 900 } });
  const sheet = await ctx.newPage();
  await openApp(sheet, '/apps/coletor/folha');
  await expect(sheet.locator('#live-reads')).toBeVisible();
  return { ctx, sheet };
}

/** Simulated camera (canvas) + simulated BarcodeDetector that stores the formats requested. */
function mockDetector(page: Page, reads: { rawValue: string; format: string }[]) {
  return page.addInitScript((list) => {
    (window as any).__fmts = [];
    let i = 0;
    (window as any).BarcodeDetector = class {
      constructor(o: any) { (window as any).__fmts.push((o && o.formats) || []); }
      static async getSupportedFormats() { return ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code', 'data_matrix']; }
      async detect() { const r = list[Math.min(i++, list.length - 1)]; return r ? [r] : []; }
    };
    const fake = () => { const c = document.createElement('canvas'); c.width = 64; c.height = 48; const g = c.getContext('2d')!; setInterval(() => { g.fillStyle = `#${Math.floor(Math.random() * 999)}`; g.fillRect(0, 0, 64, 48); }, 50); return (c as any).captureStream(10); };
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: async () => fake() }, configurable: true });
  }, reads);
}

test.describe('Coletor', () => {
  test.use({ storageState: stateFile('admin') });

  test('escanear com leitor tipo teclado: rajada + Enter registra, repetição ignorada, cartão e bipe; aparece ao vivo na folha', async ({ page, browser }) => {
    await resetReads();
    const { ctx, sheet } = await openSheet(browser);
    await page.addInitScript(() => {
      (window as any).__fb = [];
      document.addEventListener('hub:feedback', (e: any) => (window as any).__fb.push(e.detail.kind));
      (navigator as any).vibrate = (p: any) => { (window as any).__vib = p; return true; };
    });
    await openApp(page, '/apps/coletor/escanear');
    const field = page.locator('#codigo');
    await expect(field).toHaveAttribute('data-hub-wedge', '');
    await field.click();
    await page.keyboard.type(EAN, { delay: 5 });
    await page.keyboard.press('Enter');
    const card = page.locator('#scan-result [data-col-last]');
    await expect(card).toHaveAttribute('data-col-last', 'item');
    await expect(card).toContainText('coletor físico');
    await expect.poll(() => page.evaluate(() => (window as any).__fb)).toContain('ok');
    await page.keyboard.type(EAN, { delay: 5 });
    await page.keyboard.press('Enter');
    await expect(page.locator('.toast, #toast')).toContainText(/repetida/i);
    await page.keyboard.type('NADA-77', { delay: 5 });
    await page.keyboard.press('Enter');
    await expect(card).toHaveAttribute('data-col-last', 'desconhecido');
    await expect(page.locator('#scan-result .col-read')).toHaveCount(2);
    // Sheet on the computer: the two live readings (WebSocket), the newest first.
    await expect(sheet.locator('#live-reads [data-col-count]')).toHaveAttribute('data-col-count', '2', { timeout: 4_000 });
    await expect(sheet.locator('#live-reads .col-read').first()).toContainText('Código desconhecido');
    await expect(sheet.locator('#live-reads .col-read').nth(1)).toContainText(/Item: .*EAN-13/s);
    await sheet.locator('#live-reads button[type=submit]').click();
    await expect(sheet.locator('#live-reads [data-col-count]')).toHaveAttribute('data-col-count', '0');
    await ctx.close();
  });

  test('câmera falsa (ZXing do hub) no celular: EAN-13 lido aparece na folha do computador em ~2 s', async ({ browser }) => {
    test.setTimeout(240_000);
    await resetReads();
    const { ctx, sheet } = await openSheet(browser);
    const ean = await codeSvg(sheet, 'ean_13');
    const eanY4m = path.join(WORK_DIR, 'coletor-ean13.y4m');
    // Small enough to fit in the aiming frame (only it is read) on a phone held upright.
    await makeY4m(ean, eanY4m, 230, 120);
    const { b, page } = await cameraBrowser(eanY4m);
    try {
      await openMobile(page, '/apps/coletor/escanear');
      await page.locator('#app-root [data-hub-scan]').click();
      const cam = page.locator('.hub-camera');
      await expect(cam).toBeVisible();
      await expect(cam).toHaveAttribute('data-decoder', /zxing|nativo/);
      await expect(page.locator('.hub-camera-aim')).not.toHaveClass(/square/);
      await expect(cam).toHaveAttribute('data-reads', /[1-9]/, { timeout: 30_000 });
      await expect(page.locator('.hub-camera-last')).toContainText(/Item:/, { timeout: 20_000 });
      fs.mkdirSync(path.join(__dirname, '..', '..', 'dist'), { recursive: true });
      await page.screenshot({ path: path.join(__dirname, '..', '..', 'dist', 'coletor-scan-overlay.png') });
      await expect(sheet.locator('#live-reads .col-read').first()).toContainText(/EAN-13 · câmera/, { timeout: 4_000 });
      await page.locator('.hub-camera-close').click();
      await expect(cam).toHaveCount(0);
    } finally {
      await b.close();
      await ctx.close();
    }
  });

  test('QR / Código de barras: o detector recebe os formatos do modo, mira quadrada no QR, modo lembrado; QR no modo barras é ignorado com aviso', async ({ page }) => {
    await resetReads();
    await mockDetector(page, [{ rawValue: 'REC-1001', format: 'qr_code' }]);
    await openApp(page, '/apps/coletor/escanear');
    await page.locator('#app-root [data-hub-scan]').click();
    await expect(page.locator('.hub-camera-modes [data-mode="barras"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.hub-camera')).toBeVisible();
    await expect.poll(() => page.evaluate(() => (window as any).__fmts[0])).toEqual(['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf']);
    await expect(page.locator('.hub-camera-status')).toContainText('Modo código de barras — troque para QR', { timeout: 10_000 });
    await expect(page.locator('.hub-camera')).toHaveAttribute('data-wrong', /[1-9]/);
    await expect(page.locator('.hub-camera')).not.toHaveAttribute('data-reads', /[1-9]/);
    // Switches to QR inside the camera: the app records it (prefs) and the button keeps the mode.
    const saved = page.waitForResponse((r) => r.url().includes('/escanear/prefs') && r.status() === 204);
    await page.locator('.hub-camera-modes [data-mode="qr"]').click();
    await saved;
    await page.locator('.hub-camera-close').click();
    await expect(page.locator('#app-root [data-hub-scan]')).toHaveAttribute('data-hub-scan-mode', 'qr');
    await expect(page.locator('#app-root [data-hub-scan]')).toHaveAttribute('data-hub-scan-formats', 'ean_13,ean_8,upc_a,upc_e,code_128,code_39,itf|qr_code,data_matrix');
    await page.locator('#app-root [data-hub-scan]').click();
    await expect(page.locator('.hub-camera-aim')).toHaveClass(/hub-camera-aim-square/);
    // The last reader created is the warning one (formats of the other mode); the main one comes first.
    await expect.poll(() => page.evaluate(() => JSON.stringify((window as any).__fmts.slice(-2)))).toBe(JSON.stringify([['qr_code', 'data_matrix'], ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf']]));
    await expect(page.locator('#scan-result [data-col-last]')).toHaveAttribute('data-col-last', 'pedido', { timeout: 10_000 });
    await expect(page.locator('#scan-result [data-col-last]')).toContainText('QR Code · câmera');
    await page.locator('.hub-camera-close').click();
    // Remembered per user: reloading keeps QR; the reader's field stays the same.
    await page.reload();
    await expect(page.locator('#app-root [data-hub-scan]')).toHaveAttribute('data-hub-scan-mode', 'qr', { timeout: 30_000 });
    await expect(page.locator('#codigo')).toHaveAttribute('data-hub-wedge', '');
  });

  test('Escanear: tipo de código e modo do leitor só dentro da câmera; as trocas ficam lembradas', async ({ page }) => {
    await resetReads(); // barcode + Automatic without a pause
    await mockDetector(page, []);
    await openApp(page, '/apps/coletor/escanear');
    const root = page.locator('#app-root');
    await expect(root.locator('[data-hub-scan]')).toBeVisible();
    // Before opening the camera: no selector on the page.
    await expect(root.locator('[role="group"], .col-seg, select#pausa, #scan-mode-field')).toHaveCount(0);
    await expect(root.getByRole('button', { name: /^(QR|Código de barras|Interativo|Automático)$/ })).toHaveCount(0);
    await root.locator('[data-hub-scan]').click();
    const top = page.locator('.hub-camera .hub-camera-top');
    await expect(top).toBeVisible();
    await expect(top.getByRole('group', { name: 'Tipo de código' }).getByRole('button', { name: 'Código de barras' })).toHaveAttribute('aria-pressed', 'true');
    await expect(top.getByRole('group', { name: 'Modo do leitor' }).getByRole('button', { name: 'Automático' })).toHaveAttribute('aria-pressed', 'true');
    await expect(top.getByLabel('Pausa')).toHaveValue('0');
    // Switches everything inside the camera (each switch goes to the app).
    let saved = page.waitForResponse((r) => r.url().includes('/escanear/prefs') && r.status() === 204);
    await top.getByLabel('Pausa').selectOption('3000');
    await saved;
    saved = page.waitForResponse((r) => r.url().includes('/escanear/prefs') && r.status() === 204);
    await top.getByRole('button', { name: 'Interativo' }).click();
    await saved;
    await expect(top.getByLabel('Pausa')).toBeHidden();
    saved = page.waitForResponse((r) => r.url().includes('/escanear/prefs') && r.status() === 204);
    await top.getByRole('button', { name: 'QR' }).click();
    await saved;
    await expect(page.locator('.hub-camera-aim')).toHaveClass(/hub-camera-aim-square/);
    await page.keyboard.press('Escape');
    await expect(page.locator('.hub-camera')).toHaveCount(0);
    // Reopen and reload: the choices persist.
    for (const step of ['reabrir', 'recarregar']) {
      if (step === 'recarregar') { await page.reload(); await expect(root.locator('[data-hub-scan]')).toHaveAttribute('data-hub-scan-reader', 'interativo', { timeout: 30_000 }); }
      await root.locator('[data-hub-scan]').click();
      await expect(top.getByRole('button', { name: 'QR' }), step).toHaveAttribute('aria-pressed', 'true');
      await expect(top.getByRole('button', { name: 'Interativo' }), step).toHaveAttribute('aria-pressed', 'true');
      await top.getByRole('button', { name: 'Automático' }).click();
      await expect(top.getByLabel('Pausa'), step).toHaveValue('3000');
      await top.getByRole('button', { name: 'Interativo' }).click();
      await page.locator('.hub-camera-close').click();
    }
    await resetReads();
  });

  test('fila offline: sem conexão as leituras ficam no aparelho e, ao reconectar, vão uma vez só e aparecem ao vivo na folha', async ({ page, context, browser }) => {
    await resetReads();
    const { ctx, sheet } = await openSheet(browser);
    await openApp(page, '/apps/coletor/escanear');
    await context.setOffline(true);
    for (const c of ['COL-0005', 'LOC-A02', 'COL-0006']) {
      await page.locator('#codigo').fill(c);
      await page.locator('#leitura button[type=submit]').click();
    }
    const status = page.locator('#app-root [data-hub-offline-status]');
    await expect(status).toHaveAttribute('data-pending', '3');
    await expect(sheet.locator('#live-reads [data-col-count]')).toHaveAttribute('data-col-count', '0');
    await context.setOffline(false);
    await expect(status).toHaveAttribute('data-pending', '0', { timeout: 30_000 });
    await expect(sheet.locator('#live-reads [data-col-count]')).toHaveAttribute('data-col-count', '3', { timeout: 6_000 });
    await expect(sheet.locator('#live-reads .col-read').first()).toContainText('COL-0006');
    await expect(sheet.locator('#live-reads')).toContainText('Local: LOC-A02');
    await ctx.close();
  });

  test('link (QR do app) lido no Escanear: "Link para o app — abre o Coletor", aviso e não "desconhecido"', async ({ page }) => {
    await openApp(page, '/apps/coletor/escanear');
    await page.locator('#codigo').fill('https://hub.example.com/apps/coletor');
    await page.locator('#leitura button[type=submit]').click();
    const card = page.locator('#scan-result [data-col-last]');
    await expect(card).toHaveAttribute('data-col-last', 'link');
    await expect(card).toContainText('Link para o app — abre o Coletor');
    await expect(card).toHaveAttribute('data-hub-feedback-result', 'warn');
  });

  test('folha de teste: QR do app à parte, depois Itens, Locais, Pedidos e Formatos; leituras ao vivo ao lado (≥1024) ou abaixo (celular)', async ({ page }) => {
    await openApp(page, '/apps/coletor/folha');
    const steps = await page.locator('#app-root [data-col-step]').evaluateAll((els) => els.map((e) => e.getAttribute('data-col-step')));
    expect(steps).toEqual(['itens', 'locais', 'pedidos', 'formatos']);
    const box = page.locator('#app-root [data-col-open-app]');
    if (await box.count()) {
      await expect(box).toContainText('Não é um código de teste');
      await expect(box).toContainText('/apps/coletor');
    }
    await expect(page.locator('#col-folha-live')).toHaveAttribute('data-hub-ws', '/apps/coletor/ws/live?view=folha');
    await expect(page.locator('#col-folha-live')).toHaveAttribute('data-hub-ws-state', 'open');
    await expect(page.locator('#app-root [data-col-start]')).toBeVisible();
    const sheetBox = await page.locator('.col-folha-sheet').boundingBox();
    const liveBox = await page.locator('.col-folha-live').boundingBox();
    expect(liveBox!.x).toBeGreaterThan(sheetBox!.x + sheetBox!.width - 5);
    await page.setViewportSize({ width: 390, height: 844 });
    const s2 = await page.locator('.col-folha-sheet').boundingBox();
    const l2 = await page.locator('.col-folha-live').boundingBox();
    expect(l2!.y).toBeGreaterThan(s2!.y + s2!.height - 5);
  });

  test('diagnóstico: contexto seguro, rede, armazenamento e leitor; relatório copiado', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await openApp(page, '/apps/coletor/diagnostico');
    const st = (c: string) => page.locator(`#app-root [data-hub-diag-check="${c}"] [data-hub-diag-status]`);
    await expect(st('secure')).toHaveAttribute('data-diag-state', 'ok');
    await expect(st('online')).toHaveAttribute('data-diag-state', 'ok');
    await expect(st('storage')).toHaveAttribute('data-diag-state', 'ok');
    await page.locator('#diag-leitor').click();
    await page.keyboard.type('COL-0042', { delay: 5 });
    await page.keyboard.press('Enter');
    await expect(st('wedge')).toHaveAttribute('data-diag-state', 'ok');
    await expect(st('wedge')).toContainText('COL-0042');
    await page.locator('#app-root [data-hub-diag-check="beep"] [data-hub-diag-run]').click();
    await expect(st('beep')).not.toHaveAttribute('data-diag-state', 'pending');
    await page.locator('#app-root [data-hub-diag-copy]').click();
    const text = await page.evaluate(() => navigator.clipboard.readText());
    expect(text).toContain('COL-0042');
    expect(text).toContain('Navegador:');
  });

  test('folha: todos os formatos em SVG e um código decodificado de volta (ZXing)', async ({ page, browser }) => {
    await openApp(page, '/apps/coletor/folha');
    for (const f of ['ean_13', 'ean_8', 'upc_a', 'code_128', 'code_39', 'qr_code', 'data_matrix']) {
      await expect(page.locator(`#app-root [data-sample-format="${f}"] svg`)).toBeVisible();
    }
    await expect(page.locator('#app-root [data-hub-print]')).toBeVisible();
    const svgs: Record<string, string> = {};
    for (const f of ['ean_13', 'code_128', 'qr_code', 'data_matrix']) svgs[f] = await codeSvg(page, f);
    // Blank page (without the hub's CSP) with the same decoder the hub serves.
    const p = await browser.newPage();
    await p.setContent('<canvas id="c" width="900" height="500"></canvas>');
    await p.addScriptTag({ path: VENDOR });
    // Same method as the hub (ZXing in pure JS, no WebAssembly): reading sizes on a
    // computer screen; tries the scales the hub uses in rotation.
    const out = await p.evaluate(async (all) => {
      const Z = (window as any).ZXing;
      const res: Record<string, string> = {};
      const c = document.getElementById('c') as HTMLCanvasElement;
      const d = document.createElement('canvas');
      for (const [f, markup] of Object.entries(all)) {
        const ctx = c.getContext('2d')!;
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 900, 500);
        const w = f === 'qr_code' ? 360 : f === 'data_matrix' ? 460 : 700;
        const h = f === 'qr_code' ? 360 : f === 'data_matrix' ? 460 : 300;
        const img = new Image();
        img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent((markup as string).replace(/ width="\d+" height="\d+"/, '').replace('<svg ', `<svg width="${w}" height="${h}" `));
        await img.decode();
        ctx.drawImage(img, (900 - w) / 2, (500 - h) / 2, w, h);
        res[f] = 'ERRO';
        for (const s of [1, 0.6, 0.4]) {
          const W = Math.round(900 * s), H = Math.round(500 * s);
          d.width = W; d.height = H;
          const x = d.getContext('2d')!;
          x.drawImage(c, 0, 0, W, H);
          const px = x.getImageData(0, 0, W, H).data;
          const lum = new Uint8ClampedArray(W * H);
          for (let i = 0, j = 0; j < lum.length; i += 4, j++) lum[j] = (px[i] * 299 + px[i + 1] * 587 + px[i + 2] * 114) / 1000;
          try { res[f] = new Z.MultiFormatReader().decode(new Z.BinaryBitmap(new Z.HybridBinarizer(new Z.RGBLuminanceSource(lum, W, H))), new Map([[Z.DecodeHintType.TRY_HARDER, true]])).getText(); break; } catch { /* próxima escala */ }
        }
      }
      return res;
    }, svgs);
    await p.close();
    expect(out.ean_13).toBe(EAN);
    expect(out.code_128).toBe('COL-0002');
    expect(out.qr_code).toBe('REC-1001');
    expect(out.data_matrix).toBe('COL-0003');
  });

  test('PWA: manifesto (atalho do Coletor, start_url por app) e service worker do hub registrado', async ({ page, request }) => {
    const m = await json(await request.get('/manifest.webmanifest?start=/apps/coletor'));
    expect(m.name).toBeTruthy();
    expect(m.display).toBe('standalone');
    expect(m.start_url).toBe('/apps/coletor');
    expect(m.theme_color).toBe('#0a2848');
    expect(m.shortcuts[0].url).toBe('/apps/coletor');
    expect(m.icons.map((i: any) => i.sizes)).toEqual(expect.arrayContaining(['192x192', '512x512']));
    expect((await json(await request.get('/manifest.webmanifest?start=https://evil.example'))).start_url).toBe('/');
    const sw = await request.get('/hub-sw.js');
    expect(sw.headers()['service-worker-allowed']).toBe('/');
    expect(await (await request.get('/offline')).text()).toContain('hub-offline-page');
    await openApp(page, '/apps/coletor/diagnostico');
    await expect(page.locator('link[rel=manifest]')).toHaveAttribute('href', '/manifest.webmanifest?start=/apps/coletor');
    const scope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope);
    expect(scope).toBe(`${HUB}/`);
    const perm = (await request.get('/apps/coletor/diagnostico')).headers()['permissions-policy'];
    expect(perm).toContain('camera=(self)');
  });
});
