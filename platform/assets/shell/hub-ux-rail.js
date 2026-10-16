/* hub-ux-rail.js — live inbox counter and the platform rail (expanded/collapsed/hidden, ☰ menu
 * on a narrow screen, collapsible admin sections), plus the development-mode reload. Last part
 * of hub-ux.js (split only for size), loaded after hub-ux-palette.js. */
(function () {
  'use strict';
  const P = window.__hubUxParts;
  if (!P) return;
  const { body, T, t, inApp, announce } = P;
  // ---- Live inbox ---------------------------------------------------------------
  const badge = document.querySelector('[data-hub-unread]');
  const bell = document.querySelector('.hub-notif-menu');
  function setUnread(n) {
    if (!badge || !bell) return;
    const prev = Number(bell.dataset.hubNotifications || 0);
    bell.dataset.hubNotifications = String(n);
    badge.textContent = String(Math.min(n, 99));
    badge.classList.toggle('hidden', n <= 0);
    const s = bell.querySelector('summary');
    if (s) s.setAttribute('aria-label', (T['shell.inbox_label'] || 'Inbox: {n}').replace('{n}', n));
    const u = document.querySelector('[data-inbox-unread]');
    if (u) u.textContent = u.textContent.replace(/\d+\s*$/, String(n));
    if (n > prev) announce(t(n - prev === 1 ? 'js.new_items_one' : 'js.new_items', { n: n - prev }));
    document.dispatchEvent(new CustomEvent('hub:inbox', { detail: { unread: n, prev } }));
  }
  if (!inApp && bell && typeof EventSource !== 'undefined') {
    const S = window.HubLogShare;
    const onLine = (d) => { if (d && typeof d.unread === 'number') setUnread(d.unread); };
    const share = S
      ? S.createLogShare({ name: 'hub-inbox', event: 'inbox', openSource: () => new EventSource('/api/me/stream'), onLine })
      : (() => {
        let src = null;
        return {
          start() { src = new EventSource('/api/me/stream'); src.addEventListener('inbox', (ev) => onLine(JSON.parse(ev.data))); },
          stop() { if (src) src.close(); },
        };
      })();
    share.start();
    window.addEventListener('pagehide', () => share.stop());
  }
  // ---- Platform rail: expanded / collapsed / hidden ------------------------------
  // The button at the start of the header (and Ctrl+B outside text fields) toggles the 3 modes in
  // a cycle. The choice is kept separately inside apps (`app`) and outside them (`hub`): localStorage
  // (failures ignored) mirrored in the `hub_rail_<ctx>` cookie, which the server uses to
  // already draw the next page in the right mode.
  const NARROW_Q = '(max-width: 1023px), (pointer: coarse) and (max-height: 600px)';
  (function railToggle() {
    const btn = document.querySelector('[data-hub-rail-toggle]');
    if (!btn) return;
    const ctx = btn.dataset.railCtx === 'app' ? 'app' : 'hub';
    const MODES = ['expanded', 'collapsed', 'hidden'];
    const key = `hub-rail-mode:${ctx}`;
    const current = () => MODES.find((m) => body.classList.contains(`rail-${m}`)) || (ctx === 'app' ? 'hidden' : 'expanded');
    const nextOf = (m) => MODES[(MODES.indexOf(m) + 1) % MODES.length];
    function apply(m, persist) {
      MODES.forEach((x) => body.classList.toggle(`rail-${x}`, x === m));
      btn.setAttribute('aria-expanded', String(m !== 'hidden'));
      const label = `${t('js.rail_menu')}: ${t(`js.rail_${m}`)}; ${t('js.rail_next', { next: t(`js.rail_${nextOf(m)}`) })}`;
      btn.setAttribute('aria-label', label);
      btn.title = label;
      if (!persist) return;
      try { localStorage.setItem(key, m); } catch (_) { /* no storage */ }
      try { document.cookie = `hub_rail_${ctx}=${m}; path=/; max-age=31536000; samesite=lax`; } catch (_) { /* no cookies */ }
    }
    let saved = null;
    try { saved = localStorage.getItem(key); } catch (_) { saved = null; }
    // Cookie lost (or another browser) but the choice is in localStorage: reapplies it.
    if (MODES.includes(saved) && saved !== current()) apply(saved, true);
    else apply(current(), false);
    function cycle() {
      const m = nextOf(current());
      apply(m, true);
      announce(`${t('js.rail_menu')}: ${t(`js.rail_${m}`)}`);
    }
    btn.addEventListener('click', cycle);
    document.addEventListener('keydown', (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || (e.key || '').toLowerCase() !== 'b') return;
      if (window.matchMedia(NARROW_Q).matches) return; // no rail on a narrow screen
      const el = e.target;
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
      e.preventDefault();
      cycle();
    });
  })();

  // ---- Menu ☰ on a narrow screen (NARROW_Q, same query as hub.css) ----------------------------------------------------
  // The rail does not show on phones: the "menu" button (☰, right corner of the header) opens the same
  // `#hub-rail` as a panel from top to bottom just below the header (`body.hub-drawer-open`),
  // over a darkened background; the icon turns into ✕. Closes on ✕, when tapping an item or the background, with
  // Esc or when going back to a wide screen; traps focus (button + panel) and returns it to the button.
  (function mobileDrawer() {
    const btn = document.querySelector('[data-hub-drawer-toggle]');
    const rail = document.getElementById('hub-rail');
    if (!btn || !rail) return;
    const mq = window.matchMedia(NARROW_Q);
    const MODES = ['expanded', 'collapsed', 'hidden'];
    let backdrop = null;
    let saved = null;
    const isOpen = () => body.classList.contains('hub-drawer-open');
    const header = document.querySelector('.platform-header');
    const setHeaderH = () => document.documentElement.style.setProperty('--hub-header-h', `${Math.round(header?.getBoundingClientRect().height || 41)}px`);
    setHeaderH();
    window.addEventListener('resize', setHeaderH);
    const focusables = () => [btn].concat(Array.from(rail.querySelectorAll('a[href], button:not([disabled]), select, input, [tabindex]:not([tabindex="-1"])'))
      .filter((el) => el.getClientRects().length > 0));
    let zSaved = '';
    let zPrio = '';
    function setOpen(open, restoreFocus = true) {
      if (open === isOpen()) return;
      body.classList.toggle('hub-drawer-open', open);
      btn.setAttribute('aria-expanded', String(open));
      btn.setAttribute('aria-label', (open ? btn.dataset.labelClose : btn.dataset.labelOpen) || '');
      if (open) {
        // Inside apps hub.js fixes the rail's z-index inline (!important, app containment);
        // with the drawer open it must stay above the darkened background.
        zSaved = rail.style.getPropertyValue('z-index');
        zPrio = rail.style.getPropertyPriority('z-index');
        if (zSaved) rail.style.setProperty('z-index', 'calc(var(--z-overlay) + 1)', 'important');
        // The drawer uses the look of the expanded rail (without saving the preference).
        document.documentElement.style.setProperty('--hub-header-h', `${Math.round(header?.getBoundingClientRect().bottom || 41)}px`);
        saved = MODES.find((m) => body.classList.contains(`rail-${m}`)) || null;
        MODES.forEach((m) => body.classList.toggle(`rail-${m}`, m === 'expanded'));
        backdrop = document.createElement('div');
        backdrop.className = 'hub-drawer-backdrop';
        backdrop.addEventListener('click', () => setOpen(false));
        body.appendChild(backdrop);
        rail.setAttribute('role', 'dialog');
        rail.setAttribute('aria-modal', 'true');
        rail.querySelectorAll('.rail-group').forEach((g) => {
          if (g.querySelector('.rail-subitem.active')) g.querySelector('[data-rail-group-toggle]')?.setAttribute('aria-expanded', 'true');
        });
        rail.scrollTop = 0;
        (rail.querySelector('[aria-current="page"]') || focusables()[1])?.focus({ preventScroll: false });
      } else {
        if (saved) MODES.forEach((m) => body.classList.toggle(`rail-${m}`, m === saved));
        if (zSaved) rail.style.setProperty('z-index', zSaved, zPrio);
        backdrop?.remove();
        backdrop = null;
        rail.removeAttribute('role');
        rail.removeAttribute('aria-modal');
        if (restoreFocus) btn.focus();
      }
    }
    btn.addEventListener('click', () => setOpen(!isOpen()));
    document.addEventListener('keydown', (e) => {
      if (!isOpen()) return;
      if (e.key === 'Escape') { e.preventDefault(); setOpen(false); return; }
      if (e.key !== 'Tab') return;
      const f = focusables();
      if (!f.length) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (!rail.contains(document.activeElement) && document.activeElement !== btn) { e.preventDefault(); first.focus(); } else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    rail.addEventListener('click', (e) => { if (isOpen() && e.target.closest('a[href]')) setOpen(false, false); });
    mq.addEventListener('change', () => { if (!mq.matches) setOpen(false, false); });
    window.addEventListener('pageshow', () => setOpen(false, false));
    window.addEventListener('popstate', () => setOpen(false, false));
  })();

  // ---- Administrative sections of the rail (collapsible) -----------------------------------
  // Per-user state in localStorage (failures ignored); the current page's section
  // always opens on load; the others start closed. In the collapsed rail each section shows only the icon (with a hint) and
  // dividers between the sections; collapsing only applies to the expanded rail.
  (function railGroups() {
    const menu = document.getElementById('hub-admin-menu');
    if (!menu) return;
    const who = (document.querySelector('.user-copy strong')?.textContent || '').trim();
    const key = `hub-admin-groups:${who}`;
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (_) { saved = {}; }
    const set = (btn, open) => btn.setAttribute('aria-expanded', String(open));
    menu.querySelectorAll('[data-rail-group]').forEach((g) => {
      const btn = g.querySelector('[data-rail-group-toggle]');
      if (!btn) return;
      const hasActive = !!g.querySelector('.rail-subitem.active');
      // Default: only the current page's section open (the rail fits at 1280×720); the
      // user's choice (open/close) is remembered.
      set(btn, hasActive || saved[g.dataset.railGroup] === true);
      btn.addEventListener('click', () => {
        const open = btn.getAttribute('aria-expanded') !== 'true';
        set(btn, open);
        saved[g.dataset.railGroup] = open;
        try { localStorage.setItem(key, JSON.stringify(saved)); } catch (_) { /* no storage */ }
      });
    });
  })();
  delete window.__hubUxParts;
})();

/* Development mode (scripts/dev-mode.sh reload-on): only when the server sets
 * <meta name="hub-dev-reload"> (HUB_DEV_MODE=1 + HUB_DEV_RELOAD=1; never in production).
 * Listens to the SSE /_dev/reload: "reload" (asset changed/app restarted) reloads the page;
 * "hello" carries the hub's start id — if it changed since the first connection, the hub
 * was restarted (new binary) and the page also reloads. Reconnection of the EventSource itself. */
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const meta = document.querySelector('meta[name="hub-dev-reload"]');
  if (!meta || typeof EventSource === 'undefined') return;
  let boot = null;
  const es = new EventSource(meta.getAttribute('content') || '/_dev/reload');
  es.addEventListener('hello', (e) => {
    if (boot === null) boot = e.data;
    else if (e.data !== boot) location.reload();
  });
  es.addEventListener('reload', () => location.reload());
})();
