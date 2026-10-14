/* hub-ux-pages.js — row menus, service worker registration, skeleton between hub pages and the
 * Administration page tabs/drawer/filter. Kept apart from hub-ux.js for size; loaded after
 * hub-ux-colfilter.js on the same pages. */

/* ---- <details class="hub-row-menu"> menus: ↑/↓ arrows move through the items; when opened by the
 * keyboard, focus goes to the first item. ---- */
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const items = (d) => [...d.querySelectorAll('.hub-row-menu-list button:not(:disabled), .hub-row-menu-list a[href]')];
  document.addEventListener('keydown', (e) => {
    const d = e.target.closest && e.target.closest('details.hub-row-menu');
    if (!d || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return;
    if (!d.open) { if (e.key === 'ArrowDown' && e.target.tagName === 'SUMMARY') { e.preventDefault(); d.open = true; const f = items(d)[0]; if (f) f.focus(); } return; }
    const list = items(d);
    if (!list.length) return;
    e.preventDefault();
    const i = list.indexOf(document.activeElement);
    const n = e.key === 'ArrowDown' ? (i + 1) % list.length : (i <= 0 ? list.length - 1 : i - 1);
    list[n].focus();
  });

  // Wave 13 (PWA): the hub's service worker (shell + offline page; never caches authenticated responses).
  // Only in a secure context (HTTPS or localhost).
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => { navigator.serviceWorker.register('/hub-sw.js', { scope: '/' }).catch(() => {}); });
  }
})();

/* ---- Skeleton when navigating between hub pages (Home, Applications, Status, account, Administration):
 * when a hub link is clicked, the content becomes the page skeleton (header + cards or
 * table, same width as `.page-container`: no layout jump) until the server responds.
 * Without JS, navigation is the normal one. Animation off under `prefers-reduced-motion` (hub.css). ---- */
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const root = document.getElementById('app-root');
  const body = document.body;
  if (!root || !body || body.dataset.hubUx !== '1') return;
  let T = {};
  try { T = JSON.parse(body.dataset.hubI18n || '{}'); } catch (_) { T = {}; }
  const CARDS = /^\/(applications|status|account\/settings|admin)?$/;
  const hubPage = (p) => p === '/' || /^\/(applications|status|inbox|search|account\/(settings|tokens|authorized-apps))$/.test(p) || /^\/admin(\/[a-z-]+)?$/.test(p);
  const el = (cls, tag, kids) => {
    const n = document.createElement(tag || 'i');
    n.className = cls;
    (kids || []).forEach((k) => n.appendChild(k));
    return n;
  };
  const blk = (cls) => el('skeleton-block ' + cls);
  const many = (n, f) => Array.from({ length: n }, f);
  function build(path) {
    const page = el('page-container component-loading hub-page-skeleton', 'div');
    page.setAttribute('role', 'status');
    page.setAttribute('aria-busy', 'true');
    page.appendChild(el('skeleton-page-heading', 'div', [el('', 'div', [blk('eyebrow-skeleton'), blk('title-skeleton'), blk('subtitle-skeleton')]), blk('action-skeleton')]));
    if (CARDS.test(path)) {
      page.appendChild(el('skeleton-metric-row', 'div', many(3, () => blk('metric-skeleton'))));
      page.appendChild(el('skeleton-panel-row', 'div', [
        el('skeleton-panel chart-skeleton', 'section', [blk('panel-title-skeleton'), el('skeleton-chart-bars', 'div', many(6, () => el('', 'i')))]),
        el('skeleton-panel list-skeleton', 'section', [blk('panel-title-skeleton'), ...many(4, () => blk('list-line'))]),
      ]));
    } else {
      page.appendChild(el('skeleton-panel table-skeleton hub-table-sk', 'section', [blk('panel-title-skeleton'), ...many(8, () => blk('table-line'))]));
    }
    const cap = el('component-loading-caption', 'span');
    cap.textContent = T['js.loading_page'] || 'Carregando página';
    page.appendChild(cap);
    return page;
  }
  let saved = null;
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if ((a.target && a.target !== '_self') || a.hasAttribute('download') || a.hasAttribute('data-hub-no-skeleton')) return;
    let u;
    try { u = new URL(a.href, location.href); } catch (_) { return; }
    if (u.origin !== location.origin || !hubPage(u.pathname)) return;
    if (u.pathname === location.pathname && u.search === location.search) return;
    setTimeout(() => {
      if (e.defaultPrevented || saved) return;
      saved = document.createDocumentFragment();
      while (root.firstChild) saved.appendChild(root.firstChild);
      root.appendChild(build(u.pathname));
      // navigation cancelled/no response: returns the page
      setTimeout(() => { if (saved) { root.textContent = ''; root.appendChild(saved); saved = null; } }, 15000);
    }, 0);
  });
  window.addEventListener('pageshow', (e) => { if (e.persisted && saved) location.reload(); });
  document.addEventListener('change', (e) => {
    const s = e.target;
    if (s && s.matches && s.matches('select[data-hub-autosubmit]') && s.form) s.form.submit();
  });
})();

// ---- Page tabs, row details drawer and multi-select filter (Administration) ----
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  function pick(t, focus, sync) {
    $$('[role=tab]', t.closest('[data-ptabs]')).forEach((x) => {
      const on = x === t;
      x.setAttribute('aria-selected', String(on));
      x.tabIndex = on ? 0 : -1;
      const p = document.getElementById(x.getAttribute('aria-controls'));
      if (p) p.hidden = !on;
    });
    if (focus) t.focus();
    if (sync) { const u = new URL(location.href); u.searchParams.set('tab', t.dataset.ptab); u.hash = ''; history.replaceState(null, '', u); }
  }
  $$('[data-ptabs]').forEach((g) => {
    const tabs = $$('[role=tab]', g);
    const key = new URLSearchParams(location.search).get('tab') || location.hash.slice(1);
    pick(tabs.find((x) => x.dataset.ptab === key) || tabs[0], false, false);
    tabs.forEach((x, i) => {
      x.addEventListener('click', () => pick(x, false, true));
      x.addEventListener('keydown', (e) => {
        const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (d) { e.preventDefault(); pick(tabs[(i + d + tabs.length) % tabs.length], true, true); }
      });
    });
  });

  const dr = document.querySelector('[data-rowdrawer]');
  if (dr) {
    const bd = document.querySelector('[data-rowdrawer-backdrop]');
    const body = dr.querySelector('[data-rowdrawer-body]');
    let opener = null;
    const close = () => { dr.hidden = true; bd.hidden = true; document.body.classList.remove('ops-drawer-open'); if (opener && opener.isConnected) opener.focus(); };
    const open = (tr) => {
      const src = tr.querySelector('.row-detail-src');
      if (!src) return;
      opener = tr;
      dr.querySelector('[data-rowdrawer-title]').textContent = tr.dataset.detailTitle || '';
      body.replaceChildren(...[...src.cloneNode(true).childNodes]);
      dr.hidden = false; bd.hidden = false;
      document.body.classList.add('ops-drawer-open');
      dr.querySelector('[data-rowdrawer-close]').focus();
    };
    document.addEventListener('click', (e) => {
      if (e.target.closest('[data-rowdrawer-close],[data-rowdrawer-backdrop]')) { close(); return; }
      const tr = e.target.closest('tr[data-detail-row]');
      if (tr && !e.target.closest('button,a,details,input,select,form')) open(tr);
    });
    document.addEventListener('keydown', (e) => {
      const tr = e.target.closest && e.target.closest('tr[data-detail-row]');
      if (tr && e.target === tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); open(tr); }
      if (e.key === 'Escape' && !dr.hidden && !document.querySelector('dialog[open]')) close();
    });
  }

  document.addEventListener('change', (e) => {
    const m = e.target.closest && e.target.closest('[data-multi]');
    if (!m) return;
    const on = $$('input:checked', m).map((i) => i.value);
    const lab = m.querySelector('[data-multi-label]');
    lab.textContent = on.length ? `${lab.dataset.base} (${on.length})` : lab.dataset.base;
    $$(`${m.dataset.multi} tr[data-apps]`).forEach((r) => r.classList.toggle('multi-off', on.length > 0 && !r.dataset.apps.split(' ').some((a) => on.includes(a))));
  });
})();
