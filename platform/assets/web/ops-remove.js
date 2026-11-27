/* ops-remove.js — Applications › Containers: "Remover aplicação" (and "Remover apps de exemplo").
 *
 * A modal where the person types the app's name (or id) — the button stays disabled until it matches —,
 * gives their password (step-up) and picks backup / keep data. The removal runs as a job on the server
 * (POST /api/ops/apps/{id}/remove → GET /api/ops/jobs/{id}); the page reloads with its final message.
 * Values coming from the user/server enter the DOM only through textContent/value/esc(). */
(function () {
  'use strict';

  const norm = (s) => String(s ?? '').trim().split(/\s+/).filter(Boolean).join(' ').toLowerCase();
  /** The typed confirmation matches one of the accepted names (case-insensitive, trimmed). */
  function matches(typed, accepted) {
    const t = norm(typed);
    return t !== '' && accepted.some((a) => norm(a) === t);
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { matches, norm };
  if (typeof document === 'undefined') return;

  const $ = (sel, el = document) => el.querySelector(sel);
  const esc = (s) =>
    String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function call(method, path, body) {
    const init = { method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    const res = await fetch('/api/ops' + path, init);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.error) || `Erro ${res.status} ao falar com o servidor.`);
    return data;
  }

  async function finish(job, status) {
    for (;;) {
      const j = await call('GET', `/jobs/${job.id}`);
      if (j.done) return j;
      status.textContent = j.message || 'Removendo…';
      await sleep(400);
    }
  }

  function form(label, ask) {
    return `<div class="form-field"><label for="ops-rm-confirm">${esc(label)}</label><input id="ops-rm-confirm" data-rm="confirm" autocomplete="off" spellcheck="false" placeholder="${esc(ask)}"/></div>`
      + '<div class="form-field"><label for="ops-rm-password">Sua senha</label><input id="ops-rm-password" data-rm="password" type="password" autocomplete="current-password"/></div>'
      + '<label class="ops-check"><input type="checkbox" data-rm="backup" checked/> Fazer backup do banco antes de apagar</label>'
      + '<label class="ops-check"><input type="checkbox" data-rm="keep"/> Manter os dados (banco, armazenamento e backups); só tirar do hub</label>';
  }

  function open(title, label, ask, accepted, path) {
    const host = document.createElement('div');
    host.className = 'overlay ops-rm-overlay'; // above the details drawer it is opened from
    host.innerHTML = `<section class="modal" role="dialog" aria-modal="true" aria-labelledby="ops-rm-title"><div class="modal-header"><h2 id="ops-rm-title">${esc(title)}</h2><button type="button" class="modal-close" aria-label="Fechar">×</button></div><form novalidate>${form(label, ask)}<p class="ops-modal-error" role="alert" data-rm="status"></p><div class="modal-actions"><button type="button" class="secondary-button" data-rm="cancel">Cancelar</button><button type="submit" class="danger-button" data-rm="submit" disabled>Remover</button></div></form></section>`;
    document.body.appendChild(host);
    const f = (k) => $(`[data-rm="${k}"]`, host);
    const close = () => host.remove();
    $('.modal-close', host).onclick = close;
    f('cancel').onclick = close;
    host.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !f('confirm').value) close(); });
    f('confirm').addEventListener('input', () => { f('submit').disabled = !matches(f('confirm').value, accepted); });
    $('form', host).addEventListener('submit', (e) => { e.preventDefault(); submit(f, path, accepted); });
    f('confirm').focus();
    return host;
  }

  async function submit(f, path, accepted) {
    if (!matches(f('confirm').value, accepted)) return;
    const status = f('status');
    f('submit').disabled = true;
    status.textContent = 'Removendo…';
    try {
      const body = { confirm: f('confirm').value.trim(), password: f('password').value, backup: f('backup').checked, keep_data: f('keep').checked };
      const final = await finish(await call('POST', path, body), status);
      try { sessionStorage.setItem('hub-admin-toast', final.message); } catch (e) { /* no storage */ }
      location.reload();
    } catch (ex) {
      status.textContent = ex.message;
      f('submit').disabled = false;
    }
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-ops-remove],[data-ops-remove-samples]');
    if (!b) return;
    e.preventDefault();
    const d = b.dataset;
    if (d.opsRemove !== undefined) {
      open(`Remover aplicação · ${d.name}`, `Digite ${d.name} para confirmar`, d.name, [d.name, d.opsRemove], `/apps/${encodeURIComponent(d.opsRemove)}/remove`);
      return;
    }
    open('Remover apps de exemplo', `Digite ${d.phrase} para confirmar`, d.phrase, ['apps de exemplo', 'sample apps'], '/apps/remove-samples');
  });
})();
