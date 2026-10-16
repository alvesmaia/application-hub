/* hub-nav.js — app page states (loader, skeleton, restarting, queued), fragment swap,
 * polling, in-app navigation and form submission. Second part of hub.js (split only for
 * size; its header documents the behavior), loaded by a classic tag right after it. */
(function () {
  'use strict';
  const P = window.__hubShellParts;
  if (!P) return;
  const { Policy, me, app, root, LOADER_MS, SKELETON_MS, PAGE_SKELETON_MS, store, OPENED_KEY,
    startedAt, tr, appName, wait, pub, canon, sectionOf, fragUrl, toLogin, assetsReady, unavailable,
    fetchFragment } = P;
  let navToken = 0;
  // In-flight/rendered target (wave 9 bug): `location` already changes in pushState, before the
  // fragment arrives — comparing the click with `location` leaves a click during that
  // window (e.g. toggling the sorting very fast) with no effect, because the link still
  // shown (not re-rendered) points to the same target that was just pushed.
  // `pendingTarget` follows what is actually in flight/rendered on the screen.
  let pendingTarget = location.pathname + location.search;

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
          if (progress) progress.textContent = `Conexão instável, tentando de novo (${attempt + 1}/${RETRY_DELAYS.length})…`;
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
  delete window.__hubShellParts;
})();
