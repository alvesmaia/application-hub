/* hub-ux.js — shell experience (wave 9), on every hub page:
 *
 *  - Global search (palette ⌘K / Ctrl+K): modal dialog with a combobox + grouped listbox
 *    (ARIA 1.2), navigation by arrows/Home/End/Enter/Esc, focus trapped in the dialog and
 *    returned on close, recent searches. On the hub pages it queries
 *    `/api/me/search` (platform + federated apps); inside an app the CSP only lets it talk
 *    to the app's gateway, so the palette uses the local index (apps and the user's pages,
 *    published in `data-hub-palette`) and offers "Search in all applications"
 *    (opens `/search?q=`).
 *  - Guided tour: steps according to what the user sees (`[data-tour]`), dialog with focus,
 *    Back/Next/Skip, Esc interrupts (resumed later), state on the server
 *    (`/api/me/onboarding`). Starts by itself on the first visit to the Home page.
 *  - Live inbox: bell counter updated by the SSE connection shared between tabs
 *    (`logshare.js`, browser lock + BroadcastChannel) and announced in
 *    `aria-live`.
 *  - <details> menus in the header: Esc closes and returns focus; opening one closes the other.
 *
 * No dependencies; no inline script (CSP). Texts in `data-hub-i18n`.
 */
(function () {
  'use strict';

  // ---- Pure logic (tested in node: `require('hub-ux.js')`) --------------------------
  const Pure = {
    fold(s) {
      return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    },
    /** Relevance (same rule as the server): exact 1, starts with 0.8, word 0.6, contains 0.4. */
    score(text, q) {
      const t = Pure.fold(text);
      const n = Pure.fold(q).trim();
      if (!n) return 0;
      if (t === n) return 1;
      if (t.startsWith(n)) return 0.8;
      if (t.split(/[^\p{L}\p{N}]+/u).some((w) => w.startsWith(n))) return 0.6;
      if (t.includes(n)) return 0.4;
      return 0;
    },
    /** Local results (palette index) for `q`, best first. */
    local(index, q, max = 8) {
      return (index || [])
        .map((h) => ({ ...h, score: Math.max(Pure.score(h.title, q), Pure.score(h.subtitle, q) * 0.8) }))
        .filter((h) => h.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, max);
    },
    /** Next active index in the list (arrows, Home, End), wrapping around to the start/end. */
    move(cur, len, key) {
      if (!len) return -1;
      if (key === 'Home') return 0;
      if (key === 'End') return len - 1;
      if (key === 'ArrowDown') return cur < 0 ? 0 : (cur + 1) % len;
      if (key === 'ArrowUp') return cur <= 0 ? len - 1 : cur - 1;
      return cur;
    },
    /** List of recent searches (newest first, no repeats, up to `max`). */
    remember(list, q, max = 8) {
      const v = String(q || '').trim();
      if (!v) return list || [];
      return [v, ...(list || []).filter((x) => x !== v)].slice(0, max);
    },
    /** Tour steps that exist on the page, in canonical order. */
    tourSteps(available) {
      const order = ['home', 'apps', 'search', 'inbox', 'user', 'admin'];
      return order.filter((k) => available.includes(k));
    },
    /** Index to resume the tour from the saved state (`step:N`). */
    resumeAt(state, total) {
      const m = /^step:(\d+)$/.exec(state || '');
      const n = m ? Number(m[1]) : 0;
      return Math.min(Math.max(n, 0), Math.max(total - 1, 0));
    },
  };
  if (typeof module !== 'undefined' && module.exports && typeof document === 'undefined') {
    module.exports = Pure;
    return;
  }

  const body = document.body;
  if (!body || body.dataset.hubUx !== '1') return;
  // Private scope shared with hub-ux-palette.js and hub-ux-rail.js, loaded right after this
  // one; removed from window once hub-ux-rail.js has run.
  const P = (window.__hubUxParts = {});
  let T = {};
  try { T = JSON.parse(body.dataset.hubI18n || '{}'); } catch (_) { T = {}; }
  const t = (k, vars) => {
    let s = T[k] || k;
    if (vars) for (const [a, b] of Object.entries(vars)) s = s.replace(`{${a}}`, String(b));
    return s;
  };
  const inApp = body.classList.contains('in-app');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let paletteIndex = [];
  try { paletteIndex = JSON.parse(body.dataset.hubPalette || '[]'); } catch (_) { paletteIndex = []; }
  const live = document.getElementById('hub-live');
  const announce = (msg) => { if (live) { live.textContent = ''; setTimeout(() => { live.textContent = msg; }, 50); } };
  const store = () => { try { return window.localStorage; } catch (_) { return null; } };

  async function api(method, url, data) {
    const res = await fetch(url, {
      method,
      credentials: 'same-origin',
      headers: data ? { 'content-type': 'application/json', accept: 'application/json' } : { accept: 'application/json' },
      body: data ? JSON.stringify(data) : undefined,
    });
    if (res.status === 204) return {};
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || t('js.error'));
    return j;
  }
  // Resource usage (analytics); inside apps the CSP does not allow it — ignored.
  function feature(name) {
    if (inApp) return;
    api('POST', '/api/me/analytics', { feature: name }).catch(() => {});
  }
  // "Final" search (analytics + recents + "Use the search" step): same-origin POST —
  // the search GET has no side effects.
  function recordSearch(q) {
    if (inApp || !q) return;
    fetch('/api/me/search', {
      method: 'POST',
      credentials: 'same-origin',
      // No keepalive: the page stays open, and a keepalive fetch never gives the network
      // as idle in Chromium (/search?q=a never reached networkidle).
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ q }),
    }).catch(() => {});
  }
  const recordForm = document.querySelector('[data-hub-search-record]');
  if (recordForm && recordForm.dataset.hubSearchRecord) recordSearch(recordForm.dataset.hubSearchRecord);
  window.HubUx = { api, t, announce, feature, inApp };

  // ---- Header menus (<details>) ---------------------------------------------------
  const menus = [...document.querySelectorAll('.platform-header details')];
  menus.forEach((d) => {
    d.addEventListener('toggle', () => {
      if (d.open) {
        menus.forEach((o) => { if (o !== d) o.open = false; });
        if (d.classList.contains('hub-notif-menu')) feature('inbox_open');
      }
    });
    d.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && d.open) {
        d.open = false;
        d.querySelector('summary')?.focus();
      }
    });
  });
  document.addEventListener('click', (e) => {
    menus.forEach((d) => { if (d.open && !d.contains(e.target)) d.open = false; });
  });

  // ---- Confirmation in the hub modal (instead of the native confirm()) --------------------
  // `HubConfirm(message, { danger, ok })` → Promise<boolean>. Esc/Cancel = false.
  function hubConfirm(message, opts) {
    const o = opts || {};
    const danger = o.danger !== undefined ? o.danger : /excluir|apagar|revogar|desativar|remover|reverter|rotacionar|restaurar|delete|revoke|remove/i.test(message);
    return new Promise((resolve) => {
      const prev = document.activeElement;
      const host = document.createElement('div');
      host.className = 'overlay hub-confirm-overlay';
      host.innerHTML = `<section class="modal hub-confirm" role="alertdialog" aria-modal="true" aria-labelledby="hub-confirm-title" aria-describedby="hub-confirm-msg">
        <div class="modal-header"><h2 id="hub-confirm-title">${esc(o.title || t('js.confirm_title'))}</h2></div>
        <p id="hub-confirm-msg" class="hub-confirm-msg">${esc(message)}</p>
        <div class="modal-actions"><button type="button" class="secondary-button" data-hub-confirm="no">${esc(t('js.cancel'))}</button>
        <button type="button" class="${danger ? 'danger-button' : 'primary-button'}" data-hub-confirm="yes">${esc(o.ok || t('js.confirm_ok'))}</button></div></section>`;
      const done = (v) => {
        host.remove();
        document.removeEventListener('keydown', onKey, true);
        if (prev && prev.focus) prev.focus();
        resolve(v);
      };
      const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); } };
      host.addEventListener('click', (e) => {
        const b = e.target.closest('[data-hub-confirm]');
        if (b) done(b.dataset.hubConfirm === 'yes');
      });
      document.addEventListener('keydown', onKey, true);
      document.body.append(host);
      host.querySelector(danger ? '[data-hub-confirm="no"]' : '[data-hub-confirm="yes"]').focus();
    });
  }
  window.HubConfirm = hubConfirm;

  // ---- Hub file field (label in the user's language, instead of the native one) ------
  function enhanceFile(inp) {
    if (inp.dataset.hubFile || inp.hidden || inp.closest('[hidden]') || !inp.parentNode) return;
    inp.dataset.hubFile = '1';
    const wrap = document.createElement('span');
    wrap.className = 'hub-file';
    inp.parentNode.insertBefore(wrap, inp);
    const btn = document.createElement('span');
    btn.className = 'secondary-button hub-file-btn';
    btn.setAttribute('aria-hidden', 'true');
    btn.textContent = t('js.file_choose');
    const name = document.createElement('span');
    name.className = 'hub-file-name';
    name.textContent = t('js.file_none');
    inp.classList.add('hub-file-input');
    wrap.append(inp, btn, name);
    btn.addEventListener('click', () => inp.click());
    inp.addEventListener('change', () => {
      name.textContent = inp.files && inp.files.length ? [...inp.files].map((f) => f.name).join(', ') : t('js.file_none');
      name.title = name.textContent;
    });
  }
  const scanFiles = (root) => root.querySelectorAll && root.querySelectorAll('input[type=file]').forEach(enhanceFile);
  scanFiles(document);
  if (typeof MutationObserver !== 'undefined') {
    new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => { if (n.nodeType === 1) { if (n.matches('input[type=file]')) enhanceFile(n); else scanFiles(n); } })))
      .observe(document.body, { childList: true, subtree: true });
  }

  // Language: applied right away (no "OK" button).
  document.addEventListener('change', (e) => {
    const sel = e.target.closest && e.target.closest('select[data-hub-autosubmit]');
    if (sel && sel.form) sel.form.requestSubmit ? sel.form.requestSubmit() : sel.form.submit();
  });
  // Theme (3 icons, role=radiogroup): applied right away and saved through the preferences API;
  // arrows move/select (radiogroup pattern). Without JS the form does a normal POST.
  document.querySelectorAll('[data-hub-theme-seg]').forEach((seg) => {
    const btns = [...seg.querySelectorAll('[role=radio]')];
    const pick = (b, focus) => {
      btns.forEach((x) => { const on = x === b; x.setAttribute('aria-checked', String(on)); x.classList.toggle('active', on); x.tabIndex = on ? 0 : -1; });
      if (focus) b.focus();
      const v = b.value;
      document.documentElement.setAttribute('data-theme', v);
      const meta = document.querySelector('meta[name=color-scheme]');
      if (meta) meta.setAttribute('content', v === 'dark' ? 'dark' : v === 'auto' ? 'light dark' : 'light');
      const form = seg.closest('form');
      if (form) {
        fetch(form.action, { method: 'POST', credentials: 'same-origin', redirect: 'manual', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'theme=' + encodeURIComponent(v) }).catch(() => {});
      }
    };
    btns.forEach((b, i) => {
      b.addEventListener('click', (e) => { e.preventDefault(); pick(b, false); });
      b.addEventListener('keydown', (e) => {
        const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (d) { e.preventDefault(); pick(btns[(i + d + btns.length) % btns.length], true); }
        else if (e.key === 'Home' || e.key === 'End') { e.preventDefault(); pick(btns[e.key === 'Home' ? 0 : btns.length - 1], true); }
      });
    });
  });
  // Search shortcut according to the platform (⌘K on macOS, Ctrl K elsewhere).
  if (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '')) {
    document.querySelectorAll('.hub-kbd').forEach((k) => { k.textContent = '⌘K'; });
  }

  // ---- Row "⋯" menus (<details class="hub-row-menu">): one open at a time; click
  // outside or Esc closes; choosing an action closes the menu.
  document.addEventListener('click', (e) => {
    document.querySelectorAll('details.hub-row-menu[open]').forEach((d) => {
      if (!d.contains(e.target) || e.target.closest('.hub-row-menu-list button, .hub-row-menu-list a')) d.open = false;
    });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('details.hub-row-menu[open]').forEach((d) => { d.open = false; d.querySelector('summary')?.focus(); });
  });

  // "⋯" menu near the screen edge: opens upwards (no room below) and/or aligned to the
  // left (would leave the screen on the left). `toggle` does not bubble: listens in capture.
  document.addEventListener('toggle', (e) => {
    const d = e.target;
    if (!(d instanceof HTMLDetailsElement) || !d.classList.contains('hub-row-menu')) return;
    d.classList.remove('hub-row-menu--up', 'hub-row-menu--start');
    if (!d.open) return;
    const list = d.querySelector('.hub-row-menu-list');
    if (!list) return;
    const r = list.getBoundingClientRect();
    const s = d.getBoundingClientRect();
    if (r.bottom > window.innerHeight - 8 && s.top > window.innerHeight - s.bottom) d.classList.add('hub-row-menu--up');
    if (r.left < 8) d.classList.add('hub-row-menu--start');
  }, true);

  // ---- Modal dialogs of the screens (Administration/Operations/Insights) -----------------------
  // Generic for any visible [role=dialog][aria-modal=true]: Tab/Shift+Tab stay
  // inside the dialog and, when it goes away, focus returns to whoever had focus before.
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const openDialog = () => [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')].find((d) => d.getClientRects().length && !d.closest('[hidden]'));
  let beforeDialog = null;
  document.addEventListener('focusin', (e) => {
    if (!e.target.closest || !e.target.closest('[role="dialog"]')) beforeDialog = e.target;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const d = openDialog();
    if (!d || d.classList.contains('hub-palette')) return;
    const f = [...d.querySelectorAll(FOCUSABLE)].filter((x) => x.getClientRects().length);
    if (!f.length) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (!d.contains(document.activeElement)) { e.preventDefault(); first.focus(); return; }
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  if (typeof MutationObserver !== 'undefined') {
    new MutationObserver(() => {
      const lost = !document.activeElement || document.activeElement === document.body;
      if (lost && !openDialog() && beforeDialog && beforeDialog.isConnected) beforeDialog.focus();
    }).observe(document.body, { childList: true, subtree: true });
  }
  Object.assign(P, { Pure, body, T, t, inApp, esc, paletteIndex, announce, store, api, feature,
    recordSearch, menus });
})();
