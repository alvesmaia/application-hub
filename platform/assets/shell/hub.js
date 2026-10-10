/* hub.js — composição de fragmentos no shell do hub.
 *
 * A página /{id}[/{section}][?query] chega do servidor com o shell em modo "dentro do
 * app" e a tela de carregamento do mockup. Este script:
 *   1. carrega o CSS do app via gateway (o app NÃO traz JavaScript: os comportamentos são
 *      atributos data-hub-* do hub-kit.js; só scripts aprovados pela administração, listados
 *      em data-hub-scripts com o sha384, são carregados — com SRI, ver hub-safe.js);
 *   2. página pública /apps/{id}/{section}?query → busca o fragmento em
 *      /_frag/{id}/{section}?query (gateway -> upstream, que vê /{id}/{section});
 *   3. reproduz a sequência do mockup (decisão do dono): ao ABRIR o app, o carregador
 *      (0→100% em 3,5 s, `1 - (1 - t)^2.2`) → skeleton de componentes (0,9 s) → conteúdo;
 *      troca de página dentro do app: só o skeleton (0,7 s). Tempos fixos — se o fragmento
 *      ainda não chegou ao fim deles, o skeleton fica até ele chegar (nunca área vazia);
 * *   4. intercepta links do mesmo app (pushState + skeleton + novo fragmento) e popstate;
 *   5. intercepta formulários do app:
 *      - GET: vira navegação (a query entra na URL, então recarregar/compartilhar funciona);
 *      - POST (inclusive multipart com arquivos): enviado por XHR para /_frag/... com
 *        progresso de upload em [data-hub-progress]; a resposta (qualquer status com
 *        corpo, ex.: 409/413/422/429) substitui o elemento de data-hub-target (outerHTML)
 *        ou, sem alvo, o workspace inteiro;
 *   6. elementos com data-hub-poll="/{id}/..." são recarregados a cada
 *      data-hub-interval ms (padrão 1000) até a resposta não trazer mais data-hub-poll
 *      (progresso de jobs; para fluxo contínuo use data-hub-sse/data-hub-ws do hub-kit);
 *   7. downloads: links normais para /_frag/{id}/... (não interceptados) — o app
 *      responde com Content-Disposition e o gateway repassa o corpo em streaming;
 *   8. em falha (ex.: 502 do gateway com o app fora do ar) mostra a mensagem amigável
 *      dentro do shell, nunca uma página em branco; se o app está reiniciando
 *      (502/503/504, rede, `data-hub-unavailable`), mostra "reiniciando" e tenta de
 *      novo sozinho com espera crescente (2 s → 15 s), trocando pelo conteúdo quando o
 *      app volta — navegar para outro lugar cancela as tentativas; 429 (operação pesada
 *      em fila no app) mostra "em fila…" (rótulo do app em `data-hub-queued`, ex.
 *      "Relatório em fila…") e tenta de novo sozinho honrando o `Retry-After` com jitter
 *      (até QUEUE_MAX vezes; esgotadas, o aviso do app);
 *   9. voltar/avançar do navegador (popstate) cancela o carregador e o skeleton (como no
 *      mockup); erros (reiniciando, em fila, falha) aparecem sem esperar os tempos.
 * Todo HTML recebido do app passa pelo sanitizador (hub-safe.js: sem <script>, sem on*,
 * sem javascript:, formulários só para o próprio app) antes de entrar no documento; depois
 * de cada troca o hub-kit.js monta os comportamentos (HubKit.mount) e o evento `hub:mount`
 * avisa os scripts aprovados. */
(function () {
  'use strict';
  // Decisões puras (testadas em node: `require('hub.js')` devolve este objeto).
  const Policy = {
    /** Espera antes da tentativa `attempt` (0, 1, …) de recarregar um app indisponível. */
    retryDelay(attempt) {
      return Math.min(2000 * 2 ** attempt, 15000);
    },
    /** A resposta indica app reiniciando/indisponível (tentar de novo)? */
    retriable(status, html) {
      return status === 0 || status === 502 || status === 503 || status === 504 || /data-hub-unavailable/.test(html || '');
    },
    /** Máximo de novas tentativas automáticas depois de um 429 (fila do app). */
    QUEUE_MAX: 5,
    /** 429 = o app recusou por excesso de operações pesadas (fila): tentar de novo. */
    queued(status) {
      return status === 429;
    },
    /** Espera antes da nova tentativa `attempt` (0, 1, …) depois de um 429: honra o
     *  `Retry-After` (segundos ou data HTTP) com jitter de 0–50% (evita que várias abas
     *  voltem juntas); sem ele, espera crescente. Entre 1 s e 30 s. */
    queueDelay(retryAfter, attempt, rand = Math.random, now = Date.now()) {
      let ms = NaN;
      if (retryAfter != null && String(retryAfter).trim() !== '') {
        const s = Number(retryAfter);
        ms = Number.isFinite(s) ? s * 1000 : Date.parse(retryAfter) - now;
      }
      if (!Number.isFinite(ms) || ms <= 0) ms = Math.min(2000 * 2 ** attempt, 15000);
      return Math.min(Math.max(1000, Math.round(ms * (1 + 0.5 * rand()))), 30000);
    },
    /** Rótulo do estado "em fila" (o app pode sugerir com `data-hub-queued`). */
    queuedLabel(html) {
      const m = /data-hub-queued="([^"]*)"/.exec(html || '');
      return (m && m[1]) || 'Operação em fila…';
    },
    /** `/apps/app` sem seção nem consulta = entrada pelo início do app; o resto é deep link. */
    deepLink(pathname, search, app) {
      let bits = pathname.split('/').filter(Boolean);
      if (bits[0] === 'apps') bits = bits.slice(1);
      return bits[0] === app && (bits.length > 1 || Boolean(search && search !== '?'));
    },
  };
  if (typeof module !== 'undefined' && module.exports && typeof document === 'undefined') {
    module.exports = Policy;
    return;
  }
  const me = document.currentScript;
  const app = me.dataset.hubApp;
  // O conteúdo do app substitui o #app-root (e este <script>): o id fica no <html> para o
  // hub-kit/hub-device lerem depois.
  document.documentElement.dataset.hubApp = app || '';
  const frag = me.dataset.hubFrag || '/_frag';
  const version = me.dataset.hubVersion || '';
  const root = document.getElementById('app-root');
  // Tokens do app (onda 8): cor e tinta da identidade do app para os fragmentos
  // (`var(--app-color)`/`var(--app-ink)`), valores só de cor (sem injeção de CSS).
  const colorOk = (c) => /^#[0-9a-fA-F]{3,8}$/.test(c || '');
  // Tema escuro (onda 9): a tinta do app clareada e o fundo do ícone escurecido, para
  // manter o contraste sobre as superfícies escuras.
  const theme = document.documentElement.dataset.theme || '';
  const dark = theme === 'dark' || (theme === 'auto' && window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
  const mix = (hex, to, k) => {
    let x = hex.slice(1);
    if (x.length === 3) x = [...x].map((c) => c + c).join('');
    const n = parseInt(x.slice(0, 6), 16);
    const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.round(v + (to - v) * k));
    return `#${c.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
  };
  if (root && colorOk(me.dataset.hubColor)) root.style.setProperty('--app-color', dark ? mix(me.dataset.hubColor, 0, 0.78) : me.dataset.hubColor);
  if (root && colorOk(me.dataset.hubInk)) root.style.setProperty('--app-ink', dark ? mix(me.dataset.hubInk, 255, 0.5) : me.dataset.hubInk);
  // Onda 12 (M1): o conteúdo do app fica num contexto de empilhamento próprio
  // (`isolation: isolate; position: relative; z-index: 0` em #app-root): nada do app —
  // nem `position: fixed; inset: 0; z-index: 2147483647` — pinta acima do header e do
  // rail do hub (posicionados com z-index maior). Estilo inline `!important`: o CSS do app
  // não desfaz. Header e rail mantêm posição/camada e o header a exibição que tinham antes
  // do CSS do app. (`contain: paint` foi avaliado e descartado: desloca a sidebar e os
  // elementos fixos dos apps — ver PENDENCIAS.)
  function containApp() {
    if (!root) return;
    for (const [k, val] of [['isolation', 'isolate'], ['position', 'relative'], ['z-index', '0']]) root.style.setProperty(k, val, 'important');
    document.querySelectorAll('.platform-header, .platform-rail').forEach((el) => {
      const cs = getComputedStyle(el);
      el.style.setProperty('position', cs.position, 'important');
      el.style.setProperty('z-index', cs.zIndex, 'important');
      if (el.classList.contains('platform-header')) {
        el.style.setProperty('display', cs.display, 'important');
        el.style.setProperty('visibility', 'visible', 'important');
        el.style.setProperty('opacity', '1', 'important');
      }
    });
  }
  containApp();
  const base = `${frag}/${app}`;
  /** Tempos fixos do mockup (decisão do dono). */
  const LOADER_MS = 3500;
  const SKELETON_MS = 900;
  const PAGE_SKELETON_MS = 700;
  let navToken = 0;
  // Alvo em voo/renderizado (onda 9 bug): `location` já muda no pushState, antes do
  // fragmento chegar — comparar o clique com `location` deixa um clique durante essa
  // janela (ex.: alternar a ordenação bem rápido) sem efeito, porque o link ainda
  // exibido (não re-renderizado) aponta pro mesmo alvo que acabou de ser empurrado.
  // `pendingTarget` segue o que está de fato em voo/renderizado na tela.
  let pendingTarget = location.pathname + location.search;
  const store = (kind) => { try { return window[kind]; } catch (_) { return null; } };
  const ASSETS_KEY = `hub:assets:${app}`;
  const OPENED_KEY = `hub:opened:${app}`;
  const startedAt = performance.now();
  let T = {};
  try { T = JSON.parse(document.body.dataset.hubI18n || '{}'); } catch (_) { T = {}; }
  const tr = (k, fb, vars) => { let x = T[k] || fb; for (const [a, b] of Object.entries(vars || {})) x = x.replace(`{${a}}`, String(b)); return x; };
  const appName = (root && root.querySelector('.app-loading')?.dataset.loaderName) || app;
  function navType() {
    try {
      const e = performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
      return e ? e.type : 'navigate';
    } catch (_) { return 'navigate'; }
  }
  const v = version ? `?v=${encodeURIComponent(version)}` : '';

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // URLs públicas do app (decisão do dono): `/apps/{id}/...`. Um caminho antigo
  // `/{id}/...` (app ainda não atualizado) é aceito e normalizado para o novo.
  const pub = me.dataset.hubBase || `/apps/${app}`;
  function canon(path) {
    if (path === `/${app}` || path.startsWith(`/${app}/`) || path.startsWith(`/${app}?`)) return `/apps${path}`;
    return path;
  }
  function sectionOf(path) {
    const p = canon(path.split('?')[0]);
    if (p !== pub && !p.startsWith(`${pub}/`)) return null;
    return p.slice(pub.length).split('/').filter(Boolean)[0] || 'dashboard';
  }
  // Caminho público → caminho no gateway (`/_frag/{id}/...`; o upstream vê `/{id}/...`).
  const fragUrl = (target) => `${frag}/${app}${canon(target).slice(pub.length)}`;

  // CSS do app (onda 12, M1): nunca como <link> global — o texto vem pelo gateway e vira
  // uma folha construída dentro de `@scope (#app-root, [data-hub-layer])` (hub-safe.js):
  // regras do app não alcançam o header, o rail nem o `body` do hub.
  async function loadAppCss(href) {
    try {
      const res = await fetch(href, { credentials: 'same-origin' });
      if (!res.ok || !window.HubSafe || !window.HubSafe.scopedSheet) return;
      const sheet = window.HubSafe.scopedSheet(await res.text());
      document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    } catch (err) { console.error('CSS do app não aplicado', err); }
  }

  function loadCss(href) {
    return new Promise((resolve) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      link.onload = link.onerror = () => resolve();
      document.head.append(link);
    });
  }

  function loadScript(src, integrity) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.integrity = integrity;
      s.crossOrigin = 'anonymous';
      s.src = window.HubSafe ? window.HubSafe.scriptURL(src) : src;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error(`falha ao carregar ${src}`));
      document.body.append(s);
    });
  }

  // Sessão expirada (401 do hub): volta ao login e depois para esta mesma página.
  function toLogin() {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }

  // Scripts de app APROVADOS (exceção; ver CONTRATO-APP §13): `[{src, integrity}]`
  // fixado pelo servidor. Só caminhos do próprio app em /_frag/{id}/assets/, com o sha384
  // aprovado (SRI: arquivo alterado = bloqueado pelo navegador).
  let approvedScripts = [];
  try {
    approvedScripts = JSON.parse(me.dataset.hubScripts || '[]').filter((x) => x && typeof x.src === 'string'
      && x.src.startsWith(`${base}/assets/`) && !x.src.includes('..') && /^sha384-[A-Za-z0-9+/=]{64}$/.test(x.integrity || ''));
  } catch (_) { approvedScripts = []; }
  window.HubSafe?.approve(approvedScripts.map((x) => x.src));

  // Assets do app, servidos pelo próprio upstream e alcançados só via gateway.
  const assetsReady = (async () => {
    await Promise.all([loadAppCss(`${base}/assets/demo.css${v}`), loadAppCss(`${base}/assets/app.css${v}`)]);
    for (const sc of approvedScripts) {
      await loadScript(sc.src, sc.integrity).catch((err) => console.error(err));
    }
    try { store('localStorage')?.setItem(ASSETS_KEY, version); } catch (_) { /* cota/privado */ }
  })();

  // `target` = caminho da página no app (/{id}/{section}) + query.
  async function fetchFragment(target) {
    let res;
    try {
      // `x-hub-nav`: troca de página (analytics de navegação no hub; o gateway descarta
      // x-hub-* antes do app).
      res = await fetch(fragUrl(target), {
        headers: { accept: 'text/html', 'x-hub-nav': '1' },
        credentials: 'same-origin',
      });
    } catch (err) {
      return { ok: false, retry: true, html: unavailable() };
    }
    if (res.status === 401) {
      toLogin();
      return { ok: false, html: '' };
    }
    const html = await res.text();
    if (res.ok) return { ok: true, html };
    if (Policy.queued(res.status)) {
      const retryAfter = res.headers && res.headers.get ? res.headers.get('retry-after') : null;
      return { ok: false, queued: true, retryAfter, html };
    }
    // 502 do gateway e 404 do app já vêm como fragmento amigável.
    return { ok: false, retry: Policy.retriable(res.status, html), html: html.trim() ? html : unavailable() };
  }

  // App reiniciando: estado amigável dentro do shell + novas tentativas até voltar.
  function retryView(html, seconds) {
    return `<div class="workspace workspace-no-sidebar" id="workspace"><section class="workspace-content hub-app-error"><div class="hub-retry" data-hub-retry="" role="status" aria-live="polite"><div class="hub-retry-spinner" aria-hidden="true"></div>${html}<p>Tentando de novo em <span data-hub-retry-in="">${seconds}</span> s…</p><p><a class="primary-button" href="/applications">Voltar à plataforma</a></p></div></section></div>`;
  }

  async function retryUntilBack(target, token, html) {
    for (let attempt = 0; token === navToken; attempt++) {
      const delay = Policy.retryDelay(attempt);
      setHtml(root, retryView(html, Math.round(delay / 1000)));
      await wait(delay);
      if (token !== navToken) return;
      const result = await fetchFragment(target);
      if (token !== navToken) return;
      if (result.ok) { await swap(result.html); return; }
      if (!result.retry) { await swap(errorView(result.html)); return; }
      html = result.html;
    }
  }

  // 429 (operação pesada em fila no app): estado amigável "em fila" com novas tentativas
  // automáticas (Retry-After + jitter, até QUEUE_MAX); esgotadas, mostra o aviso do app.
  function queuedMarkup(label, seconds, attempt) {
    return `<div class="hub-retry" data-hub-queued-view="" role="status" aria-live="polite"><div class="hub-retry-spinner" aria-hidden="true"></div><h2>${label.replace(/[<>&"]/g, '')}</h2><p>O app está com muitas operações pesadas agora; a sua entra assim que houver vaga. Tentando de novo em <span data-hub-retry-in="">${seconds}</span> s (tentativa ${attempt + 1} de ${Policy.QUEUE_MAX})…</p></div>`;
  }

  function showQueued(markup) {
    const content = root.querySelector('.app-sidebar') && root.querySelector('.workspace-content');
    if (content) {
      content.classList.remove('component-loading');
      setHtml(content, markup);
    } else {
      setHtml(root, `<div class="workspace workspace-no-sidebar" id="workspace"><section class="workspace-content">${markup}</section></div>`);
    }
  }

  async function queueUntilReady(target, token, result) {
    const label = Policy.queuedLabel(result.html);
    for (let attempt = 0; attempt < Policy.QUEUE_MAX; attempt++) {
      const delay = Policy.queueDelay(result.retryAfter, attempt);
      showQueued(queuedMarkup(label, Math.round(delay / 1000), attempt));
      await wait(delay);
      if (token !== navToken) return;
      result = await fetchFragment(target);
      if (token !== navToken) return;
      if (result.ok) { await swap(result.html); return; }
      if (!result.queued) {
        if (result.retry) { retryUntilBack(target, token, result.html); return; }
        await swap(errorView(result.html));
        return;
      }
    }
    await swap(errorView(result.html));
  }

  function unavailable() {
    return '<section class="empty-state"><h1>Aplicação indisponível</h1><p>A aplicação não respondeu. Tente novamente em instantes.</p></section>';
  }

  function errorView(html) {
    return `<div class="workspace workspace-no-sidebar" id="workspace"><section class="workspace-content hub-app-error">${html}<p class="empty-state"><a class="primary-button" href="/applications">Voltar à plataforma</a></p></section></div>`;
  }

  // Carregador do mockup: 0→100% em LOADER_MS com `1 - (1 - t)^2.2` (a % segue mesmo com
  // prefers-reduced-motion; só o brilho da barra para, no CSS).
  function startLoader() {
    const box = root.querySelector('.app-loading');
    if (!box) return;
    const pct = box.querySelector('.loader-percentage');
    const fill = box.querySelector('.loader-fill');
    const bar = box.querySelector('.loader-track');
    (function tick(now) {
      if (!box.isConnected || box.dataset.hubLoaderDone) return;
      // Clamp: o carimbo do quadro (rAF) pode ser anterior a startedAt (daria -1%).
      const ratio = Math.max(0, Math.min((now - startedAt) / LOADER_MS, 1));
      const value = ratio >= 1 ? 100 : Math.floor((1 - Math.pow(1 - ratio, 2.2)) * 100);
      if (pct) pct.textContent = `${value}%`;
      if (fill) fill.style.width = `${value}%`;
      if (bar) bar.setAttribute('aria-valuenow', String(value));
      if (ratio < 1) requestAnimationFrame(tick);
    })(performance.now());
  }

  // Fim do carregador: o temporizador de LOADER_MS pode vencer antes do quadro de
  // animação que chegaria a 100% — fecha em 100% e deixa um quadro pintar antes de trocar.
  function finishLoader() {
    const box = root.querySelector('.app-loading');
    if (!box) return Promise.resolve();
    box.dataset.hubLoaderDone = '1';
    const pct = box.querySelector('.loader-percentage');
    const fill = box.querySelector('.loader-fill');
    const bar = box.querySelector('.loader-track');
    if (pct) pct.textContent = '100%';
    if (fill) fill.style.width = '100%';
    if (bar) bar.setAttribute('aria-valuenow', '100');
    return new Promise((r) => requestAnimationFrame(() => r()));
  }

  // Skeleton de componentes (o do mockup): título, métricas, gráfico, lista e tabela.
  function skeletonMarkup() {
    const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    return '<div class="skeleton-page-heading" aria-hidden="true"><div><i class="skeleton-block eyebrow-skeleton"></i><i class="skeleton-block title-skeleton"></i><i class="skeleton-block subtitle-skeleton"></i></div><i class="skeleton-block action-skeleton"></i></div>'
      + '<div class="skeleton-metric-row" aria-hidden="true"><i class="skeleton-block metric-skeleton"></i><i class="skeleton-block metric-skeleton"></i><i class="skeleton-block metric-skeleton"></i></div>'
      + '<div class="skeleton-panel-row" aria-hidden="true"><section class="skeleton-panel chart-skeleton"><i class="skeleton-block panel-title-skeleton"></i><div class="skeleton-chart-bars"><i></i><i></i><i></i><i></i><i></i><i></i></div></section><section class="skeleton-panel list-skeleton"><i class="skeleton-block panel-title-skeleton"></i><i class="skeleton-block list-line"></i><i class="skeleton-block list-line"></i><i class="skeleton-block list-line"></i><i class="skeleton-block list-line short"></i></section></div>'
      + '<section class="skeleton-panel table-skeleton" aria-hidden="true"><i class="skeleton-block panel-title-skeleton"></i><i class="skeleton-block table-line"></i><i class="skeleton-block table-line"></i><i class="skeleton-block table-line"></i></section>'
      + `<span class="component-loading-caption">${esc(tr('js.loading_app', 'Carregando {app}', { app: appName }))}</span>`;
  }

  // Mostra o skeleton no lugar do conteúdo (com a sidebar do app, se já houver uma).
  function showSkeleton(section) {
    let content = root.querySelector('.workspace-content');
    if (!content || root.querySelector('.app-loading')) {
      setHtml(root, '<div class="workspace workspace-no-sidebar" id="workspace"><section class="workspace-content"></section></div>');
      content = root.querySelector('.workspace-content');
    }
    const menu = content.querySelector('[data-hub-menu="open"], [data-mobile-menu]');
    setHtml(content, skeletonMarkup());
    if (menu) content.prepend(menu);
    content.classList.add('component-loading');
    content.setAttribute('aria-busy', 'true');
    content.setAttribute('aria-label', tr('js.loading_components', 'Carregando componentes da aplicação'));
    root.querySelectorAll('.app-sidebar .nav-item').forEach((a) => {
      a.classList.toggle('active', sectionOf(new URL(a.href, location.href).pathname) === section);
    });
  }

  // Único caminho de HTML para o documento: sanitizado (hub-safe.js). Sem o sanitizador
  // (script do hub não carregou) nada do app é inserido.
  function setHtml(el, html) {
    if (!window.HubSafe) { el.textContent = ''; return; }
    // A faixa "Você está em staging" (servidor, dentro do #app-root) sobrevive à troca do conteúdo do app.
    const bar = el === root ? root.querySelector(':scope > [data-hub-staging-bar]') : null;
    window.HubSafe.setHTML(el, html, { app });
    if (bar) root.prepend(bar);
  }

  function mounted(scope) {
    // Barra de navegação do app no celular: "<App> · <Seção>" (o botão vem do app).
    try {
      const bar = root.querySelector('[data-hub-menu="open"], [data-mobile-menu]');
      const sec = root.querySelector('.app-sidebar .nav-item.active')?.textContent.trim();
      if (bar && !bar.querySelector('.hub-appbar-label')) {
        const sb = root.querySelector('.app-sidebar');
        if (sb && !sb.id) sb.id = 'hub-app-nav';
        if (sb) bar.setAttribute('aria-controls', sb.id);
        bar.setAttribute('aria-expanded', 'false');
        const label = document.createElement('span');
        label.className = 'hub-appbar-label';
        label.textContent = sec ? `${appName} · ${sec}` : appName;
        Array.from(bar.childNodes).forEach((n) => { if (n.nodeType === 3) n.remove(); });
        bar.append(label);
      }
    } catch (_) { /* barra opcional */ }
    try { window.HubKit?.mount(scope); } catch (err) { console.error(err); }
    try { document.dispatchEvent(new CustomEvent('hub:mount', { detail: { root: scope, app } })); } catch (_) { /* nada */ }
    startPolling(scope);
  }

  async function swap(html) {
    setHtml(root, html);
    try {
      await assetsReady;
    } catch (err) {
      console.error(err);
    }
    mounted(root);
  }

  // Substitui `el` (outerHTML) pelo HTML recebido (sanitizado); devolve os novos nós.
  function replaceElement(el, html) {
    const frag = window.HubSafe ? window.HubSafe.sanitize(html.trim(), { app }) : document.createDocumentFragment();
    const nodes = [...frag.children];
    el.replaceWith(frag);
    nodes.forEach((n) => mounted(n));
    return nodes;
  }

  // Polling de elementos [data-hub-poll] (progresso de jobs em segundo plano).
  function startPolling(scope) {
    const els = scope.matches?.('[data-hub-poll]') ? [scope] : [...scope.querySelectorAll('[data-hub-poll]')];
    els.forEach((el) => {
      const url = el.dataset.hubPoll;
      if (!url || sectionOf(url.split('?')[0]) === null) return;
      const every = Math.max(250, Number(el.dataset.hubInterval) || 1000);
      setTimeout(async () => {
        if (!el.isConnected) return;
        try {
          const res = await fetch(fragUrl(url), { headers: { accept: 'text/html' }, credentials: 'same-origin' });
          const html = await res.text();
          if (!el.isConnected) return;
          if (html.trim()) replaceElement(el, html);
          else startPolling(el);
        } catch (_) {
          if (el.isConnected) startPolling(el); // tenta de novo no próximo intervalo
        }
      }, every);
    });
  }

  async function open(target) {
    const token = ++navToken;
    try { store('sessionStorage')?.setItem(OPENED_KEY, '1'); } catch (_) { /* privado */ }
    startLoader();
    const section = sectionOf(target.split('?')[0]);
    const fragment = Promise.all([fetchFragment(target), assetsReady.catch(() => {})]).then(([r]) => r);
    // Erro conhecido antes do fim do carregador: mostra já (sem segurar com o tempo fixo).
    const early = await Promise.race([fragment, wait(LOADER_MS - (performance.now() - startedAt)).then(() => null)]);
    if (token !== navToken) return;
    if (early && !early.ok) return showFailure(target, token, early);
    // Fragmento pronto antes: o carregador ainda cumpre o tempo fixo (decisão do dono).
    if (early) await wait(LOADER_MS - (performance.now() - startedAt));
    if (token !== navToken) return;
    // Fim do carregador → skeleton de componentes; o conteúdo entra depois de SKELETON_MS
    // (ou quando o fragmento chegar, se demorar mais).
    await finishLoader();
    if (token !== navToken) return;
    const skelAt = performance.now();
    showSkeleton(section);
    const result = early || await fragment;
    if (token !== navToken) return;
    if (!result.ok) return showFailure(target, token, result);
    await wait(SKELETON_MS - (performance.now() - skelAt));
    if (token !== navToken) return;
    await swap(result.html);
  }

  async function showFailure(target, token, result) {
    if (result.queued) { queueUntilReady(target, token, result); return; }
    if (result.retry) { retryUntilBack(target, token, result.html); return; }
    await swap(errorView(result.html));
  }

  // `target` = /{id}/{section}[?query]
  async function go(target, push, quiet) {
    const path = target.split('?')[0];
    const section = sectionOf(path);
    if (!section) { location.assign(target); return; }
    const token = ++navToken;
    const samePage = path === location.pathname;
    pendingTarget = target;
    if (push) history.pushState({}, '', target);
    if (!samePage) window.scrollTo(0, 0);
    const hasSidebar = Boolean(root.querySelector('.app-sidebar'));
    // Mesma página com outra query (busca, filtro, paginação): sem skeleton — mas o
    // conteúdo (ainda o antigo) fica sem receber cliques até o fragmento novo chegar,
    // senão um segundo clique rápido (ex.: alternar a ordenação) acerta o link antigo,
    // que ainda não reflete a troca, e o alvo calculado bate com o que já foi
    // empurrado — o clique é descartado como "sem mudança" (onda 9: consulta ao
    // servidor ficou mais lenta o bastante pra essa janela aparecer de verdade).
    // Troca de página no app: skeleton por PAGE_SKELETON_MS fixos (ou até o fragmento chegar).
    // `quiet` (voltar/avançar do navegador) cancela o skeleton, como no mockup.
    const skeleton = hasSidebar && !samePage && !quiet;
    if (skeleton) showSkeleton(section);
    const skelAt = performance.now();
    if (samePage) root.style.pointerEvents = 'none';
    try {
      const result = await fetchFragment(target);
      if (token !== navToken) return;
      if (skeleton && result.ok) {
        await wait(PAGE_SKELETON_MS - (performance.now() - skelAt));
        if (token !== navToken) return;
      }
      if (!result.ok && result.queued) { queueUntilReady(target, token, result); return; }
      if (!result.ok && result.retry) { retryUntilBack(target, token, result.html); return; }
      if (!result.ok) {
        const content = hasSidebar && root.querySelector('.workspace-content');
        if (content) {
          content.classList.remove('component-loading');
          setHtml(content, result.html);
        } else {
          await swap(errorView(result.html));
        }
        return;
      }
      const scroller = root.querySelector('.workspace-content');
      const keep = samePage && scroller ? scroller.scrollTop : 0;
      // Celular: quem rola é o documento (puxar para atualizar do navegador funciona).
      const docKeep = samePage ? window.scrollY : 0;
      await swap(result.html);
      if (keep) { const s = root.querySelector('.workspace-content'); if (s) s.scrollTop = keep; }
      if (docKeep) window.scrollTo(0, docKeep);
      if (!samePage) focusContent();
    } finally {
      if (samePage) root.style.pointerEvents = '';
    }
  }

  // Acessibilidade (onda 9): depois de trocar de seção, o foco vai para o título da
  // página nova (leitores de tela anunciam a mudança; o teclado continua dali).
  function focusContent() {
    const h = root.querySelector('.workspace-content h1, .workspace-content h2') || root;
    if (!h.hasAttribute('tabindex')) h.setAttribute('tabindex', '-1');
    try { h.focus({ preventScroll: true }); } catch (_) { h.focus(); }
  }

  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest('#app-root a[href]');
    if (!a || a.target === '_blank' || a.hasAttribute('download')) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || !sectionOf(url.pathname)) return;
    e.preventDefault();
    const target = canon(url.pathname) + url.search;
    if (target !== pendingTarget) go(target, true);
  });

  // Formulários do app (ação em /{id}/...): GET vira navegação; POST vai por XHR.
  document.addEventListener('submit', (e) => {
    const form = e.target.closest('#app-root form');
    if (!form || e.defaultPrevented) return;
    // Destino fora do app (sanitizador marcou): nunca sai daqui.
    if (form.hasAttribute('data-hub-blocked') || e.submitter?.hasAttribute?.('data-hub-blocked')) { e.preventDefault(); return; }
    const action = new URL(form.getAttribute('action') || location.href, location.href);
    if (action.origin !== location.origin || !sectionOf(action.pathname)) return;
    e.preventDefault();
    const method = (form.getAttribute('method') || 'get').toUpperCase();
    const data = new FormData(form, e.submitter || undefined);
    if (method === 'GET') {
      const q = new URLSearchParams();
      for (const [k, v] of data) if (typeof v === 'string' && v !== '') q.append(k, v);
      const qs = q.toString();
      go(canon(action.pathname) + (qs ? `?${qs}` : ''), true);
      return;
    }
    submitPost(form, canon(action.pathname) + action.search, data);
  });

  // Chave de idempotência por submissão (reusada nas novas tentativas automáticas):
  // o app devolve a mesma resposta em vez de repetir a ação (hub-sdk).
  function idempotencyKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = new Uint8Array(16);
    (window.crypto || {}).getRandomValues ? crypto.getRandomValues(b) : b.forEach((_, i) => { b[i] = Math.floor(Math.random() * 256); });
    return [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  }
  // Falhas transitórias (rede, app reiniciando, gateway sem resposta): nova tentativa
  // com a MESMA chave, com espera crescente.
  const RETRY_STATUS = [0, 502, 503, 504];
  const RETRY_DELAYS = [1000, 2000, 4000, 8000];

  function submitPost(form, target, data) {
    const hasFile = [...data.values()].some((v) => v instanceof File);
    const body = hasFile || form.enctype === 'multipart/form-data' ? data : new URLSearchParams([...data].filter(([, v]) => typeof v === 'string'));
    const buttons = [...form.querySelectorAll('button, input[type=submit]')];
    buttons.forEach((b) => { b.disabled = true; });
    const progress = form.querySelector('[data-hub-progress]');
    const key = idempotencyKey();
    const done = async (html) => {
      buttons.forEach((b) => { b.disabled = false; });
      const sel = form.dataset.hubTarget;
      // Só um id (`#id`) dentro do conteúdo do app — nunca seletor montado com texto do app.
      const m = /^#?([A-Za-z][\w-]*)$/.exec((sel || '').trim());
      const cand = m ? document.getElementById(m[1]) : null;
      const el = cand && cand !== root && root.contains(cand) ? cand : null;
      if (el) { replaceElement(el, html); return; }
      if (/class="workspace[ "]/.test(html)) { await swap(html); return; }
      const content = root.querySelector('.workspace-content');
      if (content) { setHtml(content, html); mounted(content); } else await swap(errorView(html));
    };
    const send = (attempt) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', fragUrl(target));
      xhr.setRequestHeader('accept', 'text/html');
      xhr.setRequestHeader('Idempotency-Key', key);
      if (progress && hasFile) {
        xhr.upload.onprogress = (ev) => {
          if (!ev.lengthComputable) return;
          const pct = Math.floor((ev.loaded / ev.total) * 100);
          if (progress.tagName === 'PROGRESS') { progress.max = 100; progress.value = pct; } else progress.textContent = `Enviando… ${pct}%`;
        };
      }
      const retryOrDone = (status, html) => {
        if (RETRY_STATUS.includes(status) && attempt < RETRY_DELAYS.length) {
          if (progress) progress.textContent = `Conexão instável — tentando de novo (${attempt + 1}/${RETRY_DELAYS.length})…`;
          setTimeout(() => send(attempt + 1), RETRY_DELAYS[attempt]);
          return;
        }
        done(html);
      };
      xhr.onload = () => xhr.status === 401 ? toLogin() : retryOrDone(xhr.status, xhr.responseText.trim() ? xhr.responseText : unavailable());
      xhr.onerror = () => retryOrDone(0, unavailable());
      xhr.send(body);
    };
    send(0);
  }

  window.addEventListener('popstate', () => {
    if (sectionOf(location.pathname)) go(location.pathname + location.search, false, true);
    else location.reload();
  });

  const first = sectionOf(location.pathname) ? canon(location.pathname) + location.search : `${pub}/${me.dataset.hubSection || 'dashboard'}`;
  open(first);
})();
