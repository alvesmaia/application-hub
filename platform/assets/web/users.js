/* users.js - Access › Users.
 *
 * The pages arrive ready from the server; this script opens the forms (mockup's
 * modal), calls /api/admin/users* (application/json,
 * same origin) and reloads the page after each mutation. Values from the server and the
 * user enter the DOM only through textContent/value or esc(). */
(function () {
  'use strict';

  const API = '/api/admin';
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
  const esc = (s) =>
    String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const enc = encodeURIComponent;
  const metaEl = $('[data-users-meta]');
  const meta = metaEl ? JSON.parse(metaEl.dataset.usersMeta) : { roles: [], apps: [] };

  let toastTimer;
  function toast(message, kind) {
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('toast-error', kind === 'error');
    el.classList.toggle('toast-success', kind === 'success');
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
  }
  function reloadWithToast(message) {
    try { sessionStorage.setItem('hub-admin-toast', message); } catch (e) { /* no storage */ }
    location.reload();
  }

  async function api(method, path, body) {
    const res = await fetch(API + path, {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
      credentials: 'same-origin',
    });
    if (res.status === 401) {
      location.href = `/login?next=${enc(location.pathname)}`;
      throw new Error('Sessão expirada. Entre novamente.');
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.error) || `Erro ${res.status} ao falar com o servidor.`);
    return data;
  }

  // Suggested temporary password (the admin can change it before saving).
  function suggestPassword() {
    const alphabet = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!@#';
    const bytes = new Uint32Array(16);
    crypto.getRandomValues(bytes);
    let s = '';
    bytes.forEach((b) => { s += alphabet[b % alphabet.length]; });
    return `${s}-7a`;
  }

  // ---- modal ---------------------------------------------------------------
  function closeModal() {
    $('.overlay[data-users-modal]')?.remove();
  }
  function openModal(title, copy, fieldsHtml, submitLabel, onSubmit) {
    closeModal();
    const wrap = document.createElement('div');
    wrap.className = 'overlay';
    wrap.dataset.usersModal = '';
    wrap.innerHTML = `<form class="modal hub-sheet" novalidate>
      <div class="modal-header"><h2>${esc(title)}</h2><button type="button" class="modal-close" data-close aria-label="Fechar">×</button></div>
      ${copy ? `<p class="modal-copy">${esc(copy)}</p>` : ''}
      ${fieldsHtml}
      <div class="auth-alert hidden" data-modal-error></div>
      <div class="modal-actions"><button type="button" class="secondary-button" data-close>Cancelar</button><button type="submit" class="primary-button">${esc(submitLabel)}</button></div>
    </form>`;
    document.body.appendChild(wrap);
    const form = $('form', wrap);
    // Clicking outside does not close (what was typed would be lost); Esc only closes without changes.
    wrap.addEventListener('input', () => { wrap.dataset.dirty = '1'; }); wrap.addEventListener('change', () => { wrap.dataset.dirty = '1'; });
    wrap.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) closeModal(); });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = $('[data-modal-error]', form);
      err.classList.add('hidden');
      const btn = $('button[type=submit]', form);
      btn.disabled = true;
      try {
        await onSubmit(form);
      } catch (ex) {
        err.textContent = ex.message;
        err.classList.remove('hidden');
      } finally {
        btn.disabled = false;
      }
    });
    $('input, select', form)?.focus();
    return form;
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('.overlay[data-users-modal][data-dirty]')) closeModal();
  });

  const field = (label, name, type = 'text', value = '', extra = '') =>
    `<div class="form-field"><label>${esc(label)}</label><input name="${name}" type="${type}" value="${esc(value)}" ${extra}/></div>`;
  const checks = (label, name, items, checked) =>
    `<div class="form-field"><label>${esc(label)}</label><div class="role-permissions">${items
      .map((it) => `<label class="role-permission"><input type="checkbox" name="${name}" value="${esc(it.value)}" ${checked(it.value) ? 'checked' : ''}/><span>${esc(it.label)}</span></label>`)
      .join('')}</div></div>`;
  // Temporary password with "Generate another" and "Copy".
  const pwField = (pw) =>
    `<div class="form-field"><label for="user-pw">Senha provisória</label><div class="pw-row"><input id="user-pw" name="password" type="text" value="${esc(pw)}" required minlength="10" autocomplete="off"/><button type="button" class="secondary-button" data-pw-gen>Gerar outra</button><button type="button" class="secondary-button" data-pw-copy>Copiar</button></div></div>`;
  const values = (form, name) => $$(`input[name="${name}"]:checked`, form).map((i) => i.value);
  const roleItems = meta.roles.map((r) => ({ value: r, label: r }));

  const userOf = (id) => {
    const row = $(`[data-user-id="${CSS.escape(id)}"]`);
    return row ? JSON.parse(row.dataset.user) : null;
  };

  // Credentials of a read account: shown once (the hub does not keep the password).
  function showCredentials(out) {
    const c = out.connection || {};
    const dbs = out.dbs || [];
    const ro = (label, value, id = '') => `<div class="form-field"><label>${esc(label)}</label><input ${id ? `id="${id}"` : ''} type="text" readonly value="${esc(value)}"/></div>`;
    openModal(
      'Credenciais da conta de leitura',
      '',
      ro('Host', c.host) + ro('Porta', c.port) + ro(dbs.length > 1 ? 'Bancos' : 'Banco', dbs.join(', ')) + ro('Usuário', out.role)
        + `<div class="form-field"><label for="dbaccess-password">Senha</label><div class="pw-row"><input id="dbaccess-password" type="text" readonly value="${esc(out.password)}"/><button type="button" class="secondary-button" data-dbaccess-copy>Copiar</button></div></div>`
        + ro('SSL', 'sslmode=require'),
      'Concluir',
      async () => reloadWithToast('Conta de leitura pronta.'),
    );
  }

  // ---- Users -------------------------------------------------------------
  document.addEventListener('click', async (e) => {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.matches('[data-pw-gen]')) { const i = t.closest('.pw-row').querySelector('input'); i.value = suggestPassword(); i.dispatchEvent(new Event('input', { bubbles: true })); return; }
    if (t.matches('[data-pw-copy]')) {
      const v = t.closest('.pw-row').querySelector('input').value;
      try { await navigator.clipboard.writeText(v); toast('Senha copiada.'); } catch (err) { toast('Selecione e copie a senha manualmente.'); }
      return;
    }

    if (t.matches('[data-user-new]')) {
      const pw = suggestPassword();
      openModal(
        'Novo usuário',
        'A senha informada é provisória: o usuário define a própria senha no primeiro acesso.',
        field('Nome', 'name', 'text', '', 'required maxlength="80"') +
          field('E-mail', 'email', 'email', '', 'required') +
          checks('Funções', 'roles', roleItems, (r) => r === 'User' || r === 'Colaborador') +
          pwField(pw),
        'Criar usuário',
        async (form) => {
          await api('POST', '/users', {
            name: form.name.value,
            email: form.email.value,
            roles: values(form, 'roles'),
            password: form.password.value,
            must_change_password: true,
          });
          reloadWithToast(`Usuário criado. Informe a senha provisória a ${form.name.value}.`);
        },
      );
      return;
    }

    if (t.matches('[data-user-edit]')) {
      const u = userOf(t.dataset.userEdit);
      if (!u) return;
      openModal(
        'Editar usuário',
        `${u.email} · o e-mail é o login e não pode ser alterado.`,
        field('Nome', 'name', 'text', u.name, 'required maxlength="80"') +
          checks('Funções', 'roles', roleItems, (r) => u.roles.includes(r)),
        'Salvar',
        async (form) => {
          await api('PUT', `/users/${enc(u.id)}`, { name: form.name.value, roles: values(form, 'roles') });
          reloadWithToast('Usuário atualizado.');
        },
      );
      return;
    }

    if (t.matches('[data-user-reset]')) {
      const u = userOf(t.dataset.userReset);
      if (!u) return;
      openModal(
        'Redefinir senha',
        `Defina uma senha provisória para ${u.name}. As sessões abertas serão encerradas e a troca será obrigatória no próximo acesso.`,
        pwField(suggestPassword()),
        'Redefinir',
        async (form) => {
          await api('POST', `/users/${enc(u.id)}/reset-password`, { password: form.password.value });
          reloadWithToast(`Senha redefinida. Informe a senha provisória a ${u.name}.`);
        },
      );
      return;
    }

    if (t.matches('[data-user-unlock]')) {
      try {
        await api('POST', `/users/${enc(t.dataset.userUnlock)}/unlock`, {});
        reloadWithToast('Usuário desbloqueado.');
      } catch (err) { toast(err.message, 'error'); }
      return;
    }

    if (t.matches('[data-user-toggle]')) {
      const active = t.dataset.userActive === 'true';
      const u = userOf(t.dataset.userToggle);
      if (!active && !await window.HubConfirm(`Desativar ${u ? u.name : 'o usuário'}? As sessões abertas serão encerradas.`)) return;
      try {
        await api('PUT', `/users/${enc(t.dataset.userToggle)}`, { active });
        reloadWithToast(active ? 'Usuário reativado.' : 'Usuário desativado.');
      } catch (err) { toast(err.message, 'error'); }
      return;
    }

    // ---- Database access (read accounts) ----------------------------------------
    if (t.matches('[data-dbaccess-new]')) {
      const dbs = (meta.dbs || []).map((d) => ({ value: d, label: d }));
      const people = (meta.users || []).map((u) => `<option value="${esc(u.id)}">${esc(u.name)} (${esc(u.email)})</option>`).join('');
      openModal(
        'Nova conta de leitura',
        '',
        field('Nome (ex.: powerbi_vendas)', 'name', 'text', '', 'required pattern="[a-z][a-z0-9_]{1,40}" autocomplete="off"')
          + `<div class="form-field"><label>Responsável</label><select name="owner" required>${people}</select></div>`
          + (dbs.length ? checks('Bancos das aplicações', 'dbs', dbs, () => false) : '')
          + (meta.hub_db ? `<div class="form-field"><label>Banco do hub</label><label class="role-permission"><input type="checkbox" name="hub" value="1"/><span>Incluir o banco interno do hub (<code>${esc(meta.hub_db)}</code>)</span></label></div>` : '')
          + `<div class="form-field"><label>Validade</label><select name="days"><option value="30">30 dias</option><option value="90" selected>90 dias</option><option value="180">180 dias</option><option value="365">365 dias</option></select></div>`,
        'Criar conta',
        async (form) => {
          const out = await api('POST', '/db-access', { name: form.name.value.trim(), owner: form.owner.value, dbs: values(form, 'dbs'), hub: !!(form.hub && form.hub.checked), days: Number(form.days.value) });
          showCredentials(out);
        },
      );
      return;
    }
    if (t.matches('[data-dbaccess-password]')) {
      if (!await window.HubConfirm(`Gerar nova senha para ${t.dataset.dbaccessPassword}? A senha atual deixa de funcionar.`)) return;
      try { showCredentials(await api('POST', `/db-access/${enc(t.dataset.dbaccessPassword)}/password`, {})); } catch (err) { toast(err.message, 'error'); }
      return;
    }
    if (t.matches('[data-dbaccess-remove]')) {
      if (!await window.HubConfirm(`Remover a conta ${t.dataset.dbaccessRemove}? As conexões abertas caem agora.`)) return;
      try { await api('DELETE', `/db-access/${enc(t.dataset.dbaccessRemove)}`); reloadWithToast('Conta de leitura removida.'); } catch (err) { toast(err.message, 'error'); }
      return;
    }
    if (t.matches('[data-dbaccess-copy]')) {
      try { await navigator.clipboard.writeText($('#dbaccess-password').value); toast('Senha copiada.'); } catch (err) { toast('Selecione e copie a senha manualmente.'); }
      return;
    }

    if (t.matches('[data-user-revoke-access]')) {
      const u = userOf(t.dataset.userRevokeAccess);
      if (!await window.HubConfirm(`Revogar todos os acessos de ${u ? u.name : 'o usuário'}? Sessões, tokens pessoais e apps autorizados (CLI, MCP) param agora; a conta continua ativa.`)) return;
      try {
        const out = await api('POST', `/users/${enc(t.dataset.userRevokeAccess)}/revoke-access`, {});
        reloadWithToast(`Acessos revogados: ${out.tokens} token(s), ${out.apps} app(s).`);
      } catch (err) { toast(err.message, 'error'); }
    }
  });
})();
