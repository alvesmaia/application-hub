/* ux.js — wave 9 pages in hub-web (Home, Applications, Inbox, Analytics).
 *
 *  - Home: "Customize" turns on edit mode — drag widgets (HTML5 drag and
 *    drop) or, with the keyboard, the ↑/↓/✕ buttons of each widget; "Add widget",
 *    "Restore default" and (for whoever edits settings) "Save as default for the role".
 *    The layout goes to the server (`PUT /api/me/home`). Changes announced in aria-live.
 *  - Applications: star (favorites) and "Request access" (becomes a request in the administrators' Inbox).
 *  - Inbox: approve/decline (the hub calls the app over the internal network), mark as read,
 *    archive, channel preferences and analytics opt-out.
 *  - Progress bars/charts: width from `data-pct`/`data-h`.
 */
(function () {
  'use strict';

  // Widget order after moving `id` by `delta` positions (pure logic, tested).
  function moveIn(order, id, delta) {
    const i = order.indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= order.length) return order.slice();
    const out = order.slice();
    out.splice(i, 1);
    out.splice(j, 0, id);
    return out;
  }
  if (typeof module !== 'undefined' && module.exports && typeof document === 'undefined') {
    module.exports = { moveIn };
    return;
  }

  // hub-ux.js (shell) publishes window.HubUx; read at the moment of use.
  const hx = () => window.HubUx;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const t = (k, v) => (hx() ? hx().t(k, v) : k);
  const toast = (msg, kind) => {
    const el = document.getElementById('toast');
    if (el) { el.classList.toggle('toast-error', kind === 'error'); el.classList.toggle('toast-success', kind === 'success'); }
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toast.h);
    toast.h = setTimeout(() => el.classList.remove('show'), 3500);
  };
  const api = (m, u, d) => (hx() ? hx().api(m, u, d) : Promise.reject(new Error('sem hub-ux')));

  $$('[data-pct]').forEach((el) => { el.style.width = `${Math.max(0, Math.min(100, Number(el.dataset.pct) || 0))}%`; });
  $$('[data-h]').forEach((el) => { el.style.height = `${Math.max(2, Number(el.dataset.h) || 0)}%`; });

  // ---- Home ------------------------------------------------------------------------
  const grid = $('[data-home-widgets]');
  const editor = $('[data-home-editor]');
  const toggle = $('[data-home-customize]');
  if (grid && editor && toggle) {
    let editing = false;
    let snapshot = null;
    const all = () => $$('.hub-widget', grid);
    const order = () => all().filter((w) => !w.hidden).map((w) => w.dataset.widget);
    const save = () => api('PUT', '/api/me/home', { widgets: order() });
    // "Add widget": the options come from the server (all widgets); only the hidden ones
    // are enabled. With none hidden, the selector gives way to a warning.
    const refreshAdd = () => {
      const sel = $('[data-home-add]', editor);
      const hiddenIds = all().filter((w) => w.hidden).map((w) => w.dataset.widget);
      let first = null;
      $$('option', sel).forEach((o) => {
        const avail = hiddenIds.includes(o.value);
        o.disabled = !avail;
        o.hidden = !avail;
        if (avail && first === null) first = o.value;
      });
      if (first !== null) sel.value = first;
      $('[data-home-add-wrap]', editor).hidden = first === null;
      $('[data-home-add-empty]', editor).hidden = true;
    };
    const setEditing = (on) => {
      editing = on;
      editor.hidden = !on;
      toggle.setAttribute('aria-pressed', String(on));
      toggle.hidden = on;
      grid.classList.toggle('editing', on);
      $$('[data-widget-tools]', grid).forEach((x) => { x.hidden = !on; });
      all().forEach((w) => { w.draggable = on; });
      if (on) {
        snapshot = all().map((w) => [w.dataset.widget, w.hidden]);
        refreshAdd();
        hx() && hx().feature('home_customize');
      }
    };
    // Cancel: returns order and visibility to the state from when editing started.
    const restore = () => {
      if (!snapshot) return;
      snapshot.forEach(([id, hidden]) => {
        const w = $(`.hub-widget[data-widget="${id}"]`, grid);
        if (w) { w.hidden = hidden; grid.append(w); }
      });
    };
    toggle.addEventListener('click', () => setEditing(!editing));
    $('[data-home-cancel]', editor).addEventListener('click', () => { restore(); setEditing(false); toggle.focus(); });
    // Esc leaves edit mode (= Cancel), except inside an open menu.
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || editor.hidden || e.target.closest('details[open]')) return;
      $('[data-home-cancel]', editor).click();
    });
    $('[data-home-done]', editor).addEventListener('click', async (e) => {
      const b = e.currentTarget;
      b.disabled = true;
      try {
        await save();
        setEditing(false);
        toggle.focus();
        toast(t('js.saved'));
      } catch (err) { toast(err.message, 'error'); }
      b.disabled = false;
    });
    grid.addEventListener('click', (e) => {
      const w = e.target.closest('.hub-widget');
      if (!w || !editing) return;
      const id = w.dataset.widget;
      if (e.target.closest('[data-widget-up], [data-widget-down]')) {
        const delta = e.target.closest('[data-widget-up]') ? -1 : 1;
        const next = moveIn(order(), id, delta);
        let sib = delta < 0 ? w.previousElementSibling : w.nextElementSibling;
        while (sib && sib.hidden) sib = delta < 0 ? sib.previousElementSibling : sib.nextElementSibling;
        if (!sib || next.join() === order().join()) return;
        if (delta < 0) sib.before(w); else sib.after(w);
        e.target.closest('button').focus();
        hx() && hx().announce(t('js.moved', { n: order().indexOf(id) + 1 }));
      } else if (e.target.closest('[data-widget-remove]')) {
        w.hidden = true;
        hx() && hx().announce(t('js.removed'));
        refreshAdd();
        toggle.focus();
      }
    });
    $('[data-home-add-btn]', editor).addEventListener('click', () => {
      const id = $('[data-home-add]', editor).value;
      const w = $(`.hub-widget[data-widget="${id}"]`, grid);
      if (!w || !w.hidden) return;
      w.hidden = false;
      grid.append(w);
      hx() && hx().announce(t('js.added'));
      refreshAdd();
    });
    $('[data-home-reset]', editor).addEventListener('click', async () => {
      try { await api('DELETE', '/api/me/home'); location.reload(); } catch (e) { toast(e.message, 'error'); }
    });
    // Drag and drop (mouse/touch); the keyboard uses the buttons.
    let dragged = null;
    grid.addEventListener('dragstart', (e) => {
      if (!editing) return;
      dragged = e.target.closest('.hub-widget');
      if (dragged) { dragged.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragged.dataset.widget); }
    });
    grid.addEventListener('dragover', (e) => {
      if (!dragged) return;
      e.preventDefault();
      const over = e.target.closest('.hub-widget');
      if (!over || over === dragged) return;
      const r = over.getBoundingClientRect();
      if (e.clientY < r.top + r.height / 2) over.before(dragged); else over.after(dragged);
    });
    grid.addEventListener('dragend', () => {
      if (!dragged) return;
      dragged.classList.remove('dragging');
      dragged = null;
    });
  }

  // ---- Applications ----------------------------------------------------------------------
  document.addEventListener('click', async (e) => {
    const star = e.target.closest('[data-fav]');
    if (star) {
      const on = star.getAttribute('aria-pressed') !== 'true';
      try {
        await api('PUT', `/api/me/favorites/${encodeURIComponent(star.dataset.fav)}`, { on });
        star.setAttribute('aria-pressed', String(on));
        star.classList.toggle('on', on);
        const name = star.closest('.app-card')?.querySelector('.app-card-title')?.textContent || '';
        star.setAttribute('aria-label', t(on ? 'apps.unpin' : 'apps.pin', { n: name }).replace('{n}', name));
        toast(t('js.saved'));
      } catch (err) { toast(err.message, 'error'); }
      return;
    }
    const req = e.target.closest('[data-request-access]');
    if (req) {
      req.disabled = true;
      try {
        await api('POST', '/api/me/access-requests', { app: req.dataset.requestAccess, reason: '' });
        const pill = document.createElement('span');
        pill.className = 'status-pill status-review';
        pill.textContent = t('apps.requested');
        req.replaceWith(pill);
        toast(t('js.requested'));
      } catch (err) { req.disabled = false; toast(err.message, 'error'); }
    }
  });

  // ---- Inbox ---------------------------------------------------------------------------
  const inbox = $('.hub-inbox');
  if (inbox) {
    inbox.addEventListener('click', async (e) => {
      const li = e.target.closest('[data-item]');
      if (!li) return;
      const id = li.dataset.item;
      const b = e.target.closest('button');
      if (!b) return;
      b.disabled = true;
      try {
        if (b.dataset.decide) {
          const comment = $('[data-decide-comment]', li)?.value || '';
          await api('POST', `/api/me/inbox/${encodeURIComponent(id)}/decide`, { decision: b.dataset.decide, comment });
          location.reload();
          return;
        }
        if (b.hasAttribute('data-revoke')) {
          await api('POST', '/api/admin/grants/revoke', { item: id });
          location.reload();
          return;
        }
        if (b.hasAttribute('data-read')) {
          await api('POST', '/api/me/inbox/read', { ids: [id] });
          li.classList.remove('unread');
          b.remove();
          return;
        }
        if (b.dataset.archive) {
          await api('POST', '/api/me/inbox/archive', { ids: [id], archived: b.dataset.archive === '1' });
          li.remove();
          hx() && hx().announce(t('js.saved'));
          return;
        }
      } catch (err) {
        toast(err.message, 'error');
      }
      b.disabled = false;
    });
    // New items arriving through the live connection: reloads the list (outside a field).
    document.addEventListener('hub:inbox', (ev) => {
      if (ev.detail.unread > ev.detail.prev && !inbox.contains(document.activeElement)) location.reload();
    });
  }
  const chForm = $('[data-channel-form]');
  if (chForm) {
    chForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const channels = {};
      $$('input[name$=".in_app"]', chForm).forEach((i) => { channels[i.name.split('.')[0]] = { in_app: i.checked }; });
      const opt = $('input[name="analytics_opt_out"]', chForm);
      try {
        await api('PUT', '/api/me/prefs', { channels, analytics_opt_out: opt ? opt.checked : undefined });
        toast(t('js.saved'));
      } catch (err) { toast(err.message, 'error'); }
    });
  }

  // ---- Account: Authorized apps / Personal tokens ---------------------------------------
  // Native dialogs (<dialog>); without JS, the revocation form posts directly.
  const dlgClose = (d) => { if (d && d.open) d.close(); };
  $$('dialog.acct-dialog').forEach((d) => {
    $$('[data-acct-cancel]', d).forEach((b) => b.addEventListener('click', () => dlgClose(d)));
  });
  const confirmIn = (id) => new Promise((resolve) => {
    const d = document.getElementById(id);
    if (!d || !d.showModal) { resolve(true); return; }
    const ok = $('[data-acct-ok]', d);
    const done = (v) => { ok.onclick = null; d.onclose = null; dlgClose(d); resolve(v); };
    ok.onclick = () => done(true);
    d.onclose = () => resolve(false);
    d.showModal();
  });
  $$('form[data-acct-confirm]').forEach((f) => {
    f.addEventListener('submit', async (e) => {
      if (f.dataset.confirmed === '1') return;
      e.preventDefault();
      if (await confirmIn(f.dataset.acctConfirm)) { f.dataset.confirmed = '1'; f.submit(); }
    });
  });
  // Reloads only the list (same page, HTML from the server) and highlights the new row.
  async function refreshList(sel, highlight) {
    const res = await fetch(location.pathname + location.search, { credentials: 'same-origin', headers: { accept: 'text/html' } });
    if (!res.ok) { location.reload(); return; }
    const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
    const fresh = doc.querySelector(sel);
    const cur = document.querySelector(sel);
    if (!fresh || !cur) { location.reload(); return; }
    cur.replaceWith(document.importNode(fresh, true));
    if (highlight) {
      const row = document.querySelector(`${sel} [data-token-row="${CSS.escape(String(highlight))}"]`);
      if (row) row.classList.add('row-new');
    }
  }
  const newBtn = $('[data-acct-new-token]');
  const newDlg = document.getElementById('new-token');
  if (newBtn && newDlg) {
    const form = $('[data-token-form]', newDlg);
    const shown = $('[data-token-shown]', newDlg);
    const errEl = $('[data-acct-error]', newDlg);
    let created = false;
    newBtn.addEventListener('click', () => {
      form.reset(); form.hidden = false; shown.hidden = true; errEl.hidden = true; created = false;
      newDlg.showModal();
      $('#tok-name', newDlg).focus();
    });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const name = form.name.value.trim();
      const actions = $$('input[name="actions"]:checked', form).map((i) => i.value);
      if (!name || !actions.length) { errEl.textContent = errEl.dataset.required; errEl.hidden = false; return; }
      const btn = $('button[type="submit"]', form);
      btn.disabled = true;
      try {
        const r = await api('POST', '/api/me/tokens', { name, actions, expires_days: Number(form.expires_days.value) });
        $('[data-token-secret]', shown).textContent = r.token;
        // The list behind the dialog already shows the new token (highlighted).
        await refreshList('[data-acct-tokens]', r.item && r.item.id);
        form.hidden = true; shown.hidden = false; created = true;
        $('[data-token-copy]', shown).focus();
      } catch (err) { errEl.textContent = err.message; errEl.hidden = false; }
      btn.disabled = false;
    });
    $('[data-token-copy]', shown).addEventListener('click', async () => {
      try { await navigator.clipboard.writeText($('[data-token-secret]', shown).textContent); toast(t('js.kit_copied')); } catch (_) { /* manual selection */ }
    });
    $('[data-token-done]', shown).addEventListener('click', () => dlgClose(newDlg));
    newDlg.addEventListener('close', () => {
      // The secret disappears from the DOM on close: shown only once.
      $('[data-token-secret]', shown).textContent = '';
      if (created) { created = false; toast(t('js.token_created')); }
    });
  }
  // Revoke (delegated: also valid for the rows reloaded without a reload).
  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-acct-tokens] [data-token-revoke]');
    if (!b) return;
    if (!(await confirmIn('revoke-token'))) return;
    b.disabled = true;
    try {
      await api('POST', `/api/me/tokens/${encodeURIComponent(b.dataset.tokenRevoke)}/revoke`);
      await refreshList('[data-acct-tokens]');
      toast(t('js.saved'));
    } catch (err) { toast(err.message, 'error'); b.disabled = false; }
  });

  // Wave 13 — catalog: the app's stack opens on mouse hover and focus (CSS) and, on touch,
  // through the "i" button (aria-expanded). Esc or tapping outside closes.
  const closeStacks = (except) => document.querySelectorAll('.app-card.hub-stack-open').forEach((c) => {
    if (c === except) return;
    c.classList.remove('hub-stack-open');
    c.querySelector('[data-stack-toggle]')?.setAttribute('aria-expanded', 'false');
  });
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-stack-toggle]');
    if (!b) { if (!e.target.closest('.hub-stack')) closeStacks(null); return; }
    const card = b.closest('.app-card');
    if (!card) return;
    const open = !card.classList.contains('hub-stack-open');
    closeStacks(card);
    card.classList.toggle('hub-stack-open', open);
    b.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const open = document.querySelector('.app-card.hub-stack-open');
    if (!open) return;
    closeStacks(null);
    open.querySelector('[data-stack-toggle]')?.focus();
  });
})();
