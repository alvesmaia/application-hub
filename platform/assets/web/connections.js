/* connections.js — Applications › Connections (wave 10).
 *
 * The page arrives ready from the server (without any credential); this script opens the
 * create/edit/links/rotate modals, reads the chosen certificate (PEM or PFX) as
 * base64, triggers "Test connection" and shows each step, and talks to
 * /api/admin/connections (JSON). Texts in the user's language (data-hub-i18n of the body).
 * Values coming from the user/server enter the DOM only through textContent/value/esc(). */
(function () {
  'use strict';

  const API = '/api/admin/connections';
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
  const esc = (s) =>
    String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  let dict = {};
  try { dict = JSON.parse(document.body.dataset.hubI18n || '{}'); } catch (e) { dict = {}; }
  const t = (k) => dict['js.conn_' + k] || k;
  const metaEl = $('[data-conn-meta]');
  const meta = metaEl ? JSON.parse(metaEl.dataset.connMeta) : { apps: [] };
  const OPS = ['upload', 'download', 'list', 'delete', 'move', 'share_link'];

  function toast(message, kind) {
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('toast-error', kind === 'error');
    el.classList.toggle('toast-success', kind === 'success');
    el.classList.add('show');
    setTimeout(() => el.classList.remove('show'), 2600);
  }
  function reloadWithToast(message) {
    // admin.js shows the message after the reload (same key).
    try { sessionStorage.setItem('hub-admin-toast', message); } catch (e) { /* no storage */ }
    location.reload();
  }

  async function api(method, path, body) {
    const res = await fetch(API + path, {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? (method === 'GET' || method === 'DELETE' ? undefined : '{}') : JSON.stringify(body),
      credentials: 'same-origin',
    });
    if (res.status === 401) {
      location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
      throw new Error('401');
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }
  const enc = encodeURIComponent;

  function itemOf(name) {
    const row = $(`tr[data-conn-row="${CSS.escape(name)}"]`);
    return row ? JSON.parse(row.dataset.item) : null;
  }

  // ---- accessible modal ----------------------------------------------------------
  let seq = 0;
  function modal(title, bodyHtml, onSubmit, submitLabel) {
    $$('.overlay').forEach((o) => o.remove());
    const opener = document.activeElement;
    const id = `conn-modal-${++seq}`;
    const host = document.createElement('div');
    host.className = 'overlay';
    host.innerHTML = `<section class="modal conn-modal" role="dialog" aria-modal="true" aria-labelledby="${id}"><div class="modal-header"><h2 id="${id}">${esc(title)}</h2><button type="button" class="modal-close" aria-label="${esc(t('close'))}">×</button></div><form novalidate>${bodyHtml}<p class="modal-error" role="alert" hidden></p><div class="modal-actions"><button type="button" class="secondary-button modal-cancel">${esc(t('cancel'))}</button><button type="submit" class="primary-button">${esc(submitLabel || t('save'))}</button></div></form></section>`;
    document.body.append(host);
    const close = () => { host.remove(); if (opener && opener.focus) opener.focus(); };
    $('.modal-close', host).onclick = close;
    $('.modal-cancel', host).onclick = close;
    // Clicking outside does not close (what was typed would be lost); Esc only closes without changes.
    host.addEventListener('input', () => { host.dataset.dirty = '1'; }); host.addEventListener('change', () => { host.dataset.dirty = '1'; });
    host.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); if (!host.dataset.dirty) close(); }
      if (e.key === 'Tab') {
        const f = $$('button, input, select, textarea', host).filter((x) => !x.disabled && x.offsetParent !== null);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    });
    const errorEl = $('.modal-error', host);
    $('form', host).onsubmit = async (e) => {
      e.preventDefault();
      const submit = $('button[type="submit"]', host);
      submit.disabled = true;
      errorEl.hidden = true;
      try {
        await onSubmit(host);
      } catch (err) {
        errorEl.textContent = err.message;
        errorEl.hidden = false;
        submit.disabled = false;
      }
    };
    ($('input:not([disabled]):not([type="hidden"])', host) || $('select', host))?.focus();
    return host;
  }

  let fid = 0;
  function field(key, label, value, opts = {}) {
    const id = `conn-f-${++fid}`;
    const type = opts.type || 'text';
    const req = opts.required ? 'required aria-required="true"' : '';
    const dis = opts.disabled ? 'disabled' : '';
    const ph = opts.placeholder ? `placeholder="${esc(opts.placeholder)}"` : '';
    const extra = type === 'number' ? 'min="1" max="2048"' : type === 'password' ? 'autocomplete="new-password"' : 'autocomplete="off"';
    return `<div class="form-field" data-wrap="${key}"><label for="${id}">${esc(label)}</label><input id="${id}" type="${type}" data-field="${key}" value="${esc(value ?? '')}" ${req} ${dis} ${ph} ${extra}/></div>`;
  }
  function select(key, label, value, options) {
    const id = `conn-f-${++fid}`;
    return `<div class="form-field" data-wrap="${key}"><label for="${id}">${esc(label)}</label><select id="${id}" data-field="${key}">${options
      .map(([v, l]) => `<option value="${esc(v)}" ${v === value ? 'selected' : ''}>${esc(l)}</option>`)
      .join('')}</select></div>`;
  }
  function fileField(key, label) {
    const id = `conn-f-${++fid}`;
    return `<div class="form-field" data-wrap="${key}"><label for="${id}">${esc(label)}</label><input id="${id}" type="file" data-field="${key}" accept=".pem,.pfx,.p12,.crt,.key"/></div>`;
  }
  function opsField(selected) {
    return `<fieldset class="form-field conn-fieldset"><legend>${esc(t('ops'))}</legend><div class="modal-app-picker">${OPS.map(
      (o) => `<label><input type="checkbox" data-op value="${o}" ${selected.includes(o) ? 'checked' : ''}/> ${esc(t('op_' + o))}</label>`,
    ).join('')}</div></fieldset>`;
  }
  function bindingsField(value) {
    const has = (app, env) => value.some((b) => b.app === app && b.env === env);
    return `<fieldset class="form-field conn-fieldset"><legend>${esc(dict['js.conn_bindings_title'] || '')}</legend><div class="modal-app-picker">${meta.apps
      .map((a) => [['staging', t('staging')], ['producao', t('prod')]]
        .map(([e, l]) => `<label><input type="checkbox" data-binding data-app="${esc(a.id)}" data-env="${e}" ${has(a.id, e) ? 'checked' : ''}/> ${esc(a.short)} · ${esc(l)}</label>`)
        .join(''))
      .join('')}</div></fieldset>`;
  }
  function anonAppsField(value) {
    return `<fieldset class="form-field conn-fieldset" data-wrap="anonymous_link_apps"><legend>${esc(t('anon_apps'))}</legend><p class="form-hint">${esc(t('anon_apps_hint'))}</p><div class="modal-app-picker">${meta.apps
      .map((a) => `<label><input type="checkbox" data-anon-app value="${esc(a.id)}" ${(value || []).includes(a.id) ? 'checked' : ''}/> ${esc(a.short)}</label>`)
      .join('')}</div></fieldset>`;
  }
  function linkPolicyFields(v, kind) {
    const id = `conn-f-${++fid}`;
    let html = `<div class="form-field" data-wrap="max_link_hours"><label for="${id}">${esc(t('link_hours'))}</label><input id="${id}" type="number" min="1" max="720" data-field="max_link_hours" value="${esc(String(v.max_link_hours || 168))}"/></div>`;
    if (kind === 'sharepoint') {
      html += `<div class="form-field"><label><input type="checkbox" data-field-check="allow_edit_links" ${v.allow_edit_links ? 'checked' : ''}/> ${esc(t('allow_edit'))}</label></div>`;
    }
    return html + anonAppsField(v.anonymous_link_apps);
  }
  const val = (host, k) => { const el = $(`[data-field="${k}"]`, host); return el ? el.value.trim() : ''; };

  function readFile(input) {
    return new Promise((resolve, reject) => {
      const f = input && input.files && input.files[0];
      if (!f) return resolve('');
      if (f.size > 64 * 1024) return reject(new Error('> 64 KB'));
      const r = new FileReader();
      r.onload = () => {
        const bytes = new Uint8Array(r.result);
        let bin = '';
        for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
        resolve(btoa(bin));
      };
      r.onerror = () => reject(new Error('arquivo ilegível'));
      r.readAsArrayBuffer(f);
    });
  }

  function syncAuth(host) {
    const auth = val(host, 'auth');
    $$('[data-wrap="secret"]', host).forEach((w) => { w.hidden = auth !== 'secret'; });
    $$('[data-wrap="certificate"],[data-wrap="certificate_password"]', host).forEach((w) => { w.hidden = auth !== 'certificate'; });
  }

  // SQL databases (PostgreSQL, MySQL/MariaDB, SQL Server): app queries through the broker.
  const SQL_PORTS = { postgres: 5432, mysql: 3306, sqlserver: 1433 };
  function openSqlEditor(kind, item) {
    const v = item || { readonly: true, max_rows: 5000, timeout_s: 30, ssl: 'prefer', bindings: [] };
    const editing = !!item;
    const num = (key, label, value, min, max) => {
      const id = `conn-f-${++fid}`;
      return `<div class="form-field" data-wrap="${key}"><label for="${id}">${esc(label)}</label><input id="${id}" type="number" min="${min}" max="${max}" data-field="${key}" value="${esc(String(value))}"/></div>`;
    };
    let html = field('name', t('name'), v.name, { required: true, disabled: editing, placeholder: 'vendas-bi' }) +
      field('description', t('description'), v.description) +
      field('host', t('sql_host'), v.host, { required: true, placeholder: 'db.empresa.local' }) +
      num('port', t('sql_port'), v.port || SQL_PORTS[kind], 1, 65535) +
      field('database', t('sql_database'), v.database, { required: true }) +
      field('username', t('sql_user'), v.username, { required: true }) +
      field('secret', t('sql_password'), '', { type: 'password', placeholder: editing ? t('secret_keep') : '' }) +
      select('ssl', t('sql_ssl'), v.ssl || 'prefer', [['prefer', t('sql_ssl_prefer')], ['require', t('sql_ssl_require')], ['disable', t('sql_ssl_disable')]]) +
      `<div class="form-field"><label><input type="checkbox" data-field-check="readonly" ${v.readonly !== false ? 'checked' : ''}/> ${esc(t('sql_readonly'))}</label></div>` +
      num('max_rows', t('sql_max_rows'), v.max_rows || 5000, 1, 50000) +
      num('timeout_s', t('sql_timeout'), v.timeout_s || 30, 1, 300);
    if (!editing) html += bindingsField(v.bindings);
    const title = editing ? t('edit') : t('new_' + kind);
    modal(title, html, async (h) => {
      const body = {
        kind,
        name: val(h, 'name'),
        description: val(h, 'description'),
        host: val(h, 'host'),
        port: Number(val(h, 'port') || SQL_PORTS[kind]),
        database: val(h, 'database'),
        username: val(h, 'username'),
        secret: val(h, 'secret'),
        ssl: val(h, 'ssl'),
        readonly: !!$('[data-field-check="readonly"]', h)?.checked,
        max_rows: Number(val(h, 'max_rows') || 5000),
        timeout_s: Number(val(h, 'timeout_s') || 30),
      };
      if (!editing) body.bindings = $$('[data-binding]:checked', h).map((c) => ({ app: c.dataset.app, env: c.dataset.env }));
      if (editing) await api('PUT', `/${enc(item.name)}`, body);
      else await api('POST', '', body);
      reloadWithToast(editing ? t('saved') : t('created'));
    });
  }

  function openEditor(kind, item) {
    if (SQL_PORTS[kind]) return openSqlEditor(kind, item);
    const v = item || { allowed_ops: ['upload', 'download', 'list', 'delete', 'move'], max_upload_mb: 100, bindings: [], auth: 'secret', share_scope: 'organization' };
    const editing = !!item;
    let html = field('name', t('name'), v.name, { required: true, disabled: editing, placeholder: 'contratos-docs' }) +
      field('description', t('description'), v.description);
    if (kind === 'sharepoint') {
      html +=
        field('tenant_id', t('tenant'), v.tenant_id, { required: true, placeholder: 'contoso.onmicrosoft.com' }) +
        field('client_id', t('client'), v.client_id, { required: true, placeholder: '00000000-0000-0000-0000-000000000000' }) +
        select('auth', t('auth'), v.auth, [['certificate', t('auth_cert')], ['secret', t('auth_secret')]]) +
        field('secret', t('secret'), '', { type: 'password', placeholder: editing ? t('secret_keep') : '' }) +
        fileField('certificate', t('cert_file')) +
        field('certificate_password', t('cert_password'), '', { type: 'password' }) +
        field('site', t('site'), v.site, { required: true, placeholder: 'https://contoso.sharepoint.com/sites/Financeiro' }) +
        field('drive', t('drive'), v.drive, { placeholder: 'Documentos' }) +
        field('base_folder', t('base'), v.base_folder, { placeholder: 'Hub/Contratos' }) +
        select('share_scope', t('scope'), v.share_scope || 'organization', [['organization', t('scope_org')], ['users', t('scope_users')], ['anonymous', t('scope_anon')]]);
    } else {
      html +=
        field('endpoint', t('endpoint'), v.endpoint, { required: true, placeholder: 'https://s3.exemplo.com' }) +
        field('bucket', t('bucket'), v.bucket, { required: true }) +
        field('region', t('region'), v.region, { placeholder: 'us-east-1' }) +
        field('access_key', t('access_key'), v.access_key, { required: true }) +
        field('secret', t('secret_key'), '', { type: 'password', placeholder: editing ? t('secret_keep') : '' }) +
        field('base_folder', t('base'), v.base_folder) +
        field('public_url', t('public_url'), v.public_url);
    }
    html += linkPolicyFields(v, kind);
    html += opsField(v.allowed_ops) + field('max_upload_mb', t('max_mb'), String(v.max_upload_mb), { type: 'number' });
    if (!editing) html += bindingsField(v.bindings);
    const title = editing ? t('edit') : kind === 'sharepoint' ? t('new_sp') : t('new_s3');
    const host = modal(title, html, async (h) => {
      const body = {
        kind,
        name: val(h, 'name'),
        description: val(h, 'description'),
        base_folder: val(h, 'base_folder'),
        allowed_ops: $$('[data-op]:checked', h).map((c) => c.value),
        max_upload_mb: Number(val(h, 'max_upload_mb') || 100),
        secret: val(h, 'secret'),
        max_link_hours: Number(val(h, 'max_link_hours') || 168),
        anonymous_link_apps: $$('[data-anon-app]:checked', h).map((c) => c.value),
      };
      if (kind === 'sharepoint') {
        Object.assign(body, {
          tenant_id: val(h, 'tenant_id'), client_id: val(h, 'client_id'), site: val(h, 'site'),
          drive: val(h, 'drive'), auth: val(h, 'auth'), share_scope: val(h, 'share_scope'),
          allow_edit_links: !!$('[data-field-check="allow_edit_links"]', h)?.checked,
          certificate_b64: await readFile($('[data-field="certificate"]', h)),
          certificate_password: $('[data-field="certificate_password"]', h).value,
        });
      } else {
        Object.assign(body, { endpoint: val(h, 'endpoint'), bucket: val(h, 'bucket'), region: val(h, 'region'), access_key: val(h, 'access_key'), public_url: val(h, 'public_url') });
      }
      if (!editing) body.bindings = $$('[data-binding]:checked', h).map((c) => ({ app: c.dataset.app, env: c.dataset.env }));
      if (editing) await api('PUT', `/${enc(item.name)}`, body);
      else await api('POST', '', body);
      reloadWithToast(editing ? t('saved') : t('created'));
    });
    if (kind === 'sharepoint') {
      $('[data-field="auth"]', host).addEventListener('change', () => syncAuth(host));
      syncAuth(host);
    }
  }

  function openBindings(item) {
    // Broker token per linked app × environment: "Rotate" takes effect right away (the app
    // receives the new one after Apply/restart).
    const short = (id) => (meta.apps.find((a) => a.id === id) || { short: id }).short;
    const tokens = (item.bindings || []).length
      ? `<fieldset class="form-field conn-fieldset"><legend>${esc(t('tokens_title'))}</legend><p class="form-hint">${esc(t('tokens_hint'))}</p><div class="conn-token-list">${item.bindings
        .map((b) => `<button type="button" class="secondary-button" data-rotate-token data-app="${esc(b.app)}" data-env="${esc(b.env)}">${esc(t('rotate_token'))} · ${esc(short(b.app))} · ${esc(b.env === 'producao' ? t('prod') : t('staging'))}</button>`)
        .join('')}</div></fieldset>`
      : '';
    const host = modal(t('bindings_title'), bindingsField(item.bindings) + tokens, async (h) => {
      const bindings = $$('[data-binding]:checked', h).map((c) => ({ app: c.dataset.app, env: c.dataset.env }));
      await api('PUT', `/${enc(item.name)}/bindings`, { bindings });
      reloadWithToast(t('bindings_saved'));
    });
    $$('[data-rotate-token]', host).forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (!await window.HubConfirm(t('rotate_token_confirm'))) return;
        btn.disabled = true;
        try {
          await api('POST', `/tokens/${enc(btn.dataset.app)}/${enc(btn.dataset.env)}/rotate`);
          toast(t('token_rotated'));
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  function openRotate(item) {
    const cert = item.kind === 'sharepoint' && item.auth === 'certificate';
    const html = cert
      ? fileField('certificate', t('cert_file')) + field('certificate_password', t('cert_password'), '', { type: 'password' })
      : field('secret', item.kind === 's3' ? t('secret_key') : SQL_PORTS[item.kind] ? t('sql_password') : t('secret'), '', { type: 'password', required: true });
    modal(`${t('rotate_title')} · ${item.name}`, html, async (h) => {
      const body = cert
        ? { certificate_b64: await readFile($('[data-field="certificate"]', h)), certificate_password: $('[data-field="certificate_password"]', h).value }
        : { secret: val(h, 'secret') };
      await api('POST', `/${enc(item.name)}/rotate`, body);
      reloadWithToast(t('rotated'));
    });
  }

  function renderSteps(box, result, kind) {
    const steps = result.steps || [];
    const list = document.createElement('ol');
    list.className = 'ops-steps conn-steps';
    for (const s of steps) {
      const li = document.createElement('li');
      li.className = s.ok === true ? 'done' : s.ok === false ? 'failed' : 'skipped';
      li.dataset.step = s.key;
      li.innerHTML = '<span class="ops-step-dot" aria-hidden="true"></span><div><strong></strong><small></small></div>';
      const label = t('step_' + s.key);
      const state = s.ok === true ? '✓' : s.ok === false ? '✗' : '–';
      $('strong', li).textContent = `${state} ${label}`;
      $('small', li).textContent = s.ok === null ? t('skipped') : `${s.detail}${s.ms ? ` (${s.ms} ms)` : ''}`;
      list.append(li);
    }
    box.textContent = '';
    const head = document.createElement('p');
    head.className = result.ok ? 'conn-test-head ok' : 'conn-test-head fail';
    head.textContent = result.ok ? t('test_ok') : t('test_fail');
    box.append(head, list);
    void kind;
  }

  async function runTest(name, btn) {
    const row = $(`tr[data-conn-test-row="${CSS.escape(name)}"]`);
    const box = $(`[data-conn-result="${CSS.escape(name)}"]`);
    const pill = $(`[data-conn-status="${CSS.escape(name)}"]`);
    if (row) row.hidden = false;
    if (box) box.textContent = t('testing');
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    try {
      const r = await api('POST', `/${enc(name)}/test`);
      if (box) renderSteps(box, r);
      if (pill) {
        pill.className = r.ok ? 'status-pill status-active' : 'status-pill status-maintenance';
        pill.textContent = r.ok ? 'OK' : '✗';
      }
    } catch (e) {
      if (box) box.textContent = e.message;
    } finally {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
    }
  }

  // ---- Wave 12 (2.7): provision / deprovision storage ------------------
  const storageForm = $('[data-conn-storage-form]');
  if (storageForm) {
    storageForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = $('[data-conn-storage-provision]', storageForm);
      const app = $('[data-conn-storage-app]', storageForm).value;
      const env = $('[data-conn-storage-env]', storageForm).value;
      btn.disabled = true;
      btn.setAttribute('aria-busy', 'true');
      try {
        const r = await api('POST', `/storage/${enc(app)}?env=${enc(env)}`);
        reloadWithToast(r && r.changed === false ? t('storage_same') : t('storage_done'));
      } catch (err) {
        toast(err.message, 'error');
        btn.disabled = false;
        btn.removeAttribute('aria-busy');
      }
    });
  }
  function openDeprovision(key, bucket) {
    const [app, env] = key.split('|');
    const html = field('confirm', t('deprov_confirm'), '', { required: true, placeholder: bucket })
      + `<div class="form-field"><label><input type="checkbox" data-field="force"/> ${esc(t('deprov_force'))}</label></div>`
      + field('password', t('deprov_password'), '', { type: 'password' });
    modal(`${t('deprov_title')} · ${bucket}`, html, async (h) => {
      const body = {
        confirm: $('[data-field="confirm"]', h).value.trim(),
        force: $('[data-field="force"]', h).checked,
        password: $('[data-field="password"]', h).value,
      };
      await api('POST', `/storage/${enc(app)}/deprovision?env=${enc(env)}`, body);
      reloadWithToast(t('deprov_done'));
    }, t('deprov_submit'));
  }
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-conn-storage-deprovision]');
    if (!b) return;
    e.preventDefault();
    openDeprovision(b.dataset.connStorageDeprovision, b.dataset.bucket);
  });

  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-conn-new],[data-conn-edit],[data-conn-bindings],[data-conn-rotate],[data-conn-delete],[data-conn-test]');
    if (!b) return;
    e.preventDefault();
    const d = b.dataset;
    if (d.connNew) return openEditor(d.connNew);
    const name = d.connEdit || d.connBindings || d.connRotate || d.connDelete || d.connTest;
    const item = itemOf(name);
    if (!item) return;
    if (d.connEdit) return openEditor(item.kind, item);
    if (d.connBindings) return openBindings(item);
    if (d.connRotate) return openRotate(item);
    if (d.connTest) return runTest(name, b);
    if (d.connDelete) {
      if (!await window.HubConfirm(t('confirm_delete').replace('{q}', `"${name}"`))) return;
      try {
        await api('DELETE', `/${enc(name)}`);
        reloadWithToast(t('deleted'));
      } catch (err) {
        toast(err.message, 'error');
      }
    }
  });
})();
