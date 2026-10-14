/* ops.js — Operations interactions (Deployments, Containers, Backups, SDK).
 *
 * The pages arrive ready from the server; this script only:
 *   - sends the .hubapp (multipart + X-Hub-Request: 1) and follows the pipeline job
 *     (GET /api/ops/jobs/{id}), animating the steps with the real result;
 *   - approves / promotes / rolls back (202 + job: follows the blue/green steps);
 *     starts/restarts/stops containers (production or staging); edits limits;
 *   - triggers backup, restore, clone and restore test with a progress bar;
 *   - edits backup policies and validates the manifest.
 * After each mutation the page is reloaded (the truth stays on the server).
 * Values coming from the user/server enter the DOM only through textContent/value/esc(). */
(function () {
  'use strict';

  const API = '/api/ops';
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
  const esc = (s) =>
    String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

  async function call(method, path, body, opts = {}) {
    const init = { method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (opts.form) {
      init.body = opts.form;
      init.headers['X-Hub-Request'] = '1';
    } else if (method !== 'GET') {
      init.headers['Content-Type'] = 'application/json';
      init.body = typeof body === 'string' ? body : JSON.stringify(body ?? {});
    }
    const res = await fetch(API + path, init);
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

  const envQ = (env) => (env === 'staging' ? '?env=staging' : '');

  /** Approve/promote/rollback: 202 + job; shows each blue/green step. */
  async function deploymentAction(id, action, button) {
    if (button) button.disabled = true;
    const job = await call('POST', `/deployments/${id}/${action}`);
    toast(job.message || 'Na fila…');
    const final = await pollJob(job.id, (j) => {
      const note = button && button.closest('tr') && button.closest('tr').querySelector('[data-ops-live]');
      if (note) { note.hidden = false; note.textContent = j.message; }
      toast(j.message);
    });
    return reloadWithToast(final.message);
  }

  async function pollJob(id, onUpdate) {
    for (;;) {
      const job = await call('GET', `/jobs/${id}`);
      onUpdate(job);
      if (job.done) return job;
      await sleep(250);
    }
  }

  // ---- modal simples ---------------------------------------------------------
  function modal(title, fieldsHtml, onSubmit, submitLabel = 'Salvar alterações') {
    const host = document.createElement('div');
    host.className = 'overlay';
    host.innerHTML = `<section class="modal" role="dialog" aria-modal="true"><div class="modal-header"><h2>${esc(title)}</h2><button type="button" class="modal-close" aria-label="Fechar">×</button></div><form novalidate>${fieldsHtml}<p class="ops-modal-error" role="alert"></p><div class="modal-actions"><button type="button" class="secondary-button" data-cancel>Cancelar</button><button type="submit" class="primary-button">${esc(submitLabel)}</button></div></form></section>`;
    document.body.appendChild(host);
    const close = () => host.remove();
    $('.modal-close', host).onclick = close;
    $('[data-cancel]', host).onclick = close;
    // Clicking outside does not close (what was typed would be lost); Esc only closes without changes.
    host.addEventListener('input', () => { host.dataset.dirty = '1'; }); host.addEventListener('change', () => { host.dataset.dirty = '1'; });
    host.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !host.dataset.dirty) { e.preventDefault(); close(); } });
    const form = $('form', host);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = $('.ops-modal-error', host);
      err.textContent = '';
      try { await onSubmit(form); } catch (ex) { err.textContent = ex.message; }
    });
    const first = $('input,select,textarea', host);
    if (first) first.focus();
    return host;
  }
  const field = (name, label, value, attrs = '') =>
    `<div class="form-field"><label for="ops-f-${name}">${esc(label)}</label><input id="ops-f-${name}" name="${name}" value="${esc(value)}" ${attrs}/></div>`;

  // ---- Deployments ----------------------------------------------------------
  let selectedFile = null;
  function pickFile(file) {
    selectedFile = file || null;
    const label = $('[data-ops-file-name]');
    if (!label) return;
    label.textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1).replace('.', ',')} KB` : 'Arraste um .hubapp ou clique para selecionar';
    const drop = $('[data-ops-drop]');
    if (drop) drop.classList.toggle('has-file', !!file);
  }
  document.addEventListener('change', (e) => {
    const input = e.target.closest('[data-ops-file]');
    if (input) pickFile(input.files[0]);
  });
  const drop = $('[data-ops-drop]');
  if (drop) {
    ['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('has-file'); }));
    drop.addEventListener('dragleave', () => drop.classList.toggle('has-file', !!selectedFile));
    drop.addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]); });
  }

  const STEP_CLASS = { pending: '', running: 'running', ok: 'done', warn: 'warn', failed: 'failed', skipped: 'skipped' };
  function paintSteps(steps) {
    const items = $$('[data-ops-steps] > li');
    steps.forEach((s, i) => {
      const li = items[i];
      if (!li) return;
      li.className = STEP_CLASS[s.state] || '';
      $('[data-step-detail]', li).textContent = s.detail || '';
    });
  }

  async function runImport(button) {
    if (!selectedFile) { toast('Selecione um arquivo .hubapp.'); return; }
    const status = $('[data-ops-pipeline-status]');
    const env = $('[data-ops-env]').value;
    const form = new FormData();
    form.append('package', selectedFile, selectedFile.name);
    form.append('env', env);
    button.disabled = true;
    status.textContent = 'Validando ' + selectedFile.name;
    paintSteps($$('[data-ops-steps] > li').map(() => ({ state: 'pending', detail: '' })));
    try {
      const job = await call('POST', '/packages', null, { form });
      const final = await pollJob(job.id, (j) => paintSteps(j.steps));
      const dep = final.result && final.result.deployment;
      status.textContent = final.ok ? (dep ? dep.status : 'Concluído') : 'Rejeitado';
      toast(final.message);
      setTimeout(() => reloadWithToast(final.message), final.ok ? 1400 : 2600);
    } catch (ex) {
      status.textContent = 'Falha no envio';
      toast(ex.message, 'error');
      button.disabled = false;
    }
  }

  // ---- Backups: progresso -------------------------------------------------------
  async function runWithProgress(path, title) {
    const box = $('[data-ops-progress]');
    const bar = $('[data-ops-progress-bar]');
    const msg = $('[data-ops-progress-msg]');
    if (box) { box.hidden = false; $('[data-ops-progress-title]').textContent = title; }
    const job = await call('POST', path);
    const final = await pollJob(job.id, (j) => {
      if (bar) bar.style.width = `${j.progress}%`;
      if (msg) msg.textContent = j.message;
    });
    return final;
  }

  // ---- Backups: databases on the side (interrupted restores) -------------------------
  // Only lists and offers explicit actions (return to the name / delete): nothing is deleted on its own.
  function asideRow(a) {
    const back = a.db_exists ? '' : `<button type="button" class="secondary-button" data-ops-aside="${esc(a.aside)}" data-db="${esc(a.db)}" data-action="rename-back">Devolver ao nome</button> `;
    const drop = `<button type="button" class="secondary-button" data-ops-aside="${esc(a.aside)}" data-db="${esc(a.db)}" data-action="drop">Apagar</button>`;
    return `<tr data-ops-aside-row="${esc(a.aside)}"><td>${esc(a.app)}${a.env === 'staging' ? ' (staging)' : ''}</td><td><code>${esc(a.aside)}</code></td><td><code>${esc(a.db)}</code> ${a.db_exists ? 'existe' : '<strong>não existe</strong>'}</td><td class="ops-actions">${back}${drop}</td></tr>`;
  }

  async function checkAside(button) {
    const box = $('[data-ops-aside-list]');
    if (!box) return;
    if (button) button.disabled = true;
    box.textContent = 'Verificando…';
    try {
      const r = await call('GET', '/backups/aside');
      if (!r.available) {
        box.innerHTML = '<p class="ops-muted">Backups de banco não configurados neste hub (HUB_BACKUP_DATA_MOUNT/HUB_DB_ADMIN_URL).</p>';
      } else if (!r.aside.length) {
        box.innerHTML = '<p class="ops-muted" data-ops-aside-none="">Nenhum banco de lado encontrado.</p>';
      } else {
        box.innerHTML = `<table class="data-table"><thead><tr><th>Aplicação</th><th>Banco de lado</th><th>Banco atual</th><th></th></tr></thead><tbody>${r.aside.map(asideRow).join('')}</tbody></table>`;
      }
    } catch (err) {
      box.textContent = err.message;
    } finally {
      if (button) button.disabled = false;
    }
  }

  // Deployments drawer: the version selector shows the panel (facts and actions) of the chosen version.
  document.addEventListener('change', (e) => {
    const s = e.target.closest && e.target.closest('[data-ops-ver]');
    if (!s) return;
    const box = s.closest('.row-detail-body') || document;
    box.querySelectorAll('[data-ops-ver-panel]').forEach((p) => { p.hidden = p.dataset.opsVerPanel !== s.value; });
  });

  // ---- cliques ---------------------------------------------------------------
  document.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-ops-import],[data-ops-approve-scripts],[data-ops-approve],[data-ops-promote],[data-ops-rollback],[data-ops-container],[data-ops-limits],[data-ops-backup],[data-ops-restore],[data-ops-policy],[data-ops-validate],[data-ops-apply],[data-ops-provision],[data-ops-rotate],[data-ops-task],[data-ops-maint-clear],[data-ops-aside-check],[data-ops-aside],[data-ops-backup-creds]');
    if (!t) return;
    e.preventDefault();
    const d = t.dataset;
    try {
      if (d.opsImport !== undefined) return await runImport(t);
      if (d.opsAsideCheck !== undefined) return await checkAside(t);
      if (d.opsBackupCreds !== undefined) {
        if (!await window.HubConfirm('Rotacionar as credenciais só leitura dos backups? Senhas/chaves novas vão para o cofre e as antigas deixam de valer na hora.')) return;
        t.disabled = true;
        const box = $('[data-ops-backup-creds-result]');
        try {
          const r = await call('POST', '/backups/credentials/rotate');
          if (box) {
            box.textContent = '';
            const ul = document.createElement('ul');
            for (const line of r.items || []) {
              const li = document.createElement('li');
              li.textContent = line;
              ul.append(li);
            }
            box.append(ul);
          }
          toast(r.failed ? `${r.rotated} rotacionada(s), ${r.failed} com falha.` : `${r.rotated} credencial(is) de backup rotacionada(s).`);
        } finally {
          t.disabled = false;
        }
        return;
      }
      if (d.opsAside !== undefined) {
        const rename = d.action === 'rename-back';
        const q = rename
          ? `Devolver o banco ${d.opsAside} ao nome ${d.db}? Use só se o banco ${d.db} não existe mais (os dados voltam como estavam antes da restauração).`
          : `APAGAR o banco de lado ${d.opsAside}? Os dados dele serão perdidos — confira antes que o banco ${d.db} atual está correto.`;
        if (!await window.HubConfirm(q)) return;
        t.disabled = true;
        const r = await call('POST', `/backups/aside/${encodeURIComponent(d.opsAside)}/${d.action}`);
        toast(r.detail || 'Feito.');
        return await checkAside($('[data-ops-aside-check]'));
      }
      if (d.opsMaintClear !== undefined) {
        if (!await window.HubConfirm('Liberar o app da manutenção? Confira antes os dados (restaure o snapshot "Pré-restauração" se preciso).')) return;
        t.disabled = true;
        await call('POST', `/maintenance/${encodeURIComponent(d.opsMaintClear)}/clear${envQ(d.env)}`);
        return reloadWithToast('Manutenção liberada.');
      }
      if (d.opsApply !== undefined) {
        t.disabled = true;
        toast('Recriando o container com as variáveis atuais…');
        const r = await call('POST', `/runtime/${encodeURIComponent(d.opsApply)}/apply`);
        return reloadWithToast(r.detail || 'Container reiniciado.');
      }
      if (d.opsProvision !== undefined) {
        t.disabled = true;
        const r = await call('POST', `/provision/${encodeURIComponent(d.opsProvision)}/database${envQ(d.env)}`);
        const p = r.provisioned;
        const msg = p.changed
          ? `Banco ${p.database} provisionado; ${p.variable} vinculada ao app.${r.restart_pending ? ' Reinício pendente: use Aplicar (reiniciar).' : ''}`
          : `Banco ${p.database} já provisionado e acessível.`;
        return reloadWithToast(msg);
      }
      if (d.opsRotate !== undefined) {
        const [app, what] = d.opsRotate.split('|');
        if (!await window.HubConfirm(what === 'token' ? 'Gerar um token interno novo? O app só o recebe ao Aplicar (reiniciar).' : 'Gerar uma senha nova para o banco? Novas conexões do app falham até Aplicar (reiniciar).')) return;
        t.disabled = true;
        const r = await call('POST', `/rotate/${encodeURIComponent(app)}/${what}`);
        return reloadWithToast(`${r.variable} rotacionada. Reinício pendente: use Aplicar (reiniciar).`);
      }
      if (d.opsTask !== undefined) {
        t.disabled = true;
        const [app, id, action, env] = d.opsTask.split('|');
        await call('POST', `/tasks/${encodeURIComponent(app)}/${encodeURIComponent(id)}/${action}${envQ(env)}`);
        return reloadWithToast(action === 'retry' ? 'Tarefa devolvida à fila.' : 'Cancelamento solicitado.');
      }
      if (d.opsApproveScripts !== undefined) {
        // Wave 12 (2.1): security review of the package's own JavaScript (separate and audited).
        if (!await window.HubConfirm('Aprovar o JavaScript próprio deste pacote? Você revisou os arquivos e os motivos listados; os hashes passam a valer na CSP do app.')) return;
        t.disabled = true;
        await call('POST', `/deployments/${d.opsApproveScripts}/scripts/approve`);
        return reloadWithToast('JavaScript próprio aprovado (registrado na auditoria).');
      }
      if (d.opsApprove !== undefined) return await deploymentAction(d.opsApprove, 'approve', t);
      if (d.opsPromote !== undefined) return await deploymentAction(d.opsPromote, 'promote', t);
      if (d.opsRollback !== undefined) {
        if (!await window.HubConfirm('Reverter produção para a versão anterior?')) return;
        return await deploymentAction(d.opsRollback, 'rollback', t);
      }
      if (d.opsContainer !== undefined) {
        const labels = { start: 'iniciado', restart: 'reiniciado', stop: 'parado' };
        t.disabled = true;
        const r = await call('POST', `/runtime/${encodeURIComponent(d.app)}/${d.opsContainer}${envQ(d.env)}`);
        return reloadWithToast(`Container ${r.container} ${labels[d.opsContainer]}.`);
      }
      if (d.opsLimits !== undefined) {
        const it = JSON.parse(d.opsLimits);
        return modal(`Limites · ${it.name}`,
          `<p class="modal-copy">Limitados pela política da plataforma e pela capacidade do host (admission control).</p>${field('cpu', 'CPU (vCPU)', it.cpu, 'type="number" step="0.05" min="0.05" max="4"')}${field('memory', 'Memória (ex.: 512Mi, 1Gi)', it.memory)}${field('pids', 'Processos (PIDs)', it.pids, 'type="number" min="1" max="1024"')}`,
          async (form) => {
            const body = { cpu: parseFloat(form.cpu.value), memory: form.memory.value.trim(), pids: parseInt(form.pids.value, 10) };
            const r = await call('PUT', `/runtime/${encodeURIComponent(it.app)}/limits`, body);
            reloadWithToast(`Limites de ${it.name} salvos — ${r.applied}.`);
          });
      }
      if (d.opsPolicy !== undefined) {
        const p = JSON.parse(d.opsPolicy);
        return modal('Política de backup',
          `${field('schedule', 'Agenda (cron de 5 campos, ex.: 0 2 * * *, ou "Diário 02:00", "A cada 6 h", "Manual")', p.schedule)}${field('keep', 'Retenção (manter últimos N backups)', p.keep, 'type="number" min="1" max="90"')}${field('volumes', 'Volumes (separados por vírgula)', p.volumes.join(', '))}${field('destination', 'Destino', p.destination)}${field('envs', 'Ambientes (producao, staging)', (p.envs || ['producao']).join(', '))}${field('jitter_s', 'Atraso aleatório máximo (s)', p.jitter_s ?? 20, 'type="number" min="0" max="3600"')}${field('tz', 'Fuso (vazio = padrão da plataforma)', p.tz || '')}<label class="ops-check"><input type="checkbox" name="missed" ${(p.missed || 'executar') === 'executar' ? 'checked' : ''}/> Executar uma vez ao voltar se o horário foi perdido</label><label class="ops-check"><input type="checkbox" name="hooks" ${p.hooks ? 'checked' : ''}/> Hooks pre/post de consistência</label>`,
          async (form) => {
            const body = {
              schedule: form.schedule.value, keep: parseInt(form.keep.value, 10),
              volumes: form.volumes.value.split(',').map((v) => v.trim()).filter(Boolean),
              destination: form.destination.value, hooks: form.hooks.checked,
              envs: form.envs.value.split(',').map((v) => v.trim()).filter(Boolean),
              jitter_s: parseInt(form.jitter_s.value, 10) || 0, tz: form.tz.value.trim(),
              missed: form.missed.checked ? 'executar' : 'pular',
            };
            await call('PUT', `/backups/policies/${encodeURIComponent(p.app)}`, body);
            reloadWithToast('Política de backup salva e registrada na auditoria.');
          });
      }
      if (d.opsBackup !== undefined) {
        t.disabled = true;
        toast('Backup iniciado.');
        const final = await runWithProgress(`/backups/${encodeURIComponent(d.opsBackup)}/run${envQ(d.env)}`, d.env === 'staging' ? 'Backup sob demanda (staging)' : 'Backup sob demanda');
        return reloadWithToast(final.message);
      }
      if (d.opsRestore !== undefined) {
        const q = { restore: 'Restaurar este backup sobre os dados atuais da aplicação?', clone: 'Criar/atualizar a cópia de staging a partir deste backup?', test: null };
        if (q[d.opsRestore] && !await window.HubConfirm(q[d.opsRestore])) return;
        const titles = { restore: 'Restauração', clone: 'Clone para staging', test: 'Teste de restore' };
        $$('[data-ops-restore]').forEach((b) => { b.disabled = true; });
        const final = await runWithProgress(`/backups/records/${d.id}/${d.opsRestore}`, titles[d.opsRestore]);
        return reloadWithToast(final.message);
      }
      if (d.opsValidate !== undefined) {
        const out = $('[data-ops-manifest-result]');
        const r = await call('POST', '/manifest/validate', $('[data-ops-manifest]').value);
        out.className = 'ops-validate-result ' + (r.valid ? 'ok' : 'err');
        out.textContent = r.valid
          ? `Manifest válido: ${r.manifest.id} ${r.manifest.version} · ${r.manifest.routes.length} rota(s) · ${r.manifest.resources.cpu} vCPU, ${r.manifest.resources.memory}${r.registered ? ' · atualiza app registrado' : ' · app novo'}`
          : `Inválido: ${r.error}`;
        return;
      }
    } catch (ex) {
      toast(ex.message, 'error');
      $$('button[disabled]').forEach((b) => { b.disabled = false; });
      const out = d.opsValidate !== undefined && $('[data-ops-manifest-result]');
      if (out) { out.className = 'ops-validate-result err'; out.textContent = ex.message; }
    }
  });
})();
