/* ops-drawer.js — Containers page details drawer (kept apart from ops.js for size; loaded right
 * after it on the same pages).
 *
 * Containers: details panel (right on desktop; full screen on phones) with tabs
 * Overview · Linked variables (JSON, secrets ••••) · Logs (last lines + SSE) ·
 * Inspect (JSON sanitized by the server). Clicking outside does not close (rule of the windows);
 * Esc or ✕ close. Everything enters through textContent. */
(function () {
  'use strict';
  const drawer = document.querySelector('[data-ops-drawer]');
  if (!drawer) return;
  const backdrop = document.querySelector('[data-ops-drawer-backdrop]');
  const tabs = [...drawer.querySelectorAll('[data-ops-tab]')];
  let cur = null;
  let es = null;
  let opener = null;
  const stopLogs = () => { if (es) { es.close(); es = null; } };
  const pane = (name) => cur && cur.querySelector(`[data-ops-pane="${name}"]`);

  async function loadLogs() {
    const pre = cur.querySelector('[data-ops-logs]');
    const st = cur.querySelector('[data-ops-logs-status]');
    if (!pre || pre.dataset.loaded) return;
    pre.dataset.loaded = '1';
    const q = `env=${encodeURIComponent(pre.dataset.env)}`;
    const base = `/api/ops/runtime/${encodeURIComponent(pre.dataset.app)}/logs`;
    const add = (o) => {
      const line = document.createElement('span');
      line.className = 'ops-log-line ops-log-' + String(o.level || 'INFO').toLowerCase();
      line.textContent = o.text + '\n';
      pre.append(line);
      while (pre.childNodes.length > 1000) pre.removeChild(pre.firstChild);
      pre.scrollTop = pre.scrollHeight;
    };
    try {
      const r = await fetch(`${base}?${q}&tail=200`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || j.message || `HTTP ${r.status}`);
      (j.items || []).forEach(add);
      if (!(j.items || []).length) st.textContent = 'Este contêiner ainda não escreveu nada na saída.';
      stopLogs();
      es = new EventSource(`${base}/stream?${q}`);
      es.addEventListener('ready', () => { st.textContent = 'Acompanhando em tempo real.'; });
      es.addEventListener('line', (ev) => { try { add(JSON.parse(ev.data)); } catch (_) { /* invalid line */ } });
      es.onerror = () => { st.textContent = 'Conexão encerrada (o contêiner parou ou a rede caiu).'; stopLogs(); };
    } catch (e) {
      st.textContent = 'Não foi possível ler os logs: ' + e.message;
      delete pre.dataset.loaded;
    }
  }
  async function loadInspect() {
    const pre = cur.querySelector('[data-ops-inspect]');
    if (!pre || pre.dataset.loaded) return;
    pre.dataset.loaded = '1';
    pre.textContent = 'Carregando…';
    try {
      const r = await fetch(`/api/ops/runtime/${encodeURIComponent(pre.dataset.app)}/inspect?env=${encodeURIComponent(pre.dataset.env)}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || j.message || `HTTP ${r.status}`);
      pre.textContent = JSON.stringify(j, null, 2);
    } catch (e) {
      pre.textContent = 'Não foi possível consultar o contêiner: ' + e.message;
      delete pre.dataset.loaded;
    }
  }
  function select(name, focus) {
    tabs.forEach((t) => {
      const on = t.dataset.opsTab === name;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      if (on && focus) t.focus();
    });
    if (!cur) return;
    cur.querySelectorAll('[data-ops-pane]').forEach((p) => { p.hidden = p.dataset.opsPane !== name; });
    if (name === 'logs') loadLogs(); else stopLogs();
    if (name === 'inspect') loadInspect();
    const p = pane(name);
    if (p) p.id = 'ops-pane-' + name;
  }
  function open(key, tab, from) {
    const d = drawer.querySelector(`[data-ops-detail="${CSS.escape(key)}"]`);
    if (!d) return;
    drawer.querySelectorAll('[data-ops-detail]').forEach((x) => { x.hidden = x !== d; x.querySelectorAll('[data-ops-pane]').forEach((p) => p.removeAttribute('id')); });
    cur = d;
    opener = from || null;
    drawer.querySelector('[data-ops-drawer-title]').textContent = d.dataset.title || '';
    drawer.querySelector('[data-ops-drawer-sub]').textContent = d.dataset.sub || '';
    drawer.hidden = false;
    if (backdrop) backdrop.hidden = false;
    document.body.classList.add('ops-drawer-open');
    select(tab || 'overview', false);
    drawer.querySelector('[data-ops-drawer-close]').focus();
  }
  function close() {
    stopLogs();
    drawer.hidden = true;
    if (backdrop) backdrop.hidden = true;
    document.body.classList.remove('ops-drawer-open');
    cur = null;
    if (opener && opener.isConnected) opener.focus();
  }
  document.addEventListener('click', (e) => {
    const o = e.target.closest('[data-ops-open]');
    if (o) {
      const [app, env, tab] = o.dataset.opsOpen.split('|');
      const menu = o.closest('details'); if (menu) menu.open = false;
      open(`${app}|${env}`, tab, o.closest('tr') || o);
      return;
    }
    const row = e.target.closest('tr[data-ops-row]');
    if (row && !e.target.closest('button,a,details,summary,input,select')) open(row.dataset.opsRow, 'overview', row);
    const cp = e.target.closest('[data-ops-copy]');
    if (cp && cur) {
      const pre = cp.closest('.ops-pane').querySelector('pre');
      const txt = pre ? pre.textContent : '';
      (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject(new Error('sem área de transferência')))
        .then(() => { cp.textContent = 'Copiado'; setTimeout(() => { cp.textContent = 'Copiar'; }, 1500); })
        .catch(() => { cp.textContent = 'Não foi possível copiar'; });
    }
  });
  document.addEventListener('keydown', (e) => {
    const row = e.target.closest && e.target.closest('tr[data-ops-row]');
    if (row && e.target === row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); open(row.dataset.opsRow, 'overview', row); }
    if (e.key === 'Escape' && !drawer.hidden && !document.querySelector('dialog[open]')) close();
  });
  drawer.querySelector('[data-ops-drawer-close]').addEventListener('click', close);
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => select(t.dataset.opsTab, false));
    t.addEventListener('keydown', (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (d) { e.preventDefault(); select(tabs[(i + d + tabs.length) % tabs.length].dataset.opsTab, true); }
    });
  });
  // Focus trapped in the panel while open (aria-modal).
  drawer.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const f = [...drawer.querySelectorAll('button:not([disabled]),[href],[tabindex="0"]')].filter((x) => x.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  window.addEventListener('pagehide', stopLogs);
})();
