/* insights.js — wave 8 interactions in the hub shell:
 *   - Observability: automatic refresh (15 s);
 *   - Audit: "Check integrity";
 *   - Settings: rotation of the vault's data key;
 *   - Health: define/edit/remove SLO;
 *   - Status: record incident, note and resolution;
 *   - Feature flags: create, turn on/off per environment (kill switch), rules and delete.
 * Everything talks to /api/admin/* (JSON) and reloads the page after each mutation.
 * Values enter the DOM only through textContent/value. */
(function () {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  function toast(msg, kind) {
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle('toast-error', kind === 'error');
    el.classList.toggle('toast-success', kind === 'success');
    el.classList.add('show');
    setTimeout(() => el.classList.remove('show'), 2600);
  }
  function reloadWithToast(msg) {
    try { sessionStorage.setItem('hub-admin-toast', msg); } catch (e) { /* ignore */ }
    location.reload();
  }
  async function api(method, path, body) {
    const res = await fetch('/api/admin' + path, {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify(body === undefined ? {} : body),
      credentials: 'same-origin',
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.error) || `Erro ${res.status}.`);
    return data;
  }
  const meta = (sel, attr) => {
    try { return JSON.parse($(sel)?.getAttribute(attr) || '{}'); } catch (e) { return {}; }
  };

  // ---- generic modal ---------------------------------------------------------
  // fields: [{name, label, type: text|number|select|textarea|checkbox|checks, options, value}]
  function modal(title, fields, onSubmit, extra) {
    const ov = document.createElement('div');
    ov.className = 'overlay';
    ov.setAttribute('data-insights-modal', '');
    const box = document.createElement('form');
    box.className = 'modal';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    const head = document.createElement('div');
    head.className = 'modal-header';
    const h = document.createElement('h2');
    h.textContent = title;
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'modal-close';
    close.setAttribute('aria-label', 'Fechar');
    close.textContent = '×';
    close.onclick = () => ov.remove();
    head.append(h, close);
    box.append(head);
    for (const f of fields) {
      const wrap = document.createElement('div');
      wrap.className = 'form-field';
      const id = 'ins-' + f.name;
      const label = document.createElement('label');
      label.htmlFor = id;
      label.textContent = f.label;
      let input;
      if (f.type === 'select') {
        input = document.createElement('select');
        for (const o of f.options) {
          const op = document.createElement('option');
          op.value = typeof o === 'string' ? o : o.value;
          op.textContent = typeof o === 'string' ? o : o.label;
          input.append(op);
        }
        if (f.value !== undefined) input.value = f.value;
      } else if (f.type === 'textarea') {
        input = document.createElement('textarea');
        input.value = f.value ?? '';
      } else if (f.type === 'checks') {
        input = document.createElement('div');
        input.className = 'modal-app-picker';
        for (const o of f.options) {
          const l = document.createElement('label');
          const c = document.createElement('input');
          c.type = 'checkbox';
          c.value = o;
          c.name = f.name;
          c.checked = (f.value || []).includes(o);
          l.append(c, document.createTextNode(' ' + o));
          input.append(l);
        }
      } else {
        input = document.createElement('input');
        input.type = f.type || 'text';
        if (f.type === 'checkbox') { input.checked = !!f.value; input.style.width = 'auto'; } else input.value = f.value ?? '';
        if (f.min !== undefined) input.min = f.min;
        if (f.max !== undefined) input.max = f.max;
        if (f.step !== undefined) input.step = f.step;
      }
      if (f.type !== 'checks') { input.id = id; input.name = f.name; }
      wrap.append(label, input);
      if (f.hint) { const s = document.createElement('small'); s.className = 'obs-muted'; s.textContent = f.hint; wrap.append(s); }
      box.append(wrap);
      f.wrap = wrap;
    }
    // Conditional fields (`showIf: {name, value}`): only appear when they make sense.
    for (const f of fields.filter((x) => x.showIf)) {
      const ctl = box.querySelector(`[name="${f.showIf.name}"]`);
      const sync = () => { f.wrap.hidden = !ctl || ctl.value !== f.showIf.value; };
      ctl?.addEventListener('change', sync);
      sync();
    }
    const actions = document.createElement('div');
    actions.className = 'modal-actions';
    if (extra) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'secondary-button';
      b.textContent = extra.label;
      b.onclick = async () => { try { await extra.run(); } catch (e) { toast(e.message, 'error'); } };
      actions.append(b);
    }
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'secondary-button';
    cancel.textContent = 'Cancelar';
    cancel.onclick = () => ov.remove();
    const ok = document.createElement('button');
    ok.type = 'submit';
    ok.className = 'primary-button';
    ok.textContent = 'Salvar';
    actions.append(cancel, ok);
    box.append(actions);
    box.addEventListener('submit', async (e) => {
      e.preventDefault();
      const v = {};
      for (const f of fields) {
        if (f.type === 'checks') v[f.name] = $$(`input[name="${f.name}"]:checked`, box).map((c) => c.value);
        else if (f.type === 'checkbox') v[f.name] = box.elements[f.name].checked;
        else v[f.name] = box.elements[f.name].value;
      }
      ok.disabled = true;
      try { await onSubmit(v); } catch (err) { toast(err.message, 'error'); ok.disabled = false; }
    });
    ov.append(box);
    // Clicking outside does not close (what was typed would be lost); Esc only closes without changes.
    ov.addEventListener('input', () => { ov.dataset.dirty = '1'; }); ov.addEventListener('change', () => { ov.dataset.dirty = '1'; });
    ov.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !ov.dataset.dirty) { e.preventDefault(); ov.remove(); } });
    document.body.append(ov);
    box.querySelector('input,select,textarea')?.focus();
  }

  // ---- Observability: automatic refresh --------------------------------------
  const auto = $('[data-obs-autorefresh]');
  if (auto) {
    try { if (localStorage.getItem('hub-obs-auto') === '0') auto.checked = false; } catch (e) { /* ignore */ }
    auto.addEventListener('change', () => { try { localStorage.setItem('hub-obs-auto', auto.checked ? '1' : '0'); } catch (e) { /* ignore */ } });
    setInterval(() => { if (auto.checked && !document.hidden && !$('[data-insights-modal]')) location.reload(); }, 15000);
  }

  // ---- Audit: check integrity ----------------------------------------
  const verify = $('[data-audit-verify]');
  if (verify) {
    verify.addEventListener('click', async () => {
      const out = $('[data-audit-verify-result]');
      verify.disabled = true;
      try {
        const r = await api('POST', '/audit/verify');
        out.classList.remove('hidden');
        out.classList.toggle('audit-ok', r.ok);
        out.classList.toggle('audit-bad', !r.ok);
        out.textContent = '';
        const t = document.createElement('strong');
        t.textContent = r.ok
          ? `Íntegra: ${r.entries} eventos, ${r.checkpoints} checkpoints assinados, ${r.segments} segmento(s)${r.from_seq ? ` (verificada a partir do piso de retenção assinado, seq ${r.from_seq})` : ''}.`
          : `Problemas encontrados (${r.problems.length}):`;
        out.append(t);
        if (r.migrated) {
          const m = document.createElement('p');
          m.className = 'audit-migrated';
          m.textContent = `Atenção: ${r.migrated} evento(s) entraram na cadeia por migração (trilha anterior ao cofre, sem prova de integridade anterior à migração).`;
          out.append(m);
        }
        if (!r.ok) {
          const ul = document.createElement('ul');
          for (const p of r.problems) { const li = document.createElement('li'); li.textContent = p; ul.append(li); }
          out.append(ul);
        }
      } catch (e) { toast(e.message, 'error'); }
      verify.disabled = false;
    });
  }

  // ---- Cofre -------------------------------------------------------------------
  $('[data-vault-rotate]')?.addEventListener('click', async () => {
    if (!await window.HubConfirm('Criar uma chave de dados nova e recifrar os segredos? As chaves antigas continuam abrindo os backups existentes.')) return;
    try {
      const r = await api('POST', '/vault/rotate-data-key');
      reloadWithToast(`Chave de dados rotacionada (${r.active_data_key}).`);
    } catch (e) { toast(e.message, 'error'); }
  });

  $('[data-analytics-rotate]')?.addEventListener('click', async () => {
    if (!await window.HubConfirm('Trocar a chave dos ids do analytics? Os ids antigos deixam de se ligar aos novos.')) return;
    try { await api('POST', '/analytics/rotate-key'); toast('Chave dos ids trocada.'); } catch (e) { toast(e.message, 'error'); }
  });

  // ---- SLOs ----------------------------------------------------------------------
  const sloMeta = meta('#slo-meta', 'data-slo-meta');
  $$('[data-slo-edit]').forEach((b) => b.addEventListener('click', () => {
    let item = null;
    try { item = b.getAttribute('data-slo-edit') ? JSON.parse(b.getAttribute('data-slo-edit')) : null; } catch (e) { item = null; }
    const apps = (sloMeta.apps || []).map((a) => ({ value: a.id, label: a.short }));
    modal(item ? 'Editar SLO' : 'Definir SLO', [
      { name: 'app', label: 'Aplicação', type: 'select', options: apps, value: item?.app },
      { name: 'env', label: 'Ambiente', type: 'select', options: [{ value: 'producao', label: 'Produção' }, { value: 'staging', label: 'Staging' }], value: item?.env },
      { name: 'availability', label: 'Disponibilidade alvo (%)', type: 'number', step: '0.01', min: 50, max: 99.999, value: item?.availability ?? 99.5, hint: 'Respostas sem erro 5xx ÷ requisições.' },
      { name: 'latency_ms', label: 'Latência alvo (ms)', type: 'number', min: 1, value: item?.latency_ms ?? 500 },
      { name: 'latency_target', label: 'Requisições dentro da latência (%)', type: 'number', step: '0.1', min: 50, max: 99.999, value: item?.latency_target ?? 95 },
      { name: 'window_days', label: 'Janela', type: 'select', options: [{ value: '30', label: '30 dias' }, { value: '7', label: '7 dias' }], value: String(item?.window_days ?? 30) },
    ], async (v) => {
      await api('PUT', '/slos', { app: v.app, env: v.env, availability: Number(v.availability), latency_ms: Number(v.latency_ms), latency_target: Number(v.latency_target), window_days: Number(v.window_days) });
      reloadWithToast('SLO salvo.');
    }, item ? { label: 'Remover SLO', run: async () => { await api('DELETE', `/slos/${encodeURIComponent(item.app)}/${encodeURIComponent(item.env)}`); reloadWithToast('SLO removido.'); } } : null);
  }));

  // ---- Incidentes -------------------------------------------------------------------
  const incMeta = meta('#incident-meta', 'data-incident-meta');
  $('[data-incident-new]')?.addEventListener('click', () => {
    const apps = [{ value: 'platform', label: 'Plataforma' }, ...(incMeta.apps || []).map((a) => ({ value: a.id, label: a.short }))];
    modal('Registrar incidente', [
      { name: 'app', label: 'Aplicação', type: 'select', options: apps },
      { name: 'env', label: 'Ambiente', type: 'select', options: [{ value: 'producao', label: 'Produção' }, { value: 'staging', label: 'Staging' }] },
      { name: 'title', label: 'Título', value: '' },
      { name: 'severity', label: 'Severidade', type: 'select', options: [{ value: 'minor', label: 'Degradado' }, { value: 'major', label: 'Indisponível' }, { value: 'info', label: 'Informativo' }] },
      { name: 'note', label: 'Nota', type: 'textarea' },
    ], async (v) => { await api('POST', '/incidents', v); reloadWithToast('Incidente registrado.'); });
  });
  $$('[data-incident-note]').forEach((b) => b.addEventListener('click', () => {
    modal('Adicionar nota', [{ name: 'note', label: 'Nota', type: 'textarea' }], async (v) => {
      await api('POST', `/incidents/${b.getAttribute('data-incident-note')}`, { note: v.note, resolve: false });
      reloadWithToast('Nota adicionada.');
    });
  }));
  $$('[data-incident-resolve]').forEach((b) => b.addEventListener('click', () => {
    modal('Resolver incidente', [{ name: 'note', label: 'Nota de encerramento (opcional)', type: 'textarea' }], async (v) => {
      await api('POST', `/incidents/${b.getAttribute('data-incident-resolve')}`, { note: v.note, resolve: true });
      reloadWithToast('Incidente resolvido.');
    });
  }));

  // ---- Feature flags -----------------------------------------------------------------
  const fm = meta('#flags-meta', 'data-flags-meta');
  const flagOf = (el) => { try { return JSON.parse(el.closest('[data-item]').getAttribute('data-item')); } catch (e) { return null; } };
  const valuesOf = (f) => (f.kind === 'variant' ? f.variants : ['off', 'on']);
  const path = (f) => `/flags/${encodeURIComponent(f.app)}/${encodeURIComponent(f.key)}`;
  $('[data-flag-new]')?.addEventListener('click', () => {
    modal('Nova feature flag', [
      { name: 'key', label: 'Chave', hint: 'Letras minúsculas, números, ".", "_" ou "-" (ex.: novo-resumo).' },
      { name: 'app', label: 'Aplicação', type: 'select', options: (fm.apps || []).map((a) => ({ value: a.id, label: a.short })) },
      { name: 'kind', label: 'Tipo', type: 'select', options: [{ value: 'boolean', label: 'Liga/desliga' }, { value: 'variant', label: 'Variantes' }] },
      { name: 'variants', label: 'Variantes (a primeira é a "desligada")', value: '', hint: 'Ex.: controle, azul, verde', showIf: { name: 'kind', value: 'variant' } },
      { name: 'description', label: 'Descrição', value: '' },
    ], async (v) => {
      const variants = v.kind === 'variant' ? v.variants.split(',').map((s) => s.trim()).filter(Boolean) : [];
      const off = v.kind === 'variant' ? (variants[0] || '') : 'off';
      const rule = { enabled: false, default: off, rules: [] };
      await api('POST', '/flags', { key: v.key.trim(), app: v.app, kind: v.kind, variants, description: v.description, envs: { staging: rule, producao: rule } });
      reloadWithToast('Flag criada (desligada nos dois ambientes).');
    });
  });
  $$('[data-flag-toggle]').forEach((b) => b.addEventListener('click', async () => {
    const f = flagOf(b);
    const on = b.getAttribute('data-on') !== '1';
    try {
      await api('POST', `${path(f)}/${b.getAttribute('data-flag-toggle')}/enabled`, { enabled: on });
      reloadWithToast(on ? 'Flag ligada.' : 'Flag desligada (kill switch).');
    } catch (e) { toast(e.message, 'error'); }
  }));
  $$('[data-flag-rules]').forEach((b) => b.addEventListener('click', () => {
    const f = flagOf(b);
    const env = b.getAttribute('data-flag-rules');
    const r = (f.envs && f.envs[env]) || { enabled: false, default: valuesOf(f)[0], rules: [] };
    const first = r.rules[0] || { roles: [], users: [], serve: valuesOf(f)[valuesOf(f).length - 1] };
    const vals = valuesOf(f);
    modal(`Regras de ${f.key} · ${env === 'producao' ? 'Produção' : 'Staging'}`, [
      { name: 'enabled', label: 'Ligada neste ambiente (desmarcar = kill switch)', type: 'checkbox', value: r.enabled },
      { name: 'default', label: 'Valor padrão', type: 'select', options: vals, value: r.default },
      { name: 'roles', label: 'Funções que recebem o valor abaixo', type: 'checks', options: fm.roles || [], value: first.roles },
      { name: 'users', label: 'Usuários (e-mails, separados por vírgula)', value: (first.users || []).join(', ') },
      { name: 'serve', label: 'Valor para essas funções/usuários', type: 'select', options: vals, value: first.serve },
      { name: 'percent', label: 'Percentual dos demais usuários (0–100)', type: 'number', min: 0, max: 100, value: r.rollout ? r.rollout.percent : 0 },
      { name: 'rollout_serve', label: 'Valor para esse percentual', type: 'select', options: vals, value: r.rollout ? r.rollout.serve : vals[vals.length - 1] },
    ], async (v) => {
      const users = v.users.split(',').map((s) => s.trim()).filter(Boolean);
      const rules = v.roles.length || users.length ? [{ roles: v.roles, users, serve: v.serve }] : [];
      const pct = Math.max(0, Math.min(100, parseInt(v.percent || '0', 10) || 0));
      const envs = Object.assign({}, f.envs);
      envs[env] = { enabled: v.enabled, default: v.default, rules, rollout: pct > 0 ? { percent: pct, serve: v.rollout_serve } : undefined };
      await api('PUT', path(f), Object.assign({}, f, { envs }));
      reloadWithToast('Regras salvas.');
    });
  }));
  $$('[data-flag-delete]').forEach((b) => b.addEventListener('click', async () => {
    const f = flagOf(b);
    if (!await window.HubConfirm(`Excluir a flag ${f.key}? O app passa a receber o valor "desligado".`)) return;
    try { await api('DELETE', path(f)); reloadWithToast('Flag excluída.'); } catch (e) { toast(e.message, 'error'); }
  }));
})();
