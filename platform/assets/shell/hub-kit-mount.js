/* hub-kit-mount.js — tables (pagination, sorting, filter), tabs, dependent fields, real time
 * (data-hub-ws / data-hub-sse), the mount of every behavior and the event delegation;
 * publishes window.HubKit. Second part of hub-kit.js (split only for size; its header
 * documents the attributes), loaded by a classic tag right after it. */
(function () {
  'use strict';
  const P = window.__hubKitParts;
  if (!P) return;
  const { Pure, frag, inScope, appRoot, ref, t, toast, ownPath, insert, request, confirmDialog,
    guard, openModal, showErrors } = P;
  // ---- tabelas ----
  function rowsOf(container) {
    if (!container) return [];
    if (container.tagName === 'TABLE') return [...container.tBodies].flatMap((b) => [...b.rows]);
    const marked = container.querySelectorAll('[data-hub-filter-item]');
    return marked.length ? [...marked] : [...container.children];
  }
  function paginate(table, page) {
    const size = Number(table.getAttribute('data-hub-page-size')) || 0;
    if (!size) return;
    const rows = rowsOf(table).filter((r) => !r.hasAttribute('data-hub-filtered'));
    const pages = Pure.pages(rows.length, size);
    const cur = Math.min(Math.max(1, page || Number(table.dataset.hubPage) || 1), pages);
    table.dataset.hubPage = String(cur);
    rowsOf(table).forEach((r) => { r.hidden = r.hasAttribute('data-hub-filtered'); });
    rows.forEach((r, i) => { r.hidden = i < (cur - 1) * size || i >= cur * size; });
    let nav = table.nextElementSibling && table.nextElementSibling.matches('nav.hub-pager') ? table.nextElementSibling : null;
    if (!nav) {
      nav = document.createElement('nav');
      nav.className = 'hub-pager';
      nav.setAttribute('aria-label', t('js.kit_pages', 'Paginação'));
      table.after(nav);
    }
    const mk = (label, p, disabled, current) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'secondary-button';
      b.textContent = label;
      b.disabled = disabled;
      if (current) b.setAttribute('aria-current', 'page');
      b.addEventListener('click', () => paginate(table, p));
      return b;
    };
    const info = document.createElement('span');
    info.className = 'hub-muted';
    info.textContent = `${cur} / ${pages}`;
    nav.replaceChildren(mk('‹', cur - 1, cur <= 1), info, mk('›', cur + 1, cur >= pages));
  }
  function sortBy(th) {
    const table = th.closest('table');
    if (!table || !table.tBodies[0]) return;
    const idx = [...th.parentElement.children].indexOf(th);
    const dir = th.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
    th.parentElement.querySelectorAll('th[aria-sort]').forEach((x) => x.removeAttribute('aria-sort'));
    th.setAttribute('aria-sort', dir);
    const kind = th.getAttribute('data-hub-sort') || 'text';
    const body = table.tBodies[0];
    const rows = [...body.rows].sort((a, b) => {
      const c = Pure.compare(a.cells[idx]?.textContent.trim() || '', b.cells[idx]?.textContent.trim() || '', kind);
      return dir === 'ascending' ? c : -c;
    });
    body.append(...rows);
    paginate(table, 1);
  }
  function filter(input) {
    const sel = input.getAttribute('data-hub-filter');
    const container = sel ? ref(sel) : appRoot()?.querySelector('.data-table');
    if (sel && !container) return;
    const scope = container || appRoot();
    const q = Pure.fold(input.value.trim());
    let visible = 0;
    const rows = container ? rowsOf(container) : [];
    rows.forEach((r) => {
      const show = !q || Pure.fold(r.getAttribute('data-hub-filter-text') || r.textContent).includes(q);
      r.toggleAttribute('data-hub-filtered', !show);
      r.hidden = !show;
      if (show) visible++;
    });
    if (container && container.hasAttribute('data-hub-page-size')) paginate(container, 1);
    const e = scope && ref(input.getAttribute('data-hub-filter-empty'));
    if (e) e.hidden = visible > 0;
  }

  // ---- abas ----
  function selectTab(tab) {
    const group = tab.closest('[data-hub-tabs]');
    if (!group) return;
    group.querySelectorAll('[data-hub-tab]').forEach((x) => {
      const on = x === tab;
      x.setAttribute('aria-selected', String(on));
      x.tabIndex = on ? 0 : -1;
      const p = ref(x.getAttribute('data-hub-tab'));
      if (p) p.hidden = !on;
    });
  }

  // ---- selects dependentes ----
  function fillDependent(child) {
    const parent = ref(child.getAttribute('data-hub-depends'));
    if (!parent) return;
    let map = {};
    try { map = JSON.parse(child.getAttribute('data-hub-options') || '{}'); } catch (_) { return; }
    const opts = Array.isArray(map[parent.value]) ? map[parent.value] : [];
    const keep = child.value;
    const first = child.querySelector('option[value=""]');
    child.replaceChildren(...(first ? [first] : []), ...opts.slice(0, 500).map((o) => {
      const [v, l] = Array.isArray(o) ? o : [o, o];
      const op = document.createElement('option');
      op.value = String(v);
      op.textContent = String(l);
      return op;
    }));
    if ([...child.options].some((o) => o.value === keep)) child.value = keep;
    child.disabled = opts.length === 0 && !first;
  }


  // ---- real time (WebSocket through the gateway) ----
  // One socket per URL; the owning element can be swapped for a fragment (outerHTML) — the
  // new element with the same data-hub-ws inherits the socket. No owner for 3 s → closes.
  const sockets = new Map();
  const polls = new WeakMap();
  const wsOwners = (url, attr = 'data-hub-ws') => [...document.querySelectorAll(`[${attr}]`)].filter((e) => e.getAttribute(attr) === url && inScope(e));
  function wsState(st, v) { st.state = v; wsOwners(st.url, st.attr).forEach((o) => o.setAttribute(`${st.attr || 'data-hub-ws'}-state`, v)); }
  function wsPoll(el, on) {
    if (on) {
      if (polls.has(el) || !el.hasAttribute('data-hub-get')) return;
      const ms = Pure.everyMs(el.getAttribute('data-hub-trigger') || '') || 2000;
      polls.set(el, setInterval(() => { if (!el.isConnected) { clearInterval(polls.get(el)); polls.delete(el); return; } request(el); }, ms));
    } else if (polls.has(el)) { clearInterval(polls.get(el)); polls.delete(el); }
  }
  function wsApply(st, data) {
    const a = st.attr || 'data-hub-ws';
    const owner = wsOwners(st.url, a)[0];
    if (!owner) return;
    for (const m of Pure.wsMessages(data)) {
      const tsel = m.target != null ? String(m.target) : owner.getAttribute(`${a}-target`);
      const target = tsel ? ref(tsel) : owner;
      if (m.html != null && target && inScope(target)) insert(target, String(m.html), String(m.swap || owner.getAttribute(`${a}-swap`) || 'outerHTML'));
      else if (m.text != null && target && inScope(target)) target.textContent = String(m.text).slice(0, 2000);
      if (m.event) owner.dispatchEvent(new CustomEvent(a === 'data-hub-sse' ? 'hub:sse' : 'hub:ws', { bubbles: true, detail: { event: String(m.event).slice(0, 64), data: m.data } }));
    }
  }
  function wsConnect(st) {
    st.retry = 0;
    if (!wsOwners(st.url).length) { sockets.delete(st.url); return; }
    let ws;
    try {
      ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/apps${st.path}`);
    } catch (_) { wsLost(st); return; }
    st.ws = ws;
    wsState(st, 'connecting');
    ws.onopen = () => {
      const resync = st.failed || st.opened;
      st.failed = false; st.opened = true; st.attempt = 0;
      wsState(st, 'open');
      wsOwners(st.url).forEach((o) => { wsPoll(o, false); if (resync && o.hasAttribute('data-hub-get')) request(o); });
    };
    ws.onmessage = (e) => { if (typeof e.data === 'string') wsApply(st, e.data); };
    ws.onclose = () => { if (st.ws === ws) { st.ws = null; wsLost(st); } };
  }
  function wsLost(st) {
    if (st.closing) return;
    st.failed = true;
    const owners = wsOwners(st.url);
    if (!owners.length) { sockets.delete(st.url); return; }
    wsState(st, 'polling');
    owners.forEach((o) => wsPoll(o, true));
    st.retry = setTimeout(() => wsConnect(st), Pure.wsBackoff(st.attempt++));
  }
  function wsBind(el) {
    const url = el.getAttribute('data-hub-ws') || '';
    const path = ownPath(url);
    if (!path || !Pure.isWsPath(path) || typeof WebSocket === 'undefined') {
      if (url) console.warn('data-hub-ws fora do app (ou sem WebSocket): polling', el);
      el.setAttribute('data-hub-ws-state', 'polling');
      wsPoll(el, true);
      return;
    }
    let st = sockets.get(url);
    if (!st) { st = { url, path, ws: null, attempt: 0, failed: false, opened: false, state: 'connecting', retry: 0, closing: false }; sockets.set(url, st); wsConnect(st); }
    el.setAttribute('data-hub-ws-state', st.state);
    if (st.failed) wsPoll(el, true);
    if (!sweeper) sweeper = setInterval(wsSweep, 3000);
  }
  let sweeper = 0;
  function wsSweep() {
    for (const st of [...sockets.values()]) {
      if (wsOwners(st.url).length) continue;
      st.closing = true;
      clearTimeout(st.retry);
      try { st.ws && st.ws.close(1000); } catch (_) { /* none */ }
      sockets.delete(st.url);
    }
    for (const st of [...streams.values()]) {
      if (wsOwners(st.url, st.attr).length) continue;
      clearTimeout(st.retry);
      try { st.es && st.es.close(); } catch (_) { /* none */ }
      streams.delete(st.url);
    }
    if (!sockets.size && !streams.size) { clearInterval(sweeper); sweeper = 0; }
  }

  // ---- real time (SSE through the gateway) ----
  // Same message format as data-hub-ws (each `data:` of the event), one EventSource per
  // URL in /_frag; if the stream drops, data-hub-get + data-hub-trigger polls until it comes back.
  const streams = new Map();
  function sseConnect(st) {
    const owners = wsOwners(st.url, st.attr);
    if (!owners.length) { streams.delete(st.url); return; }
    let es;
    try { es = new EventSource(`${frag}${st.path}`); } catch (_) { sseLost(st); return; }
    st.es = es;
    wsState(st, 'connecting');
    es.onopen = () => {
      const resync = st.failed;
      st.failed = false; st.attempt = 0;
      wsState(st, 'open');
      wsOwners(st.url, st.attr).forEach((o) => { wsPoll(o, false); if (resync && o.hasAttribute('data-hub-get')) request(o); });
    };
    es.onmessage = (e) => wsApply(st, e.data);
    // The browser reconnects on its own; an error with the stream closed (status ≠ 200) = us.
    es.onerror = () => { if (es.readyState === 2 && st.es === es) { st.es = null; sseLost(st); } else if (!st.failed) { st.failed = true; wsState(st, 'polling'); wsOwners(st.url, st.attr).forEach((o) => wsPoll(o, true)); } };
  }
  function sseLost(st) {
    st.failed = true;
    const owners = wsOwners(st.url, st.attr);
    if (!owners.length) { streams.delete(st.url); return; }
    wsState(st, 'polling');
    owners.forEach((o) => wsPoll(o, true));
    st.retry = setTimeout(() => sseConnect(st), Pure.wsBackoff(st.attempt++));
  }
  function sseBind(el) {
    const url = el.getAttribute('data-hub-sse') || '';
    const path = ownPath(url);
    if (!path || typeof EventSource === 'undefined') {
      if (url) console.warn('data-hub-sse fora do app (ou sem EventSource): polling', el);
      el.setAttribute('data-hub-sse-state', 'polling');
      wsPoll(el, true);
      return;
    }
    let st = streams.get(url);
    if (!st) { st = { url, path, attr: 'data-hub-sse', es: null, attempt: 0, failed: false, state: 'connecting', retry: 0 }; streams.set(url, st); sseConnect(st); }
    el.setAttribute('data-hub-sse-state', st.state);
    if (st.failed) wsPoll(el, true);
    if (!sweeper) sweeper = setInterval(wsSweep, 3000);
  }

  // ---- building a new fragment ----
  const timers = new WeakMap();
  function mount(scope) {
    if (!scope || !scope.querySelectorAll) return;
    const all = (sel) => (scope.matches && scope.matches(sel) ? [scope] : []).concat([...scope.querySelectorAll(sel)]);
    all('[data-hub-charts]').forEach((n) => {
      try { JSON.parse(n.getAttribute('data-hub-charts')).forEach((cfg) => window.TimeSeriesChart && window.TimeSeriesChart.render(cfg)); } catch (err) { console.error('data-hub-charts inválido', err); }
    });
    if (window.TimeSeriesChart) window.TimeSeriesChart.mountAll(scope.parentNode || scope);
    all('[data-hub-chart]').forEach((n) => {
      try { window.HubChart && window.HubChart.draw(n, JSON.parse(n.getAttribute('data-hub-chart'))); } catch (err) { console.error('data-hub-chart inválido', err); }
    });
    all('table[data-hub-page-size]').forEach((tb) => paginate(tb, 1));
    all('[data-hub-tabs]').forEach((g) => { const cur = g.querySelector('[data-hub-tab][aria-selected="true"]') || g.querySelector('[data-hub-tab]'); if (cur) selectTab(cur); });
    all('select[data-hub-depends]').forEach(fillDependent);
    all('[data-hub-ws]').forEach(wsBind);
    all('[data-hub-sse]').forEach(sseBind);
    all('[data-hub-get], [data-hub-post]').forEach((el) => {
      const trig = el.getAttribute('data-hub-trigger') || '';
      if (/(^|\s)load(\s|$)/.test(trig)) request(el);
      const every = Pure.everyMs(trig);
      // With data-hub-ws/-sse, polling is the fallback: it only starts when the stream drops (wsPoll).
      if (every && !timers.has(el) && !el.hasAttribute('data-hub-ws') && !el.hasAttribute('data-hub-sse')) {
        const id = setInterval(() => { if (!el.isConnected) { clearInterval(id); return; } request(el); }, every);
        timers.set(el, id);
      }
    });
  }

  const triggerOf = (el) => el.getAttribute('data-hub-trigger') || Pure.defaultTrigger(el.tagName.toLowerCase(), (el.getAttribute('type') || '').toLowerCase());
  const wants = (el, kind) => triggerOf(el).split(/\s*,\s*|\s+/).includes(kind);

  // ---- delegation ----
  document.addEventListener('keydown', (e) => {
    const ws = document.getElementById('workspace');
    if (e.key !== 'Escape' || !ws?.classList.contains('menu-open')) return;
    ws.classList.remove('menu-open');
    const bar = ws.querySelector('[data-hub-menu="open"], [data-mobile-menu]');
    bar?.setAttribute('aria-expanded', 'false');
    bar?.focus();
  });
  document.addEventListener('click', (e) => {
    const t0 = e.target;
    if (!inScope(t0)) return;
    const c = t0.closest('[data-hub-confirm]');
    if (c && c.tagName !== 'FORM' && inScope(c) && !(c.type === 'submit' && c.form)) {
      if (guard(e, c, () => c.click())) return;
    }
    // Choosing a section (or tapping the background) closes the app's navigation panel.
    if (t0.closest('.app-sidebar a[href], .mobile-backdrop')) {
      const ws = document.getElementById('workspace');
      if (ws?.classList.contains('menu-open')) {
        ws.classList.remove('menu-open');
        ws.querySelectorAll('[data-hub-menu="open"], [data-mobile-menu]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
      }
    }
    const menu = t0.closest('[data-hub-menu], [data-mobile-menu], [data-mobile-close]');
    if (menu) {
      const ws = document.getElementById('workspace');
      // The "open" button toggles (on phones the app bar opens and closes the sections panel).
      const open = (menu.getAttribute('data-hub-menu') === 'open' || menu.hasAttribute('data-mobile-menu')) && !ws?.classList.contains('menu-open');
      ws?.classList.toggle('menu-open', open);
      ws?.querySelectorAll('[data-hub-menu="open"], [data-mobile-menu]').forEach((b) => b.setAttribute('aria-expanded', String(open)));
      return;
    }
    const ext = t0.closest('a[data-hub-external]');
    if (ext) {
      if (guard(e, ext, () => ext.click(), `${t('js.kit_leave', 'Você vai sair do hub para')} ${ext.getAttribute('data-hub-external')}`)) return;
      return;
    }
    const modal = t0.closest('[data-hub-modal]');
    if (modal) { e.preventDefault(); openModal(modal); return; }
    const sort = t0.closest('th[data-hub-sort]');
    if (sort) { sortBy(sort); return; }
    const tab = t0.closest('[data-hub-tab]');
    if (tab) { e.preventDefault(); selectTab(tab); return; }
    const copy = t0.closest('[data-hub-copy], [data-hub-copy-from]');
    if (copy) {
      e.preventDefault();
      const from = copy.getAttribute('data-hub-copy-from');
      const src = from && ref(from);
      const text = copy.hasAttribute('data-hub-copy') ? copy.getAttribute('data-hub-copy') : src ? ('value' in src ? src.value : src.textContent) : '';
      const done = () => toast(copy.getAttribute('data-hub-toast') || t('js.kit_copied', 'Copiado para a área de transferência.'));
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => toast(t('js.kit_copy_fail', 'Não foi possível copiar.')));
      return;
    }
    const act = t0.closest('[data-hub-get], [data-hub-post]');
    if (act && act.tagName !== 'FORM' && wants(act, 'click')) { e.preventDefault(); request(act); return; }
    const tst = t0.closest('[data-hub-toast]');
    if (tst && tst.tagName !== 'FORM') { e.preventDefault(); toast(tst.getAttribute('data-hub-toast')); }
  }, true);

  document.addEventListener('keydown', (e) => {
    const tab = e.target.closest && e.target.closest('[data-hub-tab]');
    if (!tab || !inScope(tab) || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const tabs = [...tab.closest('[data-hub-tabs]').querySelectorAll('[data-hub-tab]')];
    const i = tabs.indexOf(tab);
    const next = e.key === 'Home' ? tabs[0] : e.key === 'End' ? tabs[tabs.length - 1] : tabs[(i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
    e.preventDefault();
    selectTab(next);
    next.focus();
  });

  // Capture: runs before hub.js (which submits the app's forms).
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (!inScope(form)) return;
    if (form.hasAttribute('data-hub-blocked')) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    const sub = e.submitter;
    if (sub && sub.hasAttribute && sub.hasAttribute('data-hub-blocked')) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (form.hasAttribute('data-hub-validate') && !showErrors(form)) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    const conf = (sub && sub.getAttribute && sub.getAttribute('data-hub-confirm') && sub) || (form.hasAttribute('data-hub-confirm') && form);
    if (conf && guard(e, conf, () => HTMLFormElement.prototype.requestSubmit.call(form, sub || undefined))) return;
    if (form.hasAttribute('data-hub-dismiss')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      form.closest('[data-hub-layer]')?.remove();
      if (form.dataset.hubToast) toast(form.dataset.hubToast);
      return;
    }
    if (form.hasAttribute('data-hub-get') || form.hasAttribute('data-hub-post')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      request(form, e);
    }
  }, true);

  document.addEventListener('input', (e) => {
    const el = e.target;
    if (!inScope(el)) return;
    if (el.matches('[data-hub-filter], [data-table-search]')) { filter(el); return; }
    if (el.closest('form[data-hub-validate]') && el.hasAttribute('aria-invalid')) showErrors(el.form);
    if ((el.hasAttribute('data-hub-get') || el.hasAttribute('data-hub-post')) && wants(el, 'input')) {
      clearTimeout(timers.get(el));
      timers.set(el, setTimeout(() => request(el), 300));
    }
  });

  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!inScope(el)) return;
    document.querySelectorAll('#app-root select[data-hub-depends]').forEach((c) => {
      const p = ref(c.getAttribute('data-hub-depends'));
      if (p === el) fillDependent(c);
    });
    if ((el.hasAttribute('data-hub-get') || el.hasAttribute('data-hub-post')) && wants(el, 'change')) request(el);
  });

  P.mount = mount; // hub-kit.js (insert, modals) calls mount through P: it loads before this file
  window.HubKit = Object.freeze({ mount, toast, insert, confirm: confirmDialog, pure: Pure });
  delete window.__hubKitParts;
})();
