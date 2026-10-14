/* admin.js — Administration interactions (and of the rail's submenu) in the hub shell.
 *
 * The /admin/* pages arrive ready from the server; this script only:
 *   - opens/closes the "Administration" submenu of the rail;
 *   - opens create/edit modals, row menus (edit/delete), reveals secrets,
 *     toggles permissions, saves settings and triggers "Check now";
 *   - talks to the JSON API (/api/admin/*, always application/json) and reloads the
 *     page after each mutation (the truth stays on the server);
 *   - keeps the log console (filters + "Real time" via SSE).
 * Values coming from the user/server enter the DOM only through textContent/value/esc(). */
(function () {
  'use strict';

  const API = '/api/admin';
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
  const esc = (s) =>
    String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const ICON = {
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M17.9 17.9A10.6 10.6 0 0 1 12 19c-6.5 0-10-7-10-7a17.7 17.7 0 0 1 4.2-4.9M9.9 4.2A9.4 9.4 0 0 1 12 4c6.5 0 10 7 10 7a17.6 17.6 0 0 1-2.2 3.1M14.1 14.1a3 3 0 1 1-4.2-4.2"/><path d="M2 2l20 20"/>',
    play: '<path d="M6 4v16l14-8Z"/>',
    pause: '<path d="M7 4h4v16H7zM13 4h4v16h-4z"/>',
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

  // ---- toast (same behavior as the mockup) ---------------------------------
  let toastTimer;
  function toast(message, kind) {
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('toast-error', kind === 'error');
    el.classList.toggle('toast-success', kind === 'success');
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
  }
  // Message to show after the reload that follows a mutation.
  function reloadWithToast(message) {
    try { sessionStorage.setItem('hub-admin-toast', message); } catch (e) { /* no storage: just reloads */ }
    location.reload();
  }
  try {
    const pending = sessionStorage.getItem('hub-admin-toast');
    if (pending) {
      sessionStorage.removeItem('hub-admin-toast');
      // Message after a completed mutation: success.
      setTimeout(() => toast(pending, 'success'), 50);
    }
  } catch (e) { /* ignore */ }

  // ---- API ------------------------------------------------------------------
  async function api(method, path, body) {
    const res = await fetch(API + path, {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? (method === 'GET' ? undefined : '{}') : JSON.stringify(body),
      credentials: 'same-origin',
    });
    if (res.status === 401) {
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
      throw new Error('Sessão expirada. Entre novamente.');
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.error) || `Erro ${res.status} ao falar com o servidor.`);
    return data;
  }
  const enc = encodeURIComponent;

  const metaEl = $('[data-admin-meta]');
  const meta = metaEl ? JSON.parse(metaEl.dataset.adminMeta) : { apps: [], permissions: [] };

  // ---- rail: Administration submenu -------------------------------------------
  document.addEventListener('click', (e) => {
    const toggle = e.target.closest('[data-rail-toggle]');
    if (!toggle) return;
    e.preventDefault();
    const menu = document.querySelector(`[data-rail-submenu="${toggle.dataset.railToggle}"]`);
    if (!menu) return;
    const open = !menu.classList.contains('open');
    menu.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
  });

  // ---- generic modal -------------------------------------------------------
  const KINDS = {
    env: { label: 'variável', create: 'Nova variável', path: '/env', created: 'Variável criada com sucesso.', updated: 'Variável atualizada.', deleted: 'Variável excluída.' },
    role: { label: 'função', create: 'Nova função', path: '/roles', created: 'Função criada com sucesso.', updated: 'Função atualizada.', deleted: 'Função excluída.' },
    health: { label: 'serviço', create: 'Novo serviço', path: '/health', created: 'Serviço adicionado com sucesso.', updated: 'Serviço atualizado.', deleted: 'Serviço removido.' },
  };

  function fieldsFor(kind, item) {
    const v = item || {};
    if (kind === 'env') {
      const secret = v.kind && v.kind !== 'publica';
      return [
        { key: 'name', label: 'Nome da variável', value: v.name || '', disabled: !!item, placeholder: 'MINHA_VARIAVEL', pattern: '[A-Z][A-Z0-9_]*' },
        { key: 'kind', label: 'Tipo', type: 'select', options: [['publica', 'Pública'], ['secreta', 'Secreta (valor mascarado)'], ['connection_string', 'Connection string (secreta)']], value: v.kind || 'publica' },
        { key: 'value', label: 'Valor', value: secret ? '' : (v.value || ''), placeholder: item && secret ? 'Deixe em branco para manter o valor atual' : 'Valor da variável', required: !(item && secret) },
        { key: 'inject_as', label: 'Nome dentro do container (opcional)', value: v.inject_as || '', placeholder: 'ex.: HUB_DB_URL', required: false, disabled: !!v.managed, pattern: '[A-Z][A-Z0-9_]*' },
        { key: 'bindings', label: 'Vínculos (aplicação × ambiente)', type: 'bindings', value: v.bindings || [] },
      ];
    }
    if (kind === 'role') {
      return [
        { key: 'name', label: 'Nome da função', value: v.name || '', disabled: !!item },
        { key: 'description', label: 'Descrição', value: v.description || '', required: false },
        { key: 'members', label: 'Usuários', value: String(v.members ?? 0), inputType: 'number' },
        { key: 'level', label: 'Nível de acesso', type: 'select', options: ['Total', 'Alto', 'Padrão'], value: v.level || 'Padrão' },
      ];
    }
    return [
      { key: 'name', label: 'Nome do serviço', value: v.name || '', disabled: !!item },
      { key: 'kind', label: 'Tipo', type: 'select', options: ['Aplicação', 'Serviço', 'Identity'], value: v.kind || 'Serviço' },
      { key: 'status', label: 'Status', type: 'select', options: ['Disponível', 'Manutenção', 'Indisponível'], value: v.status || 'Disponível' },
      { key: 'app', label: 'Aplicação monitorada (Verificar agora)', type: 'select', options: [['', 'Nenhuma']].concat(meta.apps.map((a) => [a.id, a.name])), value: v.app || '' },
    ];
  }

  function fieldHtml(f) {
    if (f.type === 'select') {
      const opts = f.options
        .map((o) => (Array.isArray(o) ? o : [o, o]))
        .map(([val, lab]) => `<option value="${esc(val)}" ${val === f.value ? 'selected' : ''}>${esc(lab)}</option>`)
        .join('');
      return `<div class="form-field"><label>${esc(f.label)}</label><select data-field="${f.key}">${opts}</select></div>`;
    }
    if (f.type === 'checkbox') {
      return `<div class="form-field"><label class="role-permission"><input type="checkbox" data-field="${f.key}" ${f.value ? 'checked' : ''}/><span>${esc(f.label)}</span></label></div>`;
    }
    if (f.type === 'bindings') {
      const has = (app, env) => f.value.some((b) => b.app === app && b.env === env);
      const envs = [['staging', 'Staging'], ['producao', 'Produção']];
      // Application × environment grid: rows = apps, columns = Staging/Production.
      return `<fieldset class="form-field binding-grid"><legend>${esc(f.label)}</legend><table class="binding-table"><thead><tr><th scope="col">Aplicação</th>${envs.map(([, l]) => `<th scope="col">${l}</th>`).join('')}</tr></thead><tbody>${meta.apps
        .map((a) => `<tr><th scope="row">${esc(a.name || a.short)}</th>${envs.map(([e, l]) => `<td><input type="checkbox" data-field-binding data-app="${esc(a.id)}" data-env="${e}" aria-label="${esc(a.name || a.short)} · ${l}" ${has(a.id, e) ? 'checked' : ''}/></td>`).join('')}</tr>`)
        .join('')}</tbody></table></fieldset>`;
    }
    if (f.type === 'apps') {
      const selected = f.value === 'all' ? meta.apps.map((a) => a.id) : f.value;
      return `<div class="form-field"><label>${esc(f.label)}</label><div class="modal-app-picker">${meta.apps
        .map((a) => `<label><input type="checkbox" data-field-app value="${esc(a.id)}" ${selected.includes(a.id) ? 'checked' : ''}/> ${esc(a.short)}</label>`)
        .join('')}</div></div>`;
    }
    const req = f.required === false ? '' : 'required';
    const pattern = f.pattern ? `pattern="${esc(f.pattern)}"` : '';
    const type = f.inputType === 'number' ? 'type="number" min="0"' : 'type="text"';
    return `<div class="form-field"><label>${esc(f.label)}</label><input ${type} data-field="${f.key}" value="${esc(f.value)}" ${f.disabled ? 'disabled' : ''} placeholder="${esc(f.placeholder || '')}" ${pattern} ${req}/></div>`;
  }

  function openModal(kind, item) {
    closeOverlay();
    const k = KINDS[kind];
    const fields = fieldsFor(kind, item);
    const title = item ? `Editar ${k.label}` : k.create;
    const host = document.createElement('div');
    host.className = 'overlay';
    host.innerHTML = `<section class="modal" role="dialog" aria-modal="true" aria-labelledby="admin-modal-title"><div class="modal-header"><h2 id="admin-modal-title">${esc(title)}</h2><button type="button" class="modal-close" aria-label="Fechar">×</button></div><form novalidate>${fields
      .map(fieldHtml)
      .join('')}<p class="modal-error" role="alert" hidden></p><div class="modal-actions"><button type="button" class="secondary-button modal-cancel">Cancelar</button><button type="submit" class="primary-button">${item ? 'Salvar alterações' : 'Adicionar'}</button></div></form></section>`;
    document.body.append(host);
    const close = () => host.remove();
    $('.modal-close', host).onclick = close;
    $('.modal-cancel', host).onclick = close;
    // Clicking outside does not close (what was typed would be lost); Esc only closes without changes.
    host.addEventListener('input', () => { host.dataset.dirty = '1'; }); host.addEventListener('change', () => { host.dataset.dirty = '1'; });
    const errorEl = $('.modal-error', host);
    $('form', host).onsubmit = async (e) => {
      e.preventDefault();
      const values = {};
      for (const f of fields) {
        if (f.type === 'bindings') {
          values.bindings = $$('[data-field-binding]:checked', host).map((c) => ({ app: c.dataset.app, env: c.dataset.env }));
        } else if (f.type === 'apps') {
          const checked = $$('[data-field-app]:checked', host).map((c) => c.value);
          values.scope = checked.length === meta.apps.length ? 'all' : checked;
        } else if (f.type === 'checkbox') {
          values[f.key] = $(`[data-field="${f.key}"]`, host).checked;
        } else {
          values[f.key] = $(`[data-field="${f.key}"]`, host).value.trim();
        }
      }
      if (kind === 'role') values.members = Number(values.members || 0);
      if (kind === 'env' && !values.inject_as) values.inject_as = null;
      if (kind === 'health' && !values.app) values.app = null;
      const submit = $('button[type="submit"]', host);
      submit.disabled = true;
      try {
        if (item) await api('PUT', `${k.path}/${enc(item.name)}`, values);
        else await api('POST', k.path, values);
        close();
        reloadWithToast(item ? k.updated : k.created);
      } catch (err) {
        errorEl.textContent = err.message;
        errorEl.hidden = false;
        submit.disabled = false;
      }
    };
    ($('input:not([disabled])', host) || $('input', host))?.focus();
  }

  function closeOverlay() { $$('.overlay').forEach((o) => o.remove()); }

  // ---- row menus -------------------------------------------------------
  function closeRowMenu() { $('.row-menu')?.remove(); }

  function openRowMenu(button) {
    closeRowMenu();
    const key = button.dataset.rowMenu;
    const scope = key.slice(0, key.indexOf(':'));
    const k = KINDS[scope];
    const menu = document.createElement('div');
    menu.className = 'row-menu';
    menu.dataset.forKey = key;
    for (const [verb, text, danger] of [['edit', `Editar ${k.label}`, false], ['delete', `Excluir ${k.label}`, true]]) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'row-menu-item' + (danger ? ' danger' : '');
      b.textContent = text;
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        closeRowMenu();
        rowAction(verb, scope, button);
      });
      menu.append(b);
    }
    document.body.appendChild(menu);
    const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    const rect = button.getBoundingClientRect();
    menu.style.top = `${(rect.bottom + 6) / zoom}px`;
    menu.style.right = `${Math.max(8, (window.innerWidth - rect.right) / zoom)}px`;
  }

  async function rowAction(verb, scope, button) {
    const holder = button.closest('[data-item]');
    const item = holder ? JSON.parse(holder.dataset.item) : null;
    if (!item) return;
    if (verb === 'edit') { openModal(scope, item); return; }
    try {
      await api('DELETE', `${KINDS[scope].path}/${enc(item.name)}`);
      reloadWithToast(KINDS[scope].deleted);
    } catch (err) { toast(err.message, 'error'); }
  }

  // ---- cliques delegados ----------------------------------------------------
  document.addEventListener('click', async (e) => {
    const menuBtn = e.target.closest('[data-row-menu]');
    if (menuBtn) {
      e.preventDefault();
      e.stopPropagation();
      const open = $('.row-menu');
      if (open && open.dataset.forKey === menuBtn.dataset.rowMenu) closeRowMenu();
      else openRowMenu(menuBtn);
      return;
    }
    if (!e.target.closest('.row-menu')) closeRowMenu();

    const add = e.target.closest('[data-admin-add]');
    if (add) { e.preventDefault(); openModal(add.dataset.adminAdd, null); return; }

    const roleToggle = e.target.closest('[data-role-toggle]');
    if (roleToggle) {
      e.preventDefault();
      const card = roleToggle.closest('.role-card');
      const open = !card.classList.contains('open');
      card.classList.toggle('open', open);
      roleToggle.setAttribute('aria-expanded', String(open));
      return;
    }

    const check = e.target.closest('[data-health-check]');
    if (check) {
      e.preventDefault();
      check.disabled = true;
      toast('Verificando serviços...');
      try {
        const data = await api('POST', '/health/check');
        const down = data.results.filter((r) => !r.available).length;
        reloadWithToast(down ? `${down} aplicação(ões) indisponível(is).` : 'Todas as aplicações estão disponíveis.');
      } catch (err) { toast(err.message, 'error'); check.disabled = false; }
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!$('.overlay[data-dirty]')) closeOverlay();
      closeRowMenu();
    }
  });

  // Role permissions.
  document.addEventListener('change', async (e) => {
    const box = e.target.closest('[data-role-perm]');
    if (!box) return;
    const role = box.closest('[data-role]').dataset.role;
    const granted = box.checked;
    try {
      await api('PUT', `/roles/${enc(role)}/permissions/${box.dataset.rolePerm}`, { granted });
      toast(granted ? 'Permissão concedida.' : 'Permissão revogada.');
    } catch (err) {
      box.checked = !granted;
      toast(err.message, 'error');
    }
  });

  // Applications allowed per role (all = null).
  document.addEventListener('change', async (e) => {
    const box = e.target.closest('[data-role-app], [data-role-app-all]');
    if (!box) return;
    const wrap = box.closest('[data-role-apps]');
    const role = box.closest('[data-role]').dataset.role;
    const all = $('[data-role-app-all]', wrap);
    const items = $$('[data-role-app]', wrap);
    if (box === all) items.forEach((i) => { i.checked = all.checked; });
    else all.checked = items.every((i) => i.checked);
    // "All" checked: the individual ones stay checked and locked (redundant).
    items.forEach((i) => { i.disabled = all.checked; });
    const apps = all.checked ? null : items.filter((i) => i.checked).map((i) => i.dataset.roleApp);
    try {
      await api('PUT', `/roles/${enc(role)}/apps`, { apps });
      toast(apps === null ? 'Função com acesso a todas as aplicações.' : 'Aplicações da função atualizadas.');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  // Local search in the tables.
  document.addEventListener('input', (e) => {
    const input = e.target.closest('[data-table-search]');
    if (!input) return;
    const q = input.value.trim().toLowerCase();
    const table = input.closest('.content-panel')?.querySelector('tbody');
    $$('tr', table).forEach((tr) => { tr.hidden = q && !tr.textContent.toLowerCase().includes(q); });
  });

  // Settings.
  const settingsForm = $('[data-admin-settings]');
  if (settingsForm) {
    settingsForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = {
        platform_name: settingsForm.platform_name.value.trim(),
        brand_color: settingsForm.brand_color_on.checked ? settingsForm.brand_color.value : '',
        status_public: settingsForm.status_public.checked,
        status_public_apps: $$('input[name="status_public_app"]:checked', settingsForm).map((i) => i.value),
        language: settingsForm.language.value,
        timezone: settingsForm.timezone.value,
      };
      try {
        await api('PUT', '/settings', body);
        reloadWithToast('Preferências salvas com sucesso.');
      } catch (err) { toast(err.message, 'error'); }
    });
  }

  // ---- console de logs -------------------------------------------------------
  const consoleEl = $('[data-log-console]');
  if (consoleEl) {
    const terminal = $('[data-log-terminal]', consoleEl);
    const appSel = $('[data-log-app]', consoleEl);
    const rangeSel = $('[data-log-range]', consoleEl);
    const userInput = $('[data-log-user]', consoleEl);
    const levelSel = $('[data-log-level]', consoleEl);
    const sourceSel = $('[data-log-source]', consoleEl);
    const liveBtn = $('[data-log-live]', consoleEl);
    const status = $('[data-log-status]', consoleEl);
    const appName = (id) => (meta.apps.find((a) => a.id === id) || { short: 'Plataforma' }).short;
    let share = null;

    function lineEl(l, fresh) {
      const cls = l.level === 'ERROR' ? 'log-level-error' : l.level === 'WARN' ? 'log-level-warn' : 'log-level-info';
      const div = document.createElement('div');
      div.className = 'log-line' + (fresh ? ' log-line-new' : '');
      for (const [c, text] of [['log-time', l.time], [`log-level ${cls}`, l.level], ['log-app', appName(l.app)], ['log-user', l.user ? '@' + l.user : ''], ['log-msg', l.message]]) {
        // Message = technical log data (monospaced, as on the server).
        const s = document.createElement(c === 'log-msg' ? 'code' : 'span');
        s.className = c;
        s.textContent = text;
        div.append(s);
      }
      return div;
    }
    function empty(text) {
      terminal.innerHTML = '';
      const d = document.createElement('div');
      d.className = 'log-empty';
      d.textContent = text;
      terminal.append(d);
    }
    function params(withRange) {
      const p = new URLSearchParams();
      p.set('app', appSel.value);
      if (withRange) p.set('minutes', rangeSel.value);
      if (userInput.value.trim()) p.set('user', userInput.value.trim());
      if (levelSel && levelSel.value) p.set('level', levelSel.value);
      if (sourceSel && sourceSel.value) p.set('source', sourceSel.value);
      return p.toString();
    }
    async function query() {
      try {
        const rows = await api('GET', `/logs?${params(true)}`);
        if (!rows.length) { empty('Nenhum log encontrado para os filtros selecionados.'); return; }
        terminal.innerHTML = '';
        rows.slice(-300).forEach((l) => terminal.append(lineEl(l, false)));
        terminal.scrollTop = terminal.scrollHeight;
      } catch (err) { toast(err.message, 'error'); }
    }
    function setLive(on) {
      liveBtn.classList.toggle('live', on);
      liveBtn.setAttribute('aria-pressed', String(on));
      liveBtn.innerHTML = `${svg(on ? 'pause' : 'play')}<span>${on ? 'Pausar' : 'Tempo real'}</span>`;
      status.classList.toggle('connected', on);
      status.innerHTML = `<i class="log-status-dot"></i>${on ? 'Conectado' : 'Parado'}`;
    }
    // Real time: ONE SSE connection for all tabs (logshare.js: the leader, with the browser
    // lock, opens the EventSource without a filter and relays it through BroadcastChannel; each tab
    // filters locally). Without logshare.js, its own connection filtered on the server.
    function stop() { if (share) { share.stop(); share = null; } setLive(false); }
    function append(l) {
      $('.log-empty', terminal)?.remove();
      terminal.append(lineEl(l, true));
      while (terminal.children.length > 300) terminal.removeChild(terminal.firstChild);
      terminal.scrollTop = terminal.scrollHeight;
    }
    function start() {
      stop();
      const S = window.HubLogShare;
      const filter = () => ({ app: appSel.value, user: userInput.value.trim() });
      const inList = (sel, v) => !sel || !sel.value || sel.value.split(',').includes(v);
      const extra = (l) => inList(levelSel, l.level) && inList(sourceSel, l.source);
      const shared = Boolean(S);
      const url = shared ? `${API}/logs/stream?app=all` : `${API}/logs/stream?${params(false)}`;
      const onStatus = (s) => {
        status.classList.toggle('connected', s.connected);
        status.dataset.logRole = s.role;
        const who = s.role === 'líder' ? 'esta aba' : 'compartilhada';
        status.innerHTML = `<i class="log-status-dot"></i>${s.connected ? `Conectado · conexão ${who}` : 'Reconectando...'}`;
      };
      share = shared
        ? S.createLogShare({ openSource: () => new EventSource(url), onLine: (l) => { if (S.matches(l, filter()) && extra(l)) append(l); }, onStatus })
        : (() => {
          let src = null;
          return {
            start() {
              src = new EventSource(url);
              src.addEventListener('ready', () => onStatus({ connected: true, role: 'líder' }));
              src.addEventListener('log', (ev) => append(JSON.parse(ev.data)));
              src.onerror = () => onStatus({ connected: false, role: 'líder' });
            },
            stop() { if (src) { src.close(); src = null; } },
          };
        })();
      setLive(true);
      share.start();
    }
    const refilter = () => { query(); if (share && !window.HubLogShare) start(); };
    appSel.addEventListener('change', refilter);
    rangeSel.addEventListener('change', query);
    if (levelSel) levelSel.addEventListener('change', refilter);
    if (sourceSel) sourceSel.addEventListener('change', refilter);
    let userTimer;
    userInput.addEventListener('input', () => { clearTimeout(userTimer); userTimer = setTimeout(refilter, 250); });
    $('[data-log-query]', consoleEl).addEventListener('click', query);
    $('[data-log-clear]', consoleEl).addEventListener('click', () => empty('Terminal limpo.'));
    liveBtn.addEventListener('click', () => (share ? stop() : start()));
    window.addEventListener('pagehide', stop);
    terminal.scrollTop = terminal.scrollHeight;
  }
})();
