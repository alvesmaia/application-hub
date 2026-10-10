import { test, expect, type Browser, type BrowserContext, type Page } from '@playwright/test';
import { HUB, apiAs, openApp, stateFile } from './helpers';

// Onda 13 — Coletor: sessão de teste em FILA (Folha no computador, um código por vez, ordem
// sorteada) sincronizada AO VIVO por WebSocket com o celular (Escanear), modos do leitor
// (Interativo / Automático com pausa e descarte de repetidas) e alternância Código de barras |
// QR dentro da câmera. Duas páginas do mesmo usuário: computador (1440) e celular (390) com a
// câmera e o BarcodeDetector simulados — o código "lido" é o que o teste empurra.

const idem = () => ({ 'idempotency-key': `e2e-f-${Date.now()}-${Math.random()}` });

async function reset(reader: 'interativo' | 'automatico' = 'automatico', pausa = '0') {
  const api = await apiAs('admin');
  await api.post('/_frag/coletor/sessao/cancelar', { form: { tela: 'folha' }, headers: idem() });
  await api.post('/_frag/coletor/folha/limpar', { headers: idem() });
  await api.post('/_frag/coletor/escanear/modo', { form: { modo: 'barras' }, headers: idem() });
  await api.post('/_frag/coletor/escanear/leitor', { form: { leitor: reader, pausa }, headers: idem() });
  await api.post('/_frag/coletor/escanear/proximo', { headers: idem() });
  await api.dispose();
}

/** Câmera falsa + BarcodeDetector que devolve o que estiver em window.__codes, sempre com um
 *  formato do modo atual (o hub descarta formato de outro modo). */
const PHONE_MOCKS = () => {
  (window as any).__codes = [];
  (window as any).__fmts = [];
  (window as any).BarcodeDetector = class {
    f: string[];
    constructor(o: any) { this.f = (o && o.formats) || []; (window as any).__fmts.push(this.f); }
    static async getSupportedFormats() { return ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code', 'data_matrix']; }
    async detect() {
      await new Promise((r) => setTimeout(r, 20));
      const q = (window as any).__codes;
      // Só o leitor do modo atual responde (o de aviso, com os formatos do outro modo, não).
      if (!q.length || document.querySelector<HTMLElement>('.hub-camera')?.dataset.formats !== this.f.join(',')) return [];
      return [{ rawValue: q.shift(), format: this.f[0] }];
    }
  };
  const fake = () => { const c = document.createElement('canvas'); c.width = 64; c.height = 48; const g = c.getContext('2d')!; setInterval(() => { g.fillStyle = `#${Math.floor(Math.random() * 999)}`; g.fillRect(0, 0, 64, 48); }, 50); return (c as any).captureStream(10); };
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: async () => fake() }, configurable: true });
  (navigator as any).vibrate = () => true;
};

async function desktop(browser: Browser, opts: { blockWs?: boolean } = {}): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({ storageState: stateFile('admin'), baseURL: HUB, viewport: { width: 1440, height: 900 }, locale: 'pt-BR' });
  const page = await ctx.newPage();
  if (opts.blockWs) await page.routeWebSocket(/\/apps\/coletor\/ws\//, (ws) => ws.close({ code: 1011 }));
  await openApp(page, '/apps/coletor/folha');
  await expect(page.locator('#col-folha-live')).toHaveAttribute('data-hub-ws-state', opts.blockWs ? 'polling' : 'open', { timeout: 15_000 });
  return { ctx, page };
}

async function phone(browser: Browser): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({ storageState: stateFile('admin'), baseURL: HUB, viewport: { width: 390, height: 844 }, locale: 'pt-BR', hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  await page.addInitScript(PHONE_MOCKS);
  await page.goto('/apps/coletor/escanear');
  await expect(page.locator('#app-root .workspace-content h1')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#app-root .component-loading')).toHaveCount(0, { timeout: 30_000 });
  await expect(page.locator('#col-phone-live')).toHaveAttribute('data-hub-ws-state', 'open', { timeout: 15_000 });
  return { ctx, page };
}

const queue = (p: Page) => p.locator('#app-root [data-col-queue]');
const expected = async (p: Page) => (await queue(p).getAttribute('data-col-expected'))!;

async function startSession(p: Page, auto = true) {
  const box = p.locator('#app-root [data-col-start] input[name=auto]');
  if (auto) await box.check(); else await box.uncheck();
  await p.locator('#app-root [data-col-action="iniciar"]').click();
  await expect(queue(p)).toHaveAttribute('data-col-pos', '1');
  await expect(p.locator('#app-root [data-col-progress]')).toHaveText('1 de 24');
}

async function openCamera(p: Page) {
  await p.locator('#app-root [data-hub-scan]').click();
  await expect(p.locator('.hub-camera')).toHaveAttribute('data-decoder', 'nativo');
}

/** Garante o modo da câmera certo para o formato esperado (troca DENTRO da câmera). */
async function aimFor(p: Page, fmt: string) {
  const want = ['qr_code', 'data_matrix'].includes(fmt) ? 'qr' : 'barras';
  const cam = p.locator('.hub-camera');
  if ((await cam.getAttribute('data-mode')) !== want) {
    await p.locator(`.hub-camera-mode[data-mode="${want}"]`).click();
    await expect(cam).toHaveAttribute('data-mode', want);
  }
}

async function scan(p: Page, value: string) {
  await p.evaluate((v) => (window as any).__codes.push(v), value);
}

test.describe('Coletor — fila ao vivo (WebSocket)', () => {
  test.describe.configure({ mode: 'serial' });

  test('@core avanço automático na leitura certa (latência < 1 s pelo socket); erro não avança; alternância na câmera', async ({ browser }) => {
    await reset('automatico', '0');
    const d = await desktop(browser);
    const m = await phone(browser);
    try {
      await startSession(d.page, true);
      // O celular recebe a sessão pelo socket (posição e código esperado).
      const sess = m.page.locator('#col-session');
      await expect(sess).toHaveAttribute('data-col-pos', '1');
      const first = await expected(d.page);
      await expect(sess).toHaveAttribute('data-col-expected', first);
      await openCamera(m.page);
      await aimFor(m.page, (await queue(d.page).locator('[data-col-current]').getAttribute('data-col-format'))!);
      // Leitura certa: o computador avança sozinho; medimos da resposta no celular até a tela.
      const resp = m.page.waitForResponse((r) => r.url().includes('/_frag/coletor/escanear/ler'));
      await scan(m.page, first);
      await resp;
      const t0 = Date.now();
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '2', { timeout: 5_000 });
      const latency = Date.now() - t0;
      expect(latency, `atualização ao vivo em ${latency} ms`).toBeLessThan(1000);
      await expect(m.page.locator('#scan-result [data-col-outcome]')).toHaveAttribute('data-col-outcome', 'ok');
      await expect(sess).toHaveAttribute('data-col-pos', '2');
      // Leitura errada: erro nas duas telas, não avança.
      const second = await expected(d.page);
      await aimFor(m.page, (await queue(d.page).locator('[data-col-current]').getAttribute('data-col-format'))!);
      await scan(m.page, 'NADA-404');
      await expect(d.page.locator('#app-root [data-col-flag="erro"]')).toContainText('NADA-404');
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '2');
      await expect(m.page.locator('#scan-result [data-col-outcome]')).toHaveAttribute('data-col-outcome', 'erro');
      await expect(m.page.locator('#col-session [data-col-flag="erro"]')).toBeVisible();
      await scan(m.page, second);
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '3');
      await expect(d.page.locator('#app-root [data-col-result="erro"]')).toHaveCount(1);
      await expect(d.page.locator('#app-root [data-col-result="ok"]')).toHaveCount(2);
      // A troca de modo feita dentro da câmera foi lembrada pelo app.
      await m.page.locator('.hub-camera-close').click();
      const mode = await m.page.locator('#app-root [data-hub-scan]').getAttribute('data-hub-scan-mode');
      expect(['barras', 'qr']).toContain(mode);
    } finally {
      await d.ctx.close();
      await m.ctx.close();
    }
  });

  test('modo Interativo: depois de uma leitura a câmera espera "Próximo"', async ({ browser }) => {
    await reset('interativo');
    const d = await desktop(browser);
    const m = await phone(browser);
    try {
      await startSession(d.page, false);
      await expect(m.page.locator('#app-root [data-hub-scan]')).toHaveAttribute('data-hub-scan-reader', 'interativo');
      const first = await expected(d.page);
      await openCamera(m.page);
      await aimFor(m.page, (await queue(d.page).locator('[data-col-current]').getAttribute('data-col-format'))!);
      await scan(m.page, first);
      // Sem avanço automático: marcado como certo, esperando Próximo.
      await expect(d.page.locator('#app-root [data-col-flag="ok"]')).toBeVisible();
      await expect(m.page.locator('.hub-camera[data-held] .hub-camera-next')).toBeVisible();
      // Outra leitura com a câmera parada: nada entra.
      await scan(m.page, 'COL-0099');
      await m.page.waitForTimeout(800);
      await expect(m.page.locator('.hub-camera')).toHaveAttribute('data-reads', '1');
      await m.page.locator('.hub-camera-next').click();
      await expect(m.page.locator('.hub-camera[data-held]')).toHaveCount(0);
      await expect(m.page.locator('.hub-camera')).toHaveAttribute('data-reads', '2', { timeout: 5_000 }); // a COL-0099 que esperava
      await expect(m.page.locator('.hub-camera[data-held]')).toHaveCount(1);
      // Próximo da sessão no celular: o computador vai para o 2º.
      await m.page.locator('.hub-camera-next').click();
      await m.page.locator('.hub-camera-close').click();
      await m.page.locator('#col-session [data-col-action="proximo"]').click();
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '2');
    } finally {
      await d.ctx.close();
      await m.ctx.close();
    }
  });

  test('modo Automático: pausa entre leituras e o mesmo código em 5 s é ignorado', async ({ browser }) => {
    await reset('automatico', '2000');
    const d = await desktop(browser);
    const m = await phone(browser);
    try {
      await startSession(d.page, true);
      await expect(m.page.locator('#app-root [data-hub-scan]')).toHaveAttribute('data-hub-scan-cooldown', '2000');
      const first = await expected(d.page);
      await openCamera(m.page);
      await aimFor(m.page, (await queue(d.page).locator('[data-col-current]').getAttribute('data-col-format'))!);
      await scan(m.page, first);
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '2');
      await expect(m.page.locator('.hub-camera')).toHaveAttribute('data-cooldown', '2000');
      const t0 = Date.now();
      // O mesmo código ainda na frente da câmera: descartado (não vira erro no 2º).
      await scan(m.page, first);
      const second = await expected(d.page);
      await aimFor(m.page, (await queue(d.page).locator('[data-col-current]').getAttribute('data-col-format'))!);
      await scan(m.page, second);
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '3', { timeout: 8_000 });
      expect(Date.now() - t0, 'esperou a pausa de 2 s').toBeGreaterThanOrEqual(1500);
      await expect(d.page.locator('#app-root [data-col-result="erro"]')).toHaveCount(0);
      await expect(m.page.locator('.hub-camera')).toHaveAttribute('data-reads', '2');
    } finally {
      await d.ctx.close();
      await m.ctx.close();
    }
  });

  test('Parar / Continuar e Cancelar valem nas duas telas', async ({ browser }) => {
    await reset('automatico', '0');
    const d = await desktop(browser);
    const m = await phone(browser);
    try {
      await startSession(d.page, true);
      await d.page.locator('#app-root [data-col-action="parar"]').click();
      await expect(queue(d.page)).toHaveAttribute('data-col-status', 'paused');
      await expect(m.page.locator('#col-session')).toHaveAttribute('data-col-session', 'paused');
      // Parada: leitura não conta (nem como erro).
      await m.page.locator('#codigo').fill('COL-0099');
      await m.page.locator('#leitura button[type=submit]').click();
      await expect(m.page.locator('#scan-result [data-col-last]')).toContainText('COL-0099');
      await expect(m.page.locator('#scan-result [data-col-outcome]')).toHaveCount(0);
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '1');
      await expect(d.page.locator('#app-root [data-col-result]')).toHaveCount(0);
      // Continuar pelo celular.
      await m.page.locator('#col-session [data-col-action="continuar"]').click();
      await expect(queue(d.page)).toHaveAttribute('data-col-status', 'running');
      await m.page.locator('#codigo').fill(await expected(d.page));
      await m.page.locator('#leitura button[type=submit]').click();
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '2');
      // Cancelar: volta ao início (QR do app de novo) e o painel do celular some.
      await d.page.locator('#app-root [data-col-action="cancelar"]').click();
      await expect(d.page.locator('#app-root [data-col-start]')).toBeVisible();
      await expect(queue(d.page)).toHaveCount(0);
      await expect(m.page.locator('#col-session')).toHaveAttribute('data-col-session', 'cancelled');
      await expect(m.page.locator('#col-session')).toBeHidden();
    } finally {
      await d.ctx.close();
      await m.ctx.close();
    }
  });

  test('Reiniciar pede confirmação (modal) e sorteia outra ordem', async ({ browser }) => {
    await reset('automatico', '0');
    const d = await desktop(browser);
    try {
      await startSession(d.page, true);
      const before = await queue(d.page).getAttribute('data-col-order');
      await d.page.locator('#app-root [data-col-action="proximo"]').click();
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '2');
      await d.page.locator('#app-root [data-col-action="reiniciar"]').click();
      const modal = d.page.locator('.hub-confirm');
      await expect(modal).toBeVisible();
      await expect(modal).toContainText('Reiniciar a sessão?');
      await modal.locator('[data-hub-close]').click();
      await expect(modal).toHaveCount(0);
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '2');
      await d.page.locator('#app-root [data-col-action="reiniciar"]').click();
      await d.page.locator('.hub-confirm [data-hub-confirm-ok]').click();
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '1');
      await expect(d.page.locator('#app-root [data-col-result]')).toHaveCount(0);
      const after = await queue(d.page).getAttribute('data-col-order');
      expect(after!.split(',').sort()).toEqual(before!.split(',').sort());
      expect(after, '24 códigos: mesma ordem tem probabilidade 1/24!').not.toBe(before);
    } finally {
      await d.ctx.close();
    }
  });

  test('WebSocket bloqueado: a Folha cai para o polling (2 s) e continua atualizando', async ({ browser }) => {
    await reset('automatico', '0');
    const d = await desktop(browser, { blockWs: true });
    const m = await phone(browser);
    try {
      await startSession(d.page, true);
      await m.page.locator('#codigo').fill(await expected(d.page));
      await m.page.locator('#leitura button[type=submit]').click();
      await expect(queue(d.page)).toHaveAttribute('data-col-pos', '2', { timeout: 6_000 });
      await expect(d.page.locator('#col-folha-live')).toHaveAttribute('data-hub-ws-state', 'polling');
    } finally {
      await d.ctx.close();
      await m.ctx.close();
      await reset('automatico', '0');
    }
  });
});
