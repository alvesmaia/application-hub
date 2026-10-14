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
  // Private scope shared with hub-nav.js, loaded right after this one; removed from window
  // once hub-nav.js has run.
  const P = (window.__hubShellParts = {});
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

  function unavailable() {
    return '<section class="empty-state"><h1>Aplicação indisponível</h1><p>A aplicação não respondeu. Tente novamente em instantes.</p></section>';
  }

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
  Object.assign(P, { Policy, me, app, root, LOADER_MS, SKELETON_MS, PAGE_SKELETON_MS, store,
    OPENED_KEY, startedAt, tr, appName, wait, pub, canon, sectionOf, fragUrl, toLogin, assetsReady,
    unavailable, fetchFragment });
})();
