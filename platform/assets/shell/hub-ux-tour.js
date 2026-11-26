/* hub-ux-tour.js — guided tour. Second part of hub-ux.js (split only for size; its header documents
 * the behavior), loaded right after it on the same pages. */
(function () {
  'use strict';
  const P = window.__hubUxParts;
  if (!P) return;
  const { Pure, body, t, inApp, esc, announce, api, feature, menus } = P;
  let opener = null;

  // ---- Guided tour -----------------------------------------------------------------
  const TOUR = {
    home: ['js.tour_home_t', 'js.tour_home_b'],
    apps: ['js.tour_apps_t', 'js.tour_apps_b'],
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
