/* hub.js — fragment composition in the hub shell.
 *
 * The page /{id}[/{section}][?query] arrives from the server with the shell in "inside the
 * app" mode and the mockup's loading screen. This script:
 *   1. loads the app's CSS through the gateway (the app does NOT bring JavaScript: behaviors
 *      are data-hub-* attributes of hub-kit.js; only scripts approved by the administration, listed
 *      in data-hub-scripts with the sha384, are loaded — with SRI, see hub-safe.js);
 *   2. public page /apps/{id}/{section}?query → fetches the fragment at
 *      /_frag/{id}/{section}?query (gateway -> upstream, which sees /{id}/{section});
 *   3. reproduces the mockup's sequence (owner's decision): when OPENING the app, the loader
 *      (0→100% in 3.5 s, `1 - (1 - t)^2.2`) → component skeleton (0.9 s) → content;
 *      page change inside the app: only the skeleton (0.7 s). Fixed times — if the fragment
 *      has not arrived by their end, the skeleton stays until it does (never an empty area);
 * *   4. intercepts links of the same app (pushState + skeleton + new fragment) and popstate;
 *   5. intercepts the app's forms:
 *      - GET: becomes navigation (the query goes into the URL, so reload/sharing works);
 *      - POST (including multipart with files): sent by XHR to /_frag/... with
 *        upload progress in [data-hub-progress]; the response (any status with
 *        a body, e.g. 409/413/422/429) replaces the data-hub-target element (outerHTML)
 *        or, without a target, the whole workspace;
 *   6. elements with data-hub-poll="/{id}/..." are reloaded every
 *      data-hub-interval ms (default 1000) until the response no longer brings data-hub-poll
 *      (job progress; for a continuous stream use data-hub-sse/data-hub-ws from hub-kit);
 *   7. downloads: normal links to /_frag/{id}/... (not intercepted) — the app
 *      responds with Content-Disposition and the gateway passes the body through as a stream;
 *   8. on failure (e.g. 502 from the gateway with the app down) shows the friendly message
 *      inside the shell, never a blank page; if the app is restarting
 *      (502/503/504, network, `data-hub-unavailable`), it shows "restarting" and retries
 *      on its own with increasing wait (2 s → 15 s), swapping in the content when the
 *      app comes back — navigating elsewhere cancels the attempts; 429 (heavy operation
 *      queued in the app) shows "queued…" (the app's label in `data-hub-queued`, e.g.
 *      "Report queued…") and retries on its own honoring the `Retry-After` with jitter
 *      (up to QUEUE_MAX times; once exhausted, the app's warning);
 *   9. the browser's back/forward (popstate) cancels the loader and the skeleton (as in the
 *      mockup); errors (restarting, queued, failure) appear without waiting for the times.
 * All HTML received from the app goes through the sanitizer (hub-safe.js: no <script>, no on*,
 * no javascript:, forms only to the app itself) before entering the document; after each
 * swap hub-kit.js mounts the behaviors (HubKit.mount) and the `hub:mount` event
 * notifies the approved scripts. */
(function () {
  'use strict';
  // Pure decisions (tested in node: `require('hub.js')` returns this object).
  const Policy = {
    /** Wait before attempt `attempt` (0, 1, …) to reload an unavailable app. */
    retryDelay(attempt) {
      return Math.min(2000 * 2 ** attempt, 15000);
    },
    /** Does the response indicate the app is restarting/unavailable (retry)? */
    retriable(status, html) {
      return status === 0 || status === 502 || status === 503 || status === 504 || /data-hub-unavailable/.test(html || '');
    },
    /** Maximum number of automatic new attempts after a 429 (the app's queue). */
    QUEUE_MAX: 5,
    /** 429 = the app refused due to excessive heavy operations (queue): retry. */
    queued(status) {
      return status === 429;
    },
    /** Wait before the new attempt `attempt` (0, 1, …) after a 429: honors the
     *  `Retry-After` (seconds or HTTP date) with 0–50% jitter (avoids several tabs
     *  coming back together); without it, increasing wait. Between 1 s and 30 s. */
    queueDelay(retryAfter, attempt, rand = Math.random, now = Date.now()) {
      let ms = NaN;
      if (retryAfter != null && String(retryAfter).trim() !== '') {
        const s = Number(retryAfter);
        ms = Number.isFinite(s) ? s * 1000 : Date.parse(retryAfter) - now;
      }
      if (!Number.isFinite(ms) || ms <= 0) ms = Math.min(2000 * 2 ** attempt, 15000);
      return Math.min(Math.max(1000, Math.round(ms * (1 + 0.5 * rand()))), 30000);
    },
    /** Label of the "queued" state (the app can suggest it with `data-hub-queued`). */
    queuedLabel(html) {
      const m = /data-hub-queued="([^"]*)"/.exec(html || '');
      return (m && m[1]) || 'Operação em fila…';
    },
    /** `/apps/app` without a section or query = entry through the app's start; the rest is a deep link. */
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
  // The app content replaces #app-root (and this <script>): the id stays on <html> so
  // hub-kit/hub-device can read it later.
  document.documentElement.dataset.hubApp = app || '';
  const frag = me.dataset.hubFrag || '/_frag';
  const version = me.dataset.hubVersion || '';
  const root = document.getElementById('app-root');
  // App tokens (wave 8): the app's identity color and ink for the fragments
  // (`var(--app-color)`/`var(--app-ink)`), color values only (no CSS injection).
  const colorOk = (c) => /^#[0-9a-fA-F]{3,8}$/.test(c || '');
  // Dark theme (wave 9): the app's ink lightened and the icon background darkened, to
  // keep contrast over the dark surfaces.
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
  // Wave 12 (M1): the app content gets its own stacking context
  // (`isolation: isolate; position: relative; z-index: 0` on #app-root): nothing from the app —
  // not even `position: fixed; inset: 0; z-index: 2147483647` — paints above the hub's header and
  // rail (positioned with a higher z-index). Inline `!important` style: the app's CSS cannot undo it.
  // Header and rail keep their position/layer, and the header keeps the display it had before
  // the app's CSS. (`contain: paint` was evaluated and discarded: it shifts the sidebar and the
  // fixed elements of the apps — see PENDENCIAS.)
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
  /** Fixed times of the mockup (owner's decision). */
  const LOADER_MS = 3500;
  const SKELETON_MS = 900;
  const PAGE_SKELETON_MS = 700;
  let navToken = 0;
  // In-flight/rendered target (wave 9 bug): `location` already changes in pushState, before the
  // fragment arrives — comparing the click with `location` leaves a click during that
  // window (e.g. toggling the sorting very fast) with no effect, because the link still
  // shown (not re-rendered) points to the same target that was just pushed.
  // `pendingTarget` follows what is actually in flight/rendered on the screen.
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

  // Public URLs of the app (owner's decision): `/apps/{id}/...`. An old path
  // `/{id}/...` (app not yet updated) is accepted and normalized to the new one.
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
  // Public path → gateway path (`/_frag/{id}/...`; the upstream sees `/{id}/...`).
  const fragUrl = (target) => `${frag}/${app}${canon(target).slice(pub.length)}`;

  // App CSS (wave 12, M1): never as a global <link> — the text comes through the gateway and becomes
  // a sheet built inside `@scope (#app-root, [data-hub-layer])` (hub-safe.js):
  // the app's rules do not reach the hub's header, rail or `body`.
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

  // Expired session (401 from the hub): goes back to login and then to this same page.
  function toLogin() {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }

  // APPROVED app scripts (exception; see CONTRATO-APP §13): `[{src, integrity}]`
  // fixed by the server. Only the app's own paths in /_frag/{id}/assets/, with the approved
  // sha384 (SRI: a changed file = blocked by the browser).
  let approvedScripts = [];
  try {
    approvedScripts = JSON.parse(me.dataset.hubScripts || '[]').filter((x) => x && typeof x.src === 'string'
      && x.src.startsWith(`${base}/assets/`) && !x.src.includes('..') && /^sha384-[A-Za-z0-9+/=]{64}$/.test(x.integrity || ''));
  } catch (_) { approvedScripts = []; }
  window.HubSafe?.approve(approvedScripts.map((x) => x.src));

  // App assets, served by the upstream itself and reached only through the gateway.
  const assetsReady = (async () => {
    await Promise.all([loadAppCss(`${base}/assets/demo.css${v}`), loadAppCss(`${base}/assets/app.css${v}`)]);
    for (const sc of approvedScripts) {
      await loadScript(sc.src, sc.integrity).catch((err) => console.error(err));
    }
    try { store('localStorage')?.setItem(ASSETS_KEY, version); } catch (_) { /* quota/private */ }
  })();

  // `target` = the page path in the app (/{id}/{section}) + query.
  async function fetchFragment(target) {
    let res;
    try {
      // `x-hub-nav`: page change (navigation analytics in the hub; the gateway drops
      // x-hub-* before the app).
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
    // 502 from the gateway and 404 from the app already come as a friendly fragment.
    return { ok: false, retry: Policy.retriable(res.status, html), html: html.trim() ? html : unavailable() };
  }

  // App restarting: friendly state inside the shell + new attempts until it returns.
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

  // 429 (heavy operation queued in the app): friendly "queued" state with new
  // automatic attempts (Retry-After + jitter, up to QUEUE_MAX); once exhausted, shows the app's warning.
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

  // Mockup loader: 0→100% in LOADER_MS with `1 - (1 - t)^2.2` (the % keeps going even with
  // prefers-reduced-motion; only the bar's shine stops, in the CSS).
  function startLoader() {
    const box = root.querySelector('.app-loading');
    if (!box) return;
    const pct = box.querySelector('.loader-percentage');
    const fill = box.querySelector('.loader-fill');
    const bar = box.querySelector('.loader-track');
    (function tick(now) {
      if (!box.isConnected || box.dataset.hubLoaderDone) return;
      // Clamp: the frame timestamp (rAF) may be earlier than startedAt (would give -1%).
      const ratio = Math.max(0, Math.min((now - startedAt) / LOADER_MS, 1));
      const value = ratio >= 1 ? 100 : Math.floor((1 - Math.pow(1 - ratio, 2.2)) * 100);
      if (pct) pct.textContent = `${value}%`;
      if (fill) fill.style.width = `${value}%`;
      if (bar) bar.setAttribute('aria-valuenow', String(value));
      if (ratio < 1) requestAnimationFrame(tick);
    })(performance.now());
  }

  // End of the loader: the LOADER_MS timer may fire before the animation frame that would reach
  // 100% — closes at 100% and lets one frame paint before swapping.
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

  // Component skeleton (the mockup's): title, metrics, chart, list and table.
  function skeletonMarkup() {
    const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    return '<div class="skeleton-page-heading" aria-hidden="true"><div><i class="skeleton-block eyebrow-skeleton"></i><i class="skeleton-block title-skeleton"></i><i class="skeleton-block subtitle-skeleton"></i></div><i class="skeleton-block action-skeleton"></i></div>'
      + '<div class="skeleton-metric-row" aria-hidden="true"><i class="skeleton-block metric-skeleton"></i><i class="skeleton-block metric-skeleton"></i><i class="skeleton-block metric-skeleton"></i></div>'
      + '<div class="skeleton-panel-row" aria-hidden="true"><section class="skeleton-panel chart-skeleton"><i class="skeleton-block panel-title-skeleton"></i><div class="skeleton-chart-bars"><i></i><i></i><i></i><i></i><i></i><i></i></div></section><section class="skeleton-panel list-skeleton"><i class="skeleton-block panel-title-skeleton"></i><i class="skeleton-block list-line"></i><i class="skeleton-block list-line"></i><i class="skeleton-block list-line"></i><i class="skeleton-block list-line short"></i></section></div>'
      + '<section class="skeleton-panel table-skeleton" aria-hidden="true"><i class="skeleton-block panel-title-skeleton"></i><i class="skeleton-block table-line"></i><i class="skeleton-block table-line"></i><i class="skeleton-block table-line"></i></section>'
      + `<span class="component-loading-caption">${esc(tr('js.loading_app', 'Carregando {app}', { app: appName }))}</span>`;
  }

  // Shows the skeleton in place of the content (with the app sidebar, if there is one already).
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

  // The only HTML path into the document: sanitized (hub-safe.js). Without the sanitizer
  // (hub script did not load) nothing from the app is inserted.
  function setHtml(el, html) {
    if (!window.HubSafe) { el.textContent = ''; return; }
    // The "You are in staging" strip (server, inside #app-root) survives the app content swap.
    const bar = el === root ? root.querySelector(':scope > [data-hub-staging-bar]') : null;
    window.HubSafe.setHTML(el, html, { app });
    if (bar) root.prepend(bar);
  }

  function mounted(scope) {
    // App navigation bar on the phone: "<App> · <Section>" (the button comes from the app).
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
    try { document.dispatchEvent(new CustomEvent('hub:mount', { detail: { root: scope, app } })); } catch (_) { /* none */ }
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

  // Replaces `el` (outerHTML) with the HTML received (sanitized); returns the new nodes.
  function replaceElement(el, html) {
    const frag = window.HubSafe ? window.HubSafe.sanitize(html.trim(), { app }) : document.createDocumentFragment();
    const nodes = [...frag.children];
    el.replaceWith(frag);
    nodes.forEach((n) => mounted(n));
    return nodes;
  }

  // Polling of [data-hub-poll] elements (background job progress).
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
          if (el.isConnected) startPolling(el); // retries at the next interval
        }
      }, every);
    });
  }

  async function open(target) {
    const token = ++navToken;
    try { store('sessionStorage')?.setItem(OPENED_KEY, '1'); } catch (_) { /* private */ }
    startLoader();
    const section = sectionOf(target.split('?')[0]);
    const fragment = Promise.all([fetchFragment(target), assetsReady.catch(() => {})]).then(([r]) => r);
    // Known error before the end of the loader: shows it right away (without holding it for the fixed time).
    const early = await Promise.race([fragment, wait(LOADER_MS - (performance.now() - startedAt)).then(() => null)]);
    if (token !== navToken) return;
    if (early && !early.ok) return showFailure(target, token, early);
    // Fragment ready earlier: the loader still completes the fixed time (owner's decision).
    if (early) await wait(LOADER_MS - (performance.now() - startedAt));
    if (token !== navToken) return;
    // End of the loader → component skeleton; the content comes in after SKELETON_MS
    // (or when the fragment arrives, if it takes longer).
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
    // Same page with another query (search, filter, pagination): no skeleton — but the
    // content (still the old one) does not receive clicks until the new fragment arrives,
    // otherwise a second quick click (e.g. toggling the sorting) hits the old link,
    // which does not yet reflect the change, and the target computed matches what was already
    // pushed — the click is discarded as "no change" (wave 9: the server query became slow
    // enough for this window to really show up).
    // Page change in the app: skeleton for a fixed PAGE_SKELETON_MS (or until the fragment arrives).
    // `quiet` (browser back/forward) cancels the skeleton, as in the mockup.
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
      // Phone: the document is what scrolls (the browser's pull-to-refresh works).
      const docKeep = samePage ? window.scrollY : 0;
      await swap(result.html);
      if (keep) { const s = root.querySelector('.workspace-content'); if (s) s.scrollTop = keep; }
      if (docKeep) window.scrollTo(0, docKeep);
      if (!samePage) focusContent();
    } finally {
      if (samePage) root.style.pointerEvents = '';
    }
  }

  // Accessibility (wave 9): after changing section, focus goes to the title of the
  // new page (screen readers announce the change; the keyboard continues from there).
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

  // App forms (action at /{id}/...): GET becomes navigation; POST goes by XHR.
  document.addEventListener('submit', (e) => {
    const form = e.target.closest('#app-root form');
    if (!form || e.defaultPrevented) return;
    // Target outside the app (sanitizer marked it): never leaves from here.
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

  // Idempotency key per submission (reused in the automatic new attempts):
  // the app returns the same response instead of repeating the action (hub-sdk).
  function idempotencyKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = new Uint8Array(16);
    (window.crypto || {}).getRandomValues ? crypto.getRandomValues(b) : b.forEach((_, i) => { b[i] = Math.floor(Math.random() * 256); });
    return [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  }
  // Transient failures (network, app restarting, gateway without response): new attempt
  // with the SAME key, with increasing wait.
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
      // Only an id (`#id`) inside the app content — never a selector built with app text.
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
