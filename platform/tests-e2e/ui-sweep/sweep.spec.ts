import { test, type Page } from '@playwright/test';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { stateFile, type Role } from '../tests/helpers';
import { CREDENTIAL_PAGES, FINDINGS_DIR, HUB, ROUTES_FILE, SHOTS_DIR, type Finding, type Theme } from './common';

/**
 * Varredura automática de UI (@sweep): cada unidade = papel × tema × viewport percorre
 * todas as rotas descobertas no setup, roda as verificações de `checks.js` na página e em
 * cada camada aberta (menus, popovers, modais) e grava os achados em
 * `<SWEEP_OUT>/findings/<unidade>.json`. O `run.mjs` junta, aplica a allowlist e decide.
 * Nada além de GET sai do navegador (exceto analytics de uso): a varredura nunca muda estado.
 */
type Unit = { role: Role | 'anon'; theme: Theme; vw: number; vh: number; interact: boolean; focus: boolean; rail?: 'collapsed' | 'expanded'; only?: RegExp };
const QUICK = process.env.SWEEP_MODE === 'quick';
// Celular (390×844): shell (gaveta), Início, Usuários (cartões) e um app.
const MOBILE_UNITS: Unit[] = (['light', 'dark'] as Theme[]).map((theme) => (
  { role: 'admin' as Role, theme, vw: 390, vh: 844, interact: true, focus: false, only: /^\/(|admin\/users|ativos)$/ }));
// Visitante sem sessão: só a documentação pública (/developers/*), shell mínimo (header sem rail).
const DOCS = /^\/developers(\/|$)/;
const ANON_UNITS: Unit[] = (['light', 'os-dark', 'dark'] as Theme[]).flatMap((theme) => [
  { role: 'anon' as const, theme, vw: 1440, vh: 900, interact: true, focus: theme === 'light', only: DOCS },
  { role: 'anon' as const, theme, vw: 390, vh: 844, interact: true, focus: false, only: DOCS }]);
const UNITS: Unit[] = QUICK
  ? [
      { role: 'admin', theme: 'light', vw: 1440, vh: 900, interact: true, focus: true },
      { role: 'admin', theme: 'dark', vw: 1440, vh: 900, interact: true, focus: false },
      ...MOBILE_UNITS, ...ANON_UNITS,
    ]
  : [
      ...(['admin', 'operador', 'leitor'] as Role[]).flatMap((role) => (['light', 'dark', 'os-dark'] as Theme[]).map((theme) => (
        { role, theme, vw: 1440, vh: 900, interact: true, focus: theme === 'light' }))),
      { role: 'admin', theme: 'auto', vw: 1440, vh: 900, interact: false, focus: false },
      { role: 'admin', theme: 'light', vw: 1280, vh: 720, interact: false, focus: false },
      { role: 'leitor', theme: 'dark', vw: 1280, vh: 720, interact: false, focus: false },
      // Rail da plataforma: recolhido (dentro e fora dos apps) e expandido dentro dos apps.
      { role: 'admin', theme: 'light', vw: 1280, vh: 720, interact: true, focus: false, rail: 'collapsed' },
      { role: 'admin', theme: 'dark', vw: 1280, vh: 720, interact: false, focus: false, rail: 'expanded' },
      ...MOBILE_UNITS, ...ANON_UNITS,
    ];

const CHECKS_JS = fs.readFileSync(path.join(__dirname, 'checks.js'), 'utf8');
const MAX_TRIGGERS = Number(process.env.SWEEP_MAX_TRIGGERS || 10);
const MAX_SHOTS_PER_PAGE = 20;
const ALLOWED_WRITES = /^\/api\/me\/analytics/; // telemetria de uso — inofensiva
const DANGER = /excluir|remover|apagar|revogar|parar|reiniciar|restaurar|desativar|desinstalar|sair|encerrar|descartar|rotacionar|bloquear|delete|remove|revoke|logout|promover|implantar|reverter|rollback/i;
const TRIGGER_TEXT = /^(\+\s*)?(nov[oa]s?\b|personalizar|editar|adicionar|filtros?\b|mais\b|ações|⋯|…|\.\.\.|convidar|criar|conectar|configurar|solicitar|provisionar)/i;
const LAYERS = 'dialog[open], [role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [popover], .popover, .menu-pop, .dropdown-menu, [data-hub-popover], [data-hub-menu-panel], .modal, .hub-modal, details[open]';

async function settle(page: Page) {
  await page.waitForLoadState('load', { timeout: 15_000 }).catch(() => undefined);
  await page.waitForFunction(() => !document.querySelector('.app-loading, .component-loading, [aria-busy="true"], .skeleton, .app-skeleton'), null, { timeout: 8_000 }).catch(() => undefined);
  await page.waitForTimeout(250);
}

for (const u of UNITS) {
  const unit = `${u.role}-${u.theme}-${u.vw}${u.rail ? `-rail-${u.rail}` : ""}`;
  test(`@sweep ${unit}`, async ({ browser }) => {
    test.setTimeout(30 * 60_000);
    const { routes } = JSON.parse(fs.readFileSync(ROUTES_FILE, 'utf8')) as { routes: { pattern: string; path: string }[] };
    fs.mkdirSync(FINDINGS_DIR, { recursive: true });
    fs.mkdirSync(SHOTS_DIR, { recursive: true });
    const ctx = await browser.newContext({
      storageState: u.role === 'anon' ? undefined : stateFile(u.role), viewport: { width: u.vw, height: u.vh }, locale: 'pt-BR',
      colorScheme: u.theme === 'os-dark' || u.theme === 'auto' ? 'dark' : 'light',
    });
    // Preferência de tema do próprio hub (`<html data-theme>`, que o servidor escreve a partir
    // de /api/me/prefs): forçada no cliente para não mexer na preferência gravada do usuário
    // compartilhado entre os workers. light/os-dark NÃO forçam: verificam o padrão claro que o
    // servidor escreve (os-dark = SO escuro, hub deve seguir claro). dark/auto = escolhas explícitas.
    const forced = u.theme === 'dark' ? 'dark' : u.theme === 'auto' ? 'auto' : '';
    if (forced) await ctx.addInitScript(`(() => { const T=${JSON.stringify(forced)}; const set=()=>{ if(document.documentElement) document.documentElement.dataset.theme=T; };
      new MutationObserver((_, o) => { if (document.documentElement) { set(); o.disconnect(); } }).observe(document, { childList: true });
      document.addEventListener('DOMContentLoaded', set); })();`);
    if (u.rail) {
      const url = new URL(HUB);
      await ctx.addCookies(["hub_rail_app", ...(u.rail === "collapsed" ? ["hub_rail_hub"] : [])].map((name) => ({ name, value: u.rail as string, domain: url.hostname, path: "/" })));
    }
    await ctx.addInitScript(CHECKS_JS);
    let blocked = 0;
    await ctx.route('**/*', (r) => {
      const req = r.request();
      if (['GET', 'HEAD', 'OPTIONS'].includes(req.method()) || ALLOWED_WRITES.test(new URL(req.url()).pathname)) return r.continue();
      blocked++;
      return r.fulfill({ status: 204, body: '' });
    });
    const page = await ctx.newPage();
    page.on('dialog', (d) => d.dismiss().catch(() => undefined)); // confirm() nativo: sempre "Cancelar"
    let consoleErrs: string[] = [];
    page.on('console', (m) => { if (m.type() === 'error') consoleErrs.push(m.text()); });
    page.on('pageerror', (e) => consoleErrs.push('pageerror: ' + (e.stack || e.message).split('\n').slice(0, 3).join(' | ')));

    const findings: Finding[] = [];
    const coverage: { pattern: string; path: string; status: number; finalPath: string; triggers: number; layers: number; ms: number }[] = [];
    const shotKeys = new Set<string>();

    for (const r of routes) {
      if (u.only && !u.only.test(r.path)) continue;
      const t0 = Date.now();
      let shots = 0;
      const base = { route: r.path, pattern: r.pattern, theme: u.theme, role: u.role, viewport: `${u.vw}x${u.vh}` };
      const push = async (layer: string, f: { check: string; selector: string; message: string; rect?: { x: number; y: number; w: number; h: number } | null }, status?: number) => {
        const entry: Finding = { ...base, layer, check: f.check, selector: f.selector, message: f.message, status };
        const key = `${r.pattern}|${layer}|${f.check}|${f.selector}`;
        if (f.rect && f.rect.w > 0 && shots < MAX_SHOTS_PER_PAGE && !shotKeys.has(key + u.theme)) {
          shotKeys.add(key + u.theme);
          shots++;
          const name = crypto.createHash('sha1').update(unit + key + f.message).digest('hex').slice(0, 12) + '.png';
          try {
            const sy = await page.evaluate(() => [scrollX, scrollY]);
            const pad = 12;
            const x = Math.max(0, f.rect.x + sy[0] - pad), y = Math.max(0, f.rect.y + sy[1] - pad);
            await page.screenshot({ path: path.join(SHOTS_DIR, name), fullPage: true, timeout: 5_000,
              clip: { x, y, width: Math.min(Math.max(f.rect.w + 2 * pad, 40), 900), height: Math.min(Math.max(f.rect.h + 2 * pad, 24), 500) } });
            entry.screenshot = `shots/${name}`;
          } catch { /* fora da página */ }
        }
        findings.push(entry);
      };

      consoleErrs = [];
      blocked = 0;
      let status = 0, finalPath = r.path;
      try {
        const resp = await page.goto(HUB + r.path, { waitUntil: 'domcontentloaded', timeout: 30_000 });
        status = resp?.status() ?? 0;
        // Documento que não é HTML (ex.: /dev/docs/*.md, texto cru para agentes): só o status conta; as verificações
        // de tela num <pre> enorme travavam o renderizador e a navegação seguinte estourava o tempo (F17).
        if (resp && !/text\/html/.test(resp.headers()['content-type'] || '')) {
          coverage.push({ pattern: r.pattern, path: r.path, status, finalPath, triggers: 0, layers: 0, ms: Date.now() - t0 });
          await page.goto('about:blank').catch(() => undefined);
          continue;
        }
        await settle(page);
        finalPath = new URL(page.url()).pathname;
      } catch (e) {
        await push('página', { check: 'navegacao', selector: '', message: `falha ao abrir: ${String(e).split('\n')[0]}` });
        coverage.push({ pattern: r.pattern, path: r.path, status, finalPath, triggers: 0, layers: 0, ms: Date.now() - t0 });
        continue;
      }
      if (finalPath.startsWith('/login') && !r.path.startsWith('/login')) {
        await push('página', { check: 'http-status', selector: '', message: `redirecionou para o login (sessão perdida?)` }, status);
      }
      const shell = await page.evaluate(() => ({
        header: !!document.querySelector('.platform-header'), rail: !!document.querySelector('.platform-rail'),
        text: (document.body?.innerText || '').trim().slice(0, 120),
      }));
      const credential = CREDENTIAL_PAGES.some((re) => re.test(finalPath));
      const inShell = shell.header && (shell.rail || u.role === 'anon');
      if (status === 403) {
        if (u.role === 'admin') await push('página', { check: 'http-status', selector: '', message: `HTTP 403 para admin` }, status);
        else if (!inShell) await push('página', { check: 'sem-acesso-fora-do-shell', selector: 'body', message: `403 esperado para ${u.role}, mas a página "sem acesso" não está no shell (header ${shell.header ? 'sim' : 'não'}, rail ${shell.rail ? 'sim' : 'não'}): "${shell.text.replace(/\s+/g, ' ').slice(0, 70)}"` }, status);
      } else if (status >= 400 || status === 0) {
        await push('página', { check: 'http-status', selector: '', message: `HTTP ${status} em ${r.path}` }, status);
      } else if (!credential && !inShell) {
        await push('página', { check: 'fora-do-shell', selector: 'body', message: `página fora do shell (header ${shell.header ? 'sim' : 'não'}, rail ${shell.rail ? 'sim' : 'não'})` }, status);
      }

      // Visitante sem sessão (docs públicas) não tem preferência gravada: o padrão é seguir o SO
      // (data-theme="auto"), então o SO escuro deixa a página escura de verdade.
      const anon = u.role === 'anon';
      const lightTheme = anon ? u.theme === 'light' : u.theme !== 'dark' && u.theme !== 'auto';
      if (!forced) {
        const want = anon ? 'auto' : 'light';
        const dt = await page.evaluate(() => document.documentElement.dataset.theme || '').catch(() => want);
        if (dt !== want) await push('página', { check: 'tema-padrao', selector: 'html', message: `tema padrão deveria ser ${want}, mas data-theme="${dt}"` }, status);
      }
      const run = async (scope?: string) => page.evaluate((o) => (window as any).__uiSweep ? (window as any).__uiSweep.run(o) : [], { scope, lightTheme }).catch(() => []);
      for (const f of await run()) await push('página', f, status);

      // Foco visível (amostra de até 8 elementos do conteúdo, via teclado)
      if (u.focus && status < 400) {
        await page.evaluate(() => { const m = document.querySelector('#app-root, main') as HTMLElement | null; if (m) { m.tabIndex = -1; m.focus(); } }).catch(() => undefined);
        const seenSel = new Set<string>();
        for (let i = 0; i < 10 && seenSel.size < 8; i++) {
          await page.keyboard.press('Tab');
          const snap = await page.evaluate(() => (window as any).__uiSweep?.focusSnapshot()).catch(() => null);
          if (!snap || snap.inShell || seenSel.has(snap.selector)) continue;
          seenSel.add(snap.selector);
          const blurred = await page.evaluate(() => (window as any).__uiSweep?.blurredStyle()).catch(() => null);
          if (blurred && blurred === snap.style) await push('página', { check: 'foco-invisivel', selector: snap.selector, message: `"${snap.name}" sem indicação visível de foco`, rect: snap.rect }, status);
        }
        if (new URL(page.url()).pathname !== finalPath) { await page.goto(HUB + r.path, { waitUntil: 'domcontentloaded' }).catch(() => undefined); await settle(page); }
      }

      // Interações: abre cada gatilho de camada e verifica a camada aberta
      let nTrig = 0, nLayers = 0;
      if (u.interact && status < 400 && !credential) {
        const triggers: { idx: number; label: string; kind: string }[] = await page.evaluate(({ textRe, dangerRe, max, includeShell }) => {
          const tre = new RegExp(textRe, 'i'), dre = new RegExp(dangerRe, 'i');
          const vis = (el: Element) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && !el.closest('[hidden],[inert],[aria-hidden="true"]'); };
          const out: { idx: number; label: string; kind: string }[] = [];
          const cand = [...document.querySelectorAll('button, [role="button"], summary, a[data-hub-modal], [data-hub-modal], [data-menu], [aria-haspopup]')];
          let i = 0;
          for (const el of cand) {
            if (!vis(el)) continue;
            const inShell = !!el.closest('.platform-header, .platform-rail');
            if (inShell && !includeShell) continue;
            const row = el.closest('tbody tr');
            if (row && row.parentElement && row !== row.parentElement.querySelector('tr')) continue; // só a 1ª linha
            const label = ((el.getAttribute('aria-label') || (el as HTMLElement).innerText || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ')).slice(0, 60);
            if (dre.test(label)) continue;
            const hp = el.getAttribute('aria-haspopup');
            const kind = el.hasAttribute('data-hub-modal') ? 'modal' : hp && hp !== 'false' ? 'popup' : el.hasAttribute('data-menu') ? 'menu' : el.tagName === 'SUMMARY' ? 'details' : tre.test(label) ? 'texto' : '';
            if (!kind) continue;
            if (kind === 'texto' && (el as HTMLButtonElement).type === 'submit' && el.closest('form')) continue;
            el.setAttribute('data-sweep-trigger', String(i));
            out.push({ idx: i++, label, kind });
            if (out.length >= max) break;
          }
          return out;
        }, { textRe: TRIGGER_TEXT.source, dangerRe: DANGER.source, max: MAX_TRIGGERS, includeShell: r.path === '/' }).catch(() => []);
        nTrig = triggers.length;

        for (const t of triggers) {
          const before = page.url();
          const marked = await page.evaluate((L) => { document.querySelectorAll(L).forEach((e) => e.setAttribute('data-sweep-pre', '')); return true; }, LAYERS).catch(() => false);
          if (!marked) break;
          const el = page.locator(`[data-sweep-trigger="${t.idx}"]`);
          try { await el.click({ timeout: 3_000 }); } catch { continue; }
          await page.waitForTimeout(400);
          if (page.url() !== before) {
            // o gatilho navegou (link): volta e segue
            await page.goto(HUB + r.path, { waitUntil: 'domcontentloaded' }).catch(() => undefined);
            await settle(page);
            break; // índices mudaram com o recarregamento
          }
          const layer = await page.evaluate((L) => {
            const vis = (el: Element) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 2 && r.height > 2 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
            document.querySelectorAll('[data-sweep-layer]').forEach((e) => e.removeAttribute('data-sweep-layer'));
            const fresh = [...document.querySelectorAll(L)].filter((e) => vis(e) && (!e.hasAttribute('data-sweep-pre') || (e.tagName === 'DETAILS' && (e as HTMLDetailsElement).open && e.querySelector('[data-sweep-trigger]'))));
            const pick = fresh.find((e) => e.matches('dialog[open], [role="dialog"], [role="alertdialog"], .modal, .hub-modal')) || fresh[fresh.length - 1];
            document.querySelectorAll('[data-sweep-pre]').forEach((e) => e.removeAttribute('data-sweep-pre'));
            if (!pick) return null;
            pick.setAttribute('data-sweep-layer', '');
            const modal = pick.matches('dialog, [role="dialog"], [role="alertdialog"], .modal, .hub-modal, [aria-modal="true"]');
            const menu = pick.matches('[role="menu"], [role="listbox"], .dropdown-menu, .menu-pop, [data-hub-menu-panel]');
            const items = [...pick.querySelectorAll('[role="menuitem"], [role="option"], a[href], button, label, input')].filter(vis).length;
            const r = pick.getBoundingClientRect();
            return { modal, menu, items, tag: pick.tagName.toLowerCase() + (pick.className ? '.' + String(pick.className).split(' ')[0] : ''), rect: { x: r.x, y: r.y, w: r.width, h: r.height } };
          }, LAYERS).catch(() => null);
          const lname = `${t.kind}: ${t.label || '(sem rótulo)'}`;
          if (!layer) {
            if (t.kind === 'popup' || t.kind === 'modal') await push(lname, { check: 'gatilho-sem-efeito', selector: `[gatilho] ${t.label}`, message: `"${t.label}" (${t.kind}) não abriu nenhuma camada visível` }, status);
            continue;
          }
          nLayers++;
          if (layer.menu && layer.items === 0) await push(lname, { check: 'menu-vazio', selector: layer.tag, message: `menu/lista aberto por "${t.label}" sem itens`, rect: layer.rect }, status);
          for (const f of await run('[data-sweep-layer]')) await push(lname, f, status);
          if (t.kind === 'details') { await el.click({ timeout: 2_000 }).catch(() => undefined); continue; }
          const isOpen = () => page.evaluate(() => { const e = document.querySelector('[data-sweep-layer]'); if (!e) return false; const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2 && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none' && (e.tagName !== 'DIALOG' || (e as HTMLDialogElement).open); }).catch(() => false);
          if (layer.modal) {
            // Clique no fundo: só se o ponto cair no próprio modal/fundo (nunca num link do rail)
            const pt = { x: Math.max(4, layer.rect.x - 30), y: Math.min(u.vh - 8, layer.rect.y + layer.rect.h + 30) };
            const safe = await page.evaluate((p) => { const e = document.elementFromPoint(p.x, p.y); if (!e) return false; const lay = document.querySelector('[data-sweep-layer]'); return e === lay || /backdrop|overlay|scrim|modal/i.test(String((e as HTMLElement).className)) && !e.closest('a,button,input,select,textarea'); }, pt).catch(() => false);
            if (safe) {
              await page.mouse.click(pt.x, pt.y);
              await page.waitForTimeout(300);
              if (!(await isOpen())) await push(lname, { check: 'modal-fecha-no-fundo', selector: layer.tag, message: `modal "${t.label}" fechou com clique no fundo` }, status);
            }
          }
          if (await isOpen()) {
            await page.keyboard.press('Escape');
            await page.waitForTimeout(300);
            if (await isOpen()) {
              await push(lname, { check: 'camada-sem-esc', selector: layer.tag, message: `"${t.label}" não fecha com Esc`, rect: layer.rect }, status);
              const closer = page.locator('[data-sweep-layer] :is([aria-label*="Fechar" i], [aria-label*="Close" i], [data-hub-close], .close, button:text-matches("^(Cancelar|Fechar)$", "i"))').first();
              await closer.click({ timeout: 1_500 }).catch(() => undefined);
              await page.waitForTimeout(250);
              if (await isOpen()) {
                await push(lname, { check: 'camada-sem-fechar', selector: layer.tag, message: `"${t.label}" sem botão de fechar funcional` }, status);
                await page.goto(HUB + r.path, { waitUntil: 'domcontentloaded' }).catch(() => undefined);
                await settle(page);
                break;
              }
            }
          }
        }
      }

      // Erros de console/página (exceto: o próprio 403 esperado e as escritas bloqueadas pela varredura)
      for (const m of consoleErrs) {
        if (status === 403 && u.role !== 'admin' && /status of 403/.test(m)) continue;
        if (blocked > 0 && /net::ERR_FAILED|status of 204/.test(m)) continue;
        await push('página', { check: m.startsWith('pageerror') ? 'erro-de-pagina' : 'console', selector: '', message: m.slice(0, 300) }, status);
      }
      coverage.push({ pattern: r.pattern, path: r.path, status, finalPath, triggers: nTrig, layers: nLayers, ms: Date.now() - t0 });
    }
    fs.writeFileSync(path.join(FINDINGS_DIR, `${unit}.json`), JSON.stringify({ unit: { ...u, name: unit }, coverage, findings }, null, 2));
    await ctx.close();
  });
}
