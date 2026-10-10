/* users.js — Acesso › Usuários e Tokens de API.
 *
 * As páginas chegam prontas do servidor; este script abre os formulários (modal
 * do mockup), chama /api/admin/users* e /api/admin/tokens* (application/json,
 * mesma origem) e recarrega a página após cada mutação. Valores do servidor e do
 * usuário entram no DOM só por textContent/value ou esc(). */
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
    try { sessionStorage.setItem('hub-admin-toast', message); } catch (e) { /* sem storage */ }
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

  // Senha provisória sugerida (o admin pode trocar antes de salvar).
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
      <p class="modal-copy">${esc(copy)}</p>
      ${fieldsHtml}
      <div class="auth-alert hidden" data-modal-error></div>
      <div class="modal-actions"><button type="button" class="secondary-button" data-close>Cancelar</button><button type="submit" class="primary-button">${esc(submitLabel)}</button></div>
    </form>`;
    document.body.appendChild(wrap);
    const form = $('form', wrap);
    // Clicar fora não fecha (o que foi digitado se perderia); Esc só fecha sem alterações.
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
  // Senha provisória com "Gerar outra" e "Copiar".
  const pwField = (pw) =>
    `<div class="form-field"><label for="user-pw">Senha provisória</label><div class="pw-row"><input id="user-pw" name="password" type="text" value="${esc(pw)}" required minlength="10" autocomplete="off"/><button type="button" class="secondary-button" data-pw-gen>Gerar outra</button><button type="button" class="secondary-button" data-pw-copy>Copiar</button></div></div>`;
  const values = (form, name) => $$(`input[name="${name}"]:checked`, form).map((i) => i.value);
  const roleItems = meta.roles.map((r) => ({ value: r, label: r }));

  const userOf = (id) => {
    const row = $(`[data-user-id="${CSS.escape(id)}"]`);
    return row ? JSON.parse(row.dataset.user) : null;
  };

  // Recarrega só a lista (HTML do servidor) e destaca a linha nova.
  async function refreshPanel(sel, id) {
    try {
      const res = await fetch(location.pathname + location.search, { credentials: 'same-origin', headers: { accept: 'text/html' } });
      const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      const fresh = doc.querySelector(sel);
      const cur = $(sel);
      if (!res.ok || !fresh || !cur) return;
      cur.replaceWith(document.importNode(fresh, true));
      if (id) $(`${sel} [data-token-row="${CSS.escape(String(id))}"]`)?.classList.add('row-new');
    } catch (_) { /* a lista atualiza no próximo carregamento */ }
  }

  // ---- Usuários -------------------------------------------------------------
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
          checks('Funções', 'roles', roleItems, (r) => r === 'Colaborador') +
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

    // ---- Tokens de API ------------------------------------------------------
    if (t.matches('[data-token-new]')) {
      const appItems = meta.apps.map((a) => ({ value: a.id, label: a.name }));
      openModal(
        'Novo token de API',
        'Sem aplicações marcadas = todas. O token aparece uma única vez.',
        field('Nome', 'name', 'text', '', 'required maxlength="60" placeholder="CI — Gestão de Ativos"') +
          checks('Aplicações', 'apps', appItems, () => false) +
          checks('Ambientes', 'envs', [{ value: 'staging', label: 'Staging' }, { value: 'producao', label: 'Produção' }], (v) => v === 'staging') +
          checks('Ações', 'actions', [
            { value: 'upload', label: 'Enviar pacotes (CI)' },
            { value: 'status', label: 'Consultar status (CI)' },
            { value: 'api:read', label: 'API v1: leitura (age como você)' },
            { value: 'api:write', label: 'API v1: alterações (age como você)' },
            { value: 'mcp', label: 'MCP: clientes de IA (age como você)' },
            { value: 'app-api', label: 'API dos apps (/{app}/api)' },
          ], (v) => v === 'upload' || v === 'status') +
          field('Validade (dias)', 'days', 'number', '90', 'min="1" max="365" required'),
        'Criar token',
        async (form) => {
          const out = await api('POST', '/tokens', {
            name: form.name.value,
            apps: values(form, 'apps'),
            envs: values(form, 'envs'),
            actions: values(form, 'actions'),
            expires_days: Number(form.days.value) || 90,
          });
          closeModal();
          await refreshPanel('[data-admin-tokens]', out.item && out.item.id);
          const box = $('[data-token-secret]');
          $('[data-token-value]', box).textContent = out.token;
          box.classList.remove('hidden');
          toast('Token criado. Copie agora: ele não será mostrado de novo.');
        },
      );
      return;
    }

    if (t.matches('[data-token-copy]')) {
      const v = $('[data-token-value]').textContent;
      try { await navigator.clipboard.writeText(v); toast('Token copiado.'); } catch (err) { toast('Selecione e copie o token manualmente.'); }
      return;
    }

    if (t.matches('[data-token-revoke]')) {
      if (!await window.HubConfirm('Revogar o token? Pipelines que o usam deixam de conseguir enviar pacotes.')) return;
      try {
        await api('POST', `/tokens/${enc(t.dataset.tokenRevoke)}/revoke`, {});
        reloadWithToast('Token revogado.');
      } catch (err) { toast(err.message, 'error'); }
    }

    if (t.matches('[data-token-delete]')) {
      const live = t.dataset.tokenActive === '1';
      if (!await window.HubConfirm(live ? 'Excluir o token? Ele será revogado e removido da lista; pipelines que o usam deixam de funcionar.' : 'Excluir o token revogado? Ele some da lista.')) return;
      try {
        await api('DELETE', `/tokens/${enc(t.dataset.tokenDelete)}`);
        reloadWithToast('Token excluído.');
      } catch (err) { toast(err.message, 'error'); }
    }
  });
})();
