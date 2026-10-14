/* hub-ux-colfilter.js — kept apart from hub-ux.js for size; loaded right after the hub-ux part
 * files on the same pages.
 *
 * ---- Column filters, spreadsheet style (`data-hub-colfilter`; guide in docs/DESIGN-TOKENS.md) ----
 * <table data-hub-colfilter data-hub-colfilter-empty="Text">; each <th data-hub-col="key"
 * data-hub-col-label="Label" [data-hub-col-type="list|date"]> gets a funnel that opens a
 * popover (on a phone, a sheet from the bottom up): sort, list of values with search and
 * "Select all" (or a date range), Clear/Apply. The value of each cell comes from
 * `data-hub-value` (dates in ms) or from the text. State in the URL (`f.<key>=a,b`, `f.<key>=from..to`,
 * `sort=<key>:asc|desc`) — shareable. "Active filters" line above the table; in the
 * card layout (no header), the "Filters" button opens the same options. */
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const body = document.body;
  if (!body) return;
  let T = {};
  try { T = JSON.parse(body.dataset.hubI18n || '{}'); } catch (_) { T = {}; }
  const t = (k, d) => T[k] || d;
  const L = {
    filter: t('js.cf_filter', 'Filtrar'), asc: t('js.cf_asc', 'Ordenar A → Z'), desc: t('js.cf_desc', 'Ordenar Z → A'),
    search: t('js.cf_search', 'Buscar valores'), all: t('js.cf_all', 'Selecionar todos'), clear: t('js.cf_clear', 'Limpar'),
    apply: t('js.cf_apply', 'Aplicar'), from: t('js.cf_from', 'De'), to: t('js.cf_to', 'Até'), active: t('js.cf_active', 'Filtros ativos:'),
    clearAll: t('js.cf_clear_all', 'Limpar tudo'), filters: t('js.cf_filters', 'Filtros'), remove: t('js.cf_remove', 'Remover filtro'),
    empty: t('js.cf_empty', 'Nenhum resultado com estes filtros.'), close: t('js.cf_close', 'Fechar'),
  };
  const FUNNEL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16l-6 7.5V19l-4 1.5v-8Z"/></svg>';
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const dayMs = (s, end) => { if (!s) return null; const d = new Date(s + 'T00:00:00'); return d.getTime() + (end ? 86399999 : 0); };

  function setup(table) {
    const heads = [...table.querySelectorAll('thead th')];
    const cols = heads.map((th, i) => ({ th, i, key: th.dataset.hubCol, label: th.dataset.hubColLabel || th.textContent.trim(), type: th.dataset.hubColType || 'list' })).filter((c) => c.key);
    if (!cols.length || !table.tBodies[0]) return;
    const tbody = table.tBodies[0];
    const rows = [...tbody.rows];
    const value = (r, c) => { const td = r.cells[c.i]; return td ? (td.dataset.hubValue != null ? td.dataset.hubValue : td.textContent.trim()) : ''; };
    const text = (r, c) => { const td = r.cells[c.i]; return td ? td.textContent.trim().replace(/\s+/g, ' ') : ''; };
    const state = { f: {}, sort: null };
    const url = new URL(location.href);
    for (const c of cols) {
      const v = url.searchParams.get('f.' + c.key);
      if (v) state.f[c.key] = c.type === 'date' ? v : v.split(',').filter(Boolean);
    }
    const sp = (url.searchParams.get('sort') || '').split(':');
    if (cols.some((c) => c.key === sp[0])) state.sort = { key: sp[0], dir: sp[1] === 'desc' ? 'desc' : 'asc' };

    const bar = el('div', 'hub-cf-bar');
    bar.hidden = true;
    bar.setAttribute('data-hub-cf-bar', '');
    const mobileBtn = el('button', 'secondary-button hub-cf-mobile');
    mobileBtn.type = 'button';
    mobileBtn.innerHTML = FUNNEL;
    mobileBtn.append(' ' + L.filters);
    mobileBtn.setAttribute('aria-haspopup', 'dialog');
    const top = el('div', 'hub-cf-top');
    top.append(mobileBtn, bar);
    table.parentNode.insertBefore(top, table);
    const emptyRow = el('div', 'hub-cf-empty', table.dataset.hubColfilterEmpty || L.empty);
    emptyRow.hidden = true;
    table.parentNode.insertBefore(emptyRow, table.nextSibling);

    const labelOf = (c, v) => { const r = rows.find((x) => value(x, c) === v); return r ? text(r, c) : v; };
    const pass = (r) => cols.every((c) => {
      const f = state.f[c.key];
      if (!f) return true;
      const v = value(r, c);
      if (c.type === 'date') {
        const [a, b] = String(f).split('..');
        const n = Number(v); const lo = dayMs(a, false); const hi = dayMs(b, true);
        return (lo == null || n >= lo) && (hi == null || n <= hi);
      }
      return f.includes(v);
    });
    function apply() {
      let shown = 0;
      for (const r of rows) { const ok = pass(r); r.hidden = !ok; if (ok) shown++; }
      if (state.sort) {
        const c = cols.find((x) => x.key === state.sort.key);
        const k = (r) => { const v = value(r, c); const n = Number(v); return v !== '' && !isNaN(n) ? n : text(r, c).toLowerCase(); };
        const sorted = [...rows].sort((a, b) => { const x = k(a), y = k(b); const d = x < y ? -1 : x > y ? 1 : 0; return state.sort.dir === 'desc' ? -d : d; });
        sorted.forEach((r) => tbody.appendChild(r));
      }
      emptyRow.hidden = shown > 0 || rows.length === 0;
      for (const c of cols) {
        const on = !!state.f[c.key];
        c.btn.classList.toggle('on', on);
        c.btn.setAttribute('aria-pressed', String(on));
        c.th.setAttribute('aria-sort', state.sort && state.sort.key === c.key ? (state.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
      }
      bar.textContent = '';
      const act = cols.filter((c) => state.f[c.key]);
      bar.hidden = !act.length;
      top.classList.toggle('has-active', act.length > 0);
      if (act.length) {
        bar.append(el('span', 'hub-cf-bar-label', L.active));
        for (const c of act) {
          const f = state.f[c.key];
          const desc = c.type === 'date' ? String(f).replace('..', ' – ') : f.map((v) => labelOf(c, v)).join(', ');
          const chip = el('span', 'hub-cf-chip');
          const lab = el('span', null, c.label + ': ' + desc);
          lab.title = c.label + ': ' + desc;
          chip.append(lab);
          const x = el('button', 'hub-cf-chip-x', '✕');
          x.type = 'button';
          x.setAttribute('aria-label', L.remove + ': ' + c.label);
          x.addEventListener('click', () => { delete state.f[c.key]; commit(); });
          chip.append(x);
          bar.append(chip);
        }
        const all = el('button', 'hub-cf-clear-all', L.clearAll);
        all.type = 'button';
        all.addEventListener('click', () => { state.f = {}; commit(); });
        bar.append(all);
      }
    }
    function commit() {
      const u = new URL(location.href);
      [...u.searchParams.keys()].filter((k) => k.startsWith('f.') || k === 'sort').forEach((k) => u.searchParams.delete(k));
      for (const [k, v] of Object.entries(state.f)) u.searchParams.set('f.' + k, Array.isArray(v) ? v.join(',') : v);
      if (state.sort) u.searchParams.set('sort', state.sort.key + ':' + state.sort.dir);
      history.replaceState(history.state, '', u.pathname + u.search + u.hash);
      apply();
    }

    let open = null;
    function close(focus) {
      if (!open) return;
      const { pop, back, btn } = open;
      pop.remove(); if (back) back.remove();
      open = null;
      if (focus && btn) btn.focus();
    }
    function sheetHead(pop, title) {
      const head = el('div', 'hub-cf-head');
      head.append(el('strong', null, title));
      const x = el('button', 'hub-cf-close', '✕');
      x.type = 'button'; x.setAttribute('aria-label', L.close);
      x.addEventListener('click', () => close(true));
      head.append(x);
      pop.append(head);
    }
    function panel(c, btn) {
      close(false);
      const sheet = window.matchMedia('(max-width: 767px)').matches;
      const pop = el('div', 'hub-cf-pop' + (sheet ? ' hub-cf-sheet' : ''));
      pop.setAttribute('role', 'dialog');
      pop.setAttribute('aria-label', L.filter + ': ' + c.label);
      pop.setAttribute('data-hub-cf-pop', c.key);
      sheetHead(pop, c.label);
      const sorts = el('div', 'hub-cf-sorts');
      for (const [dir, lab] of [['asc', L.asc], ['desc', L.desc]]) {
        const b = el('button', 'hub-cf-sort' + (state.sort && state.sort.key === c.key && state.sort.dir === dir ? ' active' : ''), lab);
        b.type = 'button';
        b.addEventListener('click', () => { state.sort = { key: c.key, dir }; commit(); close(true); });
        sorts.append(b);
      }
      pop.append(sorts);
      let read;
      if (c.type === 'date') {
        const [a, b] = String(state.f[c.key] || '').split('..');
        const wrap = el('div', 'hub-cf-dates');
        const mk = (lab, v) => { const l = el('label', 'hub-cf-date'); l.append(el('span', null, lab)); const i = el('input'); i.type = 'date'; i.value = v || ''; l.append(i); wrap.append(l); return i; };
        const ia = mk(L.from, a); const ib = mk(L.to, b);
        pop.append(wrap);
        read = () => (ia.value || ib.value ? (ia.value || '') + '..' + (ib.value || '') : null);
      } else {
        const vals = [...new Set(rows.map((r) => value(r, c)))].filter((v) => v !== '').sort((p, q) => labelOf(c, p).localeCompare(labelOf(c, q)));
        const cur = state.f[c.key];
        const list = el('div', 'hub-cf-list');
        if (vals.length > 8) {
          const s = el('input', 'hub-cf-search'); s.type = 'search'; s.placeholder = L.search; s.setAttribute('aria-label', L.search);
          s.addEventListener('input', () => { const q = s.value.toLowerCase(); list.querySelectorAll('label').forEach((lb) => { lb.hidden = !lb.textContent.toLowerCase().includes(q); }); });
          pop.append(s);
        }
        const allL = el('label', 'hub-cf-opt hub-cf-all');
        const allC = el('input'); allC.type = 'checkbox';
        allL.append(allC, el('span', null, L.all));
        pop.append(allL);
        const boxes = vals.map((v) => {
          const lb = el('label', 'hub-cf-opt'); const cb = el('input'); cb.type = 'checkbox'; cb.value = v;
          cb.checked = !cur || cur.includes(v);
          lb.append(cb, el('span', null, labelOf(c, v)));
          list.append(lb);
          return cb;
        });
        const sync = () => { const n = boxes.filter((b) => b.checked).length; allC.checked = n === boxes.length; allC.indeterminate = n > 0 && n < boxes.length; };
        boxes.forEach((b) => b.addEventListener('change', sync));
        allC.addEventListener('change', () => { boxes.forEach((b) => { if (!b.closest('label').hidden) b.checked = allC.checked; }); sync(); });
        sync();
        pop.append(list);
        read = () => { const sel = boxes.filter((b) => b.checked).map((b) => b.value); return sel.length === boxes.length ? null : sel; };
      }
      const acts = el('div', 'hub-cf-actions');
      const clr = el('button', 'secondary-button', L.clear); clr.type = 'button';
      clr.addEventListener('click', () => { delete state.f[c.key]; commit(); close(true); });
      const ok = el('button', 'primary-button', L.apply); ok.type = 'button';
      ok.addEventListener('click', () => { const v = read(); if (v == null) delete state.f[c.key]; else state.f[c.key] = v; commit(); close(true); });
      acts.append(clr, ok);
      pop.append(acts);
      let back = null;
      if (sheet) {
        back = el('div', 'hub-cf-backdrop');
        back.addEventListener('click', () => close(true));
        document.body.append(back, pop);
      } else {
        document.body.append(pop);
        const r = btn.getBoundingClientRect();
        const w = pop.offsetWidth;
        pop.style.top = Math.round(r.bottom + 4 + window.scrollY) + 'px';
        pop.style.left = Math.round(Math.max(8 + window.scrollX, Math.min(r.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - w - 8))) + 'px';
      }
      open = { pop, back, btn };
      const first = pop.querySelector('input,button.hub-cf-sort');
      if (first) first.focus();
    }
    mobileBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      close(false);
      const back = el('div', 'hub-cf-backdrop');
      const pop = el('div', 'hub-cf-pop hub-cf-sheet');
      pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', L.filters);
      sheetHead(pop, L.filters);
      for (const c of cols) {
        const b = el('button', 'hub-cf-colpick' + (state.f[c.key] ? ' on' : ''), c.label); b.type = 'button';
        b.addEventListener('click', (ev) => { ev.stopPropagation(); panel(c, mobileBtn); });
        pop.append(b);
      }
      back.addEventListener('click', () => close(true));
      document.body.append(back, pop);
      open = { pop, back, btn: mobileBtn };
      const f = pop.querySelector('.hub-cf-colpick');
      if (f) f.focus();
    });
    for (const c of cols) {
      const b = el('button', 'hub-cf-btn');
      b.type = 'button';
      b.innerHTML = FUNNEL;
      b.setAttribute('aria-label', L.filter + ': ' + c.label);
      b.title = L.filter + ': ' + c.label;
      b.setAttribute('aria-haspopup', 'dialog');
      b.setAttribute('data-hub-cf-col', c.key);
      b.addEventListener('click', (e) => { e.stopPropagation(); if (open && open.btn === b) close(true); else panel(c, b); });
      const wrap = el('span', 'hub-cf-th');
      while (c.th.firstChild) wrap.append(c.th.firstChild);
      wrap.append(b);
      c.th.append(wrap);
      c.btn = b;
    }
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && open) close(true); });
    document.addEventListener('click', (e) => { if (open && !open.back && !open.pop.contains(e.target)) close(false); });
    apply();
  }
  document.querySelectorAll('table[data-hub-colfilter]').forEach(setup);
})();
