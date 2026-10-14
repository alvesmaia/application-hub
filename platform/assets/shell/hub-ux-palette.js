/* hub-ux-palette.js — global search palette (⌘K / Ctrl+K) and guided tour. Second part of
 * hub-ux.js (split only for size; its header documents the behavior), loaded right after it
 * on the same pages. */
(function () {
  'use strict';
  const P = window.__hubUxParts;
  if (!P) return;
  const { Pure, body, t, inApp, esc, paletteIndex, announce, store, api, feature, recordSearch,
    menus } = P;
  // ---- Paleta de busca (⌘K) ---------------------------------------------------------
  let palette = null;
  let opener = null;
  let options = [];
  let active = -1;
  let timer = null;
  let seq = 0;
  let lastQ = '';

  function recentList() {
    try { return JSON.parse(store()?.getItem('hub:recent-searches') || '[]'); } catch (_) { return []; }
  }
  function saveRecent(q) {
    try { store()?.setItem('hub:recent-searches', JSON.stringify(Pure.remember(recentList(), q))); } catch (_) { /* private */ }
  }

  function build() {
    const el = document.createElement('div');
    el.className = 'hub-palette-backdrop';
    el.hidden = true;
    el.innerHTML = `<div class="hub-palette" role="dialog" aria-modal="true" aria-label="${esc(t('js.palette_label'))}">
      <div class="hub-palette-input">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></svg>
        <input type="text" role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls="hub-palette-list" aria-label="${esc(t('js.palette_label'))}" placeholder="${esc(t('js.palette_placeholder'))}" autocomplete="off" spellcheck="false" maxlength="100">
        <button type="button" class="icon-button hub-palette-close" aria-label="${esc(t('common.close'))}">✕</button>
      </div>
      <div id="hub-palette-list" role="listbox" aria-label="${esc(t('js.palette_label'))}" class="hub-palette-list"></div>
      <div class="hub-palette-foot"><span>${esc(t('js.palette_hint'))}</span><span class="hub-palette-status" role="status" aria-live="polite"></span></div>
    </div>`;
    document.body.append(el);
    const input = el.querySelector('input');
    input.addEventListener('input', () => { schedule(input.value); });
    input.addEventListener('keydown', onKey);
    el.querySelector('.hub-palette-close').addEventListener('click', close);
    el.addEventListener('mousedown', (e) => { if (e.target === el) close(); });
    el.querySelector('.hub-palette-list').addEventListener('click', (e) => {
      const o = e.target.closest('[role="option"]');
      if (o) choose(Number(o.dataset.idx));
    });
    // Focus trapped in the dialog (Tab cycles between the field and the close button).
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'Tab') return;
      const f = [input, el.querySelector('.hub-palette-close')];
      const i = f.indexOf(document.activeElement);
      e.preventDefault();
      f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
    });
    return el;
  }

  function open() {
    if (!palette) palette = build();
    if (!palette.hidden) return;
    opener = document.activeElement;
    palette.hidden = false;
    body.classList.add('hub-modal-open');
    const input = palette.querySelector('input');
    input.value = '';
    render([], '');
    input.focus();
    feature('palette_open');
  }

  function close() {
    if (!palette || palette.hidden) return;
    palette.hidden = true;
    body.classList.remove('hub-modal-open');
    clearTimeout(timer);
    if (opener && opener.focus) opener.focus();
  }

  function schedule(q) {
    clearTimeout(timer);
    const v = q.trim();
    render(localGroups(v), v, !inApp && v.length > 0);
    if (!v) { if (!inApp) remoteRecent(); return; }
    if (inApp) return;
    timer = setTimeout(() => remote(v), 180);
  }

  function localGroups(q) {
    if (!q) return [];
    const hits = Pure.local(paletteIndex, q);
    return hits.length ? [{ id: 'platform', label: hits[0] ? t('js.palette_label') : '', hits }] : [];
  }

  async function remoteRecent() {
    const my = ++seq;
    try {
      const r = await api('GET', '/api/me/search?q=');
      if (my !== seq || palette.querySelector('input').value.trim()) return;
      render([], '', false, r.recent || []);
    } catch (_) { /* no network: only local recents */ }
  }

  async function remote(q) {
    const my = ++seq;
    lastQ = q;
    try {
      const r = await api('GET', `/api/me/search?q=${encodeURIComponent(q)}`);
      if (my !== seq) return;
      render(r.groups || [], q, false, null, r.partial);
    } catch (_) {
      if (my === seq) render(localGroups(q), q, false);
    }
  }

  // Highlights the searched term (no case/accent differences).
  function mark(text, q) {
    const t = String(text || '');
    const n = Pure.fold(q).trim();
    if (!n) return esc(t);
    const folded = [...t].map((c) => Pure.fold(c));
    const i = folded.join('').indexOf(n);
    if (i < 0) return esc(t);
    let pos = 0, start = -1, end = -1;
    folded.forEach((f, k) => { if (start < 0 && pos >= i) start = k; pos += f.length; if (end < 0 && pos >= i + n.length) end = k + 1; });
    const chars = [...t];
    return `${esc(chars.slice(0, start).join(''))}<mark>${esc(chars.slice(start, end).join(''))}</mark>${esc(chars.slice(end).join(''))}`;
  }

  function render(groups, q, loading, recent, partial) {
    const list = palette.querySelector('.hub-palette-list');
    const status = palette.querySelector('.hub-palette-status');
    options = [];
    let html = '';
    if (!q) {
      const rec = recent || recentList();
      if (rec.length) {
        html += `<div role="group" aria-labelledby="hub-pg-recent"><div class="hub-palette-group" id="hub-pg-recent" role="presentation">${esc(t('js.palette_recent'))}</div>`;
        rec.forEach((r) => {
          const idx = options.length;
          options.push({ recent: r });
          html += `<div role="option" id="hub-po-${idx}" data-idx="${idx}" aria-selected="false" class="hub-palette-option"><span>${esc(r)}</span></div>`;
        });
        html += '</div>';
      }
    }
    // ⌘K: up to 5 app records (combined), 3 pages and 3 internal items; the rest
    // stays under "See all results".
    const caps = { app: 5, platform: groups.some((g) => g.section === 'app') ? 3 : 8, admin: 3 };
    const used = { app: 0, platform: 0, admin: 0 };
    groups = groups.map((g) => {
      const sec = g.section || (g.id === 'platform' ? 'platform' : g.id === 'admin' ? 'admin' : 'app');
      const room = Math.max(0, (caps[sec] ?? 5) - (used[sec] || 0));
      const hits = (g.hits || []).slice(0, room);
      used[sec] = (used[sec] || 0) + hits.length;
      return { ...g, hits };
    });
    groups.forEach((g, gi) => {
      if (!g.hits || !g.hits.length) return;
      html += `<div role="group" aria-labelledby="hub-pg-${gi}"><div class="hub-palette-group" id="hub-pg-${gi}" role="presentation">${esc(g.label)}</div>`;
      g.hits.forEach((h) => {
        const idx = options.length;
        options.push({ url: h.url, q });
        html += `<div role="option" id="hub-po-${idx}" data-idx="${idx}" aria-selected="false" class="hub-palette-option"><span>${mark(h.title, q)}</span><small>${esc([h.kind, h.subtitle].filter(Boolean).join(' · '))}</small></div>`;
      });
      html += '</div>';
    });
    if (q) {
      const idx = options.length;
      options.push({ url: `/search?q=${encodeURIComponent(q)}`, q });
      html += `<div role="group" aria-label="${esc(t('shell.search') || 'Busca')}"><div role="option" id="hub-po-${idx}" data-idx="${idx}" aria-selected="false" class="hub-palette-option hub-palette-all"><span>${esc(t('js.palette_all', { q }))}</span></div></div>`;
    }
    list.innerHTML = html;
    const n = options.filter((o) => o.url && !o.url.startsWith('/search?')).length;
    status.textContent = loading ? t('js.palette_loading') : q ? `${n ? t(n === 1 ? 'js.palette_count_one' : 'js.palette_count', { n }) : t('js.palette_empty')}${partial ? ` ${t('js.palette_partial')}` : ''}` : '';
    setActive(options.length && q ? 0 : -1);
  }

  function setActive(i) {
    const input = palette.querySelector('input');
    const els = palette.querySelectorAll('[role="option"]');
    els.forEach((el) => el.setAttribute('aria-selected', 'false'));
    active = i;
    if (i >= 0 && els[i]) {
      els[i].setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', els[i].id);
      els[i].scrollIntoView({ block: 'nearest' });
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  function choose(i) {
    const o = options[i];
    if (!o) return;
    if (o.recent) {
      const input = palette.querySelector('input');
      input.value = o.recent;
      schedule(o.recent);
      return;
    }
    const q = o.q || lastQ;
    if (q) {
      saveRecent(q);
      if (!inApp) {
        // "Final" search (analytics + recents + "Use the search" step).
        recordSearch(q);
        feature('palette_select');
      }
    }
    close();
    location.assign(o.url);
  }

  function onKey(e) {
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key) && options.length) {
      if ((e.key === 'Home' || e.key === 'End') && !e.ctrlKey && palette.querySelector('input').value) return;
      e.preventDefault();
      setActive(Pure.move(active, options.length, e.key));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (active >= 0) choose(active);
      else if (e.target.value.trim()) location.assign(`/search?q=${encodeURIComponent(e.target.value.trim())}`);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  }

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (palette && !palette.hidden) close();
      else open();
      return;
    }
    // Esc closes the palette even when focus is outside the field (e.g. tapping the list on a phone).
    if (e.key === 'Escape' && palette && !palette.hidden && !palette.querySelector('input')?.contains(e.target)) { e.preventDefault(); close(); }
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-hub-palette-open]')) { e.preventDefault(); open(); }
  });

  // ---- Guided tour -----------------------------------------------------------------
  const TOUR = {
    home: ['js.tour_home_t', 'js.tour_home_b'],
    apps: ['js.tour_apps_t', 'js.tour_apps_b'],
    search: ['js.tour_search_t', 'js.tour_search_b'],
    inbox: ['js.tour_inbox_t', 'js.tour_inbox_b'],
    user: ['js.tour_user_t', 'js.tour_user_b'],
    admin: ['js.tour_admin_t', 'js.tour_admin_b'],
  };
  let tour = null;

  function saveTour(state) {
    if (inApp) return Promise.resolve();
    body.dataset.hubTour = state;
    return api('PUT', '/api/me/onboarding', { tour: state }).catch(() => {});
  }

  function visibleTarget(k) {
    const el = document.querySelector(`[data-tour="${k}"]`);
    return el && el.getClientRects().length ? el : null;
  }

  function startTour(at) {
    if (inApp) { location.assign('/?tour=1'); return; }
    const steps = Pure.tourSteps(Object.keys(TOUR).filter((k) => visibleTarget(k)));
    if (!steps.length) return;
    endTour(null);
    opener = document.activeElement;
    const box = document.createElement('div');
    box.className = 'hub-tour';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'false');
    box.setAttribute('aria-labelledby', 'hub-tour-title');
    box.setAttribute('aria-describedby', 'hub-tour-body');
    document.body.append(box);
    tour = { steps, i: Math.min(at || 0, steps.length - 1), box };
    box.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight') { e.preventDefault(); stepTo(tour.i + 1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); stepTo(tour.i - 1); }
      else if (e.key === 'Tab') {
        const f = [...box.querySelectorAll('button')];
        const i = f.indexOf(document.activeElement);
        e.preventDefault();
        f[(i + (e.shiftKey ? f.length - 1 : 1) + f.length) % f.length].focus();
      }
    });
    box.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.tour === 'next') stepTo(tour.i + 1);
      else if (b.dataset.tour === 'back') stepTo(tour.i - 1);
      else if (b.dataset.tour === 'skip') endTour('skipped');
      else if (b.dataset.tour === 'close') endTour(`step:${tour.i}`);
    });
    feature('tour_start');
    stepTo(tour.i);
  }

  function stepTo(i) {
    if (!tour) return;
    if (i >= tour.steps.length) { endTour('done'); announce(t('js.tour_done')); return; }
    if (i < 0) return;
    tour.i = i;
    const k = tour.steps[i];
    const target = visibleTarget(k);
    document.querySelectorAll('.hub-tour-target').forEach((x) => x.classList.remove('hub-tour-target'));
    if (target) target.classList.add('hub-tour-target');
    const [tt, tb] = TOUR[k];
    const last = i === tour.steps.length - 1;
    tour.box.innerHTML = `<button type="button" class="hub-cf-close hub-tour-close" data-tour="close" aria-label="${esc(t('js.cf_close', 'Fechar'))}">×</button><p class="hub-tour-step">${esc(t('js.tour_step', { n: i + 1, t: tour.steps.length }))}</p>
      <h2 id="hub-tour-title">${esc(t(tt))}</h2><p id="hub-tour-body">${esc(t(tb))}</p>
      <div class="hub-tour-actions"><button type="button" class="secondary-button" data-tour="skip">${esc(t('js.tour_skip'))}</button>
      <span></span>${i > 0 ? `<button type="button" class="secondary-button" data-tour="back">${esc(t('js.tour_back'))}</button>` : ''}
      <button type="button" class="primary-button" data-tour="next">${esc(last ? t('js.tour_done') : t('js.tour_next'))}</button></div>`;
    if (target) {
      const r = target.getBoundingClientRect();
      const top = Math.min(window.innerHeight - 220, r.bottom + 12);
      const left = Math.max(12, Math.min(window.innerWidth - 340, r.left));
      tour.box.style.top = `${Math.max(12, top)}px`;
      tour.box.style.left = `${left}px`;
    }
    tour.box.querySelector('[data-tour="next"]').focus();
    saveTour(`step:${i}`);
  }

  function endTour(state) {
    if (!tour) return;
    tour.box.remove();
    document.querySelectorAll('.hub-tour-target').forEach((x) => x.classList.remove('hub-tour-target'));
    tour = null;
    if (state) saveTour(state);
    if (opener && opener.focus) opener.focus();
  }

  // Esc closes the tour with focus anywhere (the dialog is not modal).
  document.addEventListener('keydown', (e) => {
    if (tour && e.key === 'Escape') endTour(`step:${tour.i}`);
  });
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-hub-tour-start]');
    if (b) { e.preventDefault(); menus.forEach((d) => { d.open = false; }); startTour(0); }
    const s = e.target.closest('[data-step-action]');
    if (s) {
      e.preventDefault();
      if (s.dataset.stepAction === '#search') open();
      if (s.dataset.stepAction === '#theme') {
        const m = document.querySelector('.hub-user-menu');
        if (m) { m.open = true; (m.querySelector('.hub-theme-btn[aria-checked=true]') || m.querySelector('.hub-seg-btn'))?.focus(); }
      }
    }
  });
  // First visit to the Home page (or resumed on request with ?tour=1).
  if (!inApp && location.pathname === '/') {
    const st = body.dataset.hubTour || '';
    const asked = /[?&]tour=1\b/.test(location.search);
    if (asked || st === '' || /^step:/.test(st)) {
      const steps = Pure.tourSteps(Object.keys(TOUR).filter((k) => visibleTarget(k)));
      setTimeout(() => startTour(asked ? 0 : Pure.resumeAt(st, steps.length)), 400);
    }
  }
})();
