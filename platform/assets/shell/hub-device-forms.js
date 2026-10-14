/* hub-device-forms.js — keyboard-wedge reader, offline submission queue (IndexedDB) and photo
 * capture. Third part of hub-device.js (split only for size), loaded right after
 * hub-device-camera.js. */
(function () {
  'use strict';
  const P = window.__hubDeviceParts;
  if (!P) return;
  const { Pure, app, fragPrefix, inScope, appRoot, t, fmt, toast, ref, ownPath, uuid, feedback,
    openCamera, cameraError, stopStream, cameraLayer } = P;
  // ---- leitor tipo teclado (wedge) ---------------------------------------------------------
  const keyTimes = new WeakMap();
  const lastWedge = new WeakMap();
  let wedgeSeen = false;
  try { wedgeSeen = sessionStorage.getItem('hub-wedge-seen') === '1'; } catch (_) { /* private */ }
  const keepFocusWanted = () => wedgeSeen || (window.matchMedia && matchMedia('(pointer: fine)').matches);
  function onWedgeKey(e) {
    const el = e.target;
    if (!el || !el.matches || !el.matches('input[data-hub-wedge], textarea[data-hub-wedge]') || !inScope(el)) return;
    const now = e.timeStamp || performance.now();
    if (e.key && e.key.length === 1) {
      const arr = keyTimes.get(el) || [];
      if (arr.length && now - arr[arr.length - 1] > 300) arr.length = 0;
      arr.push(now);
      keyTimes.set(el, arr);
      return;
    }
    if (e.key !== 'Enter' && e.key !== 'Tab') return;
    const times = keyTimes.get(el) || [];
    keyTimes.set(el, []);
    const burst = Pure.isBurst(times);
    const code = String(el.value || '').trim();
    if (!burst) return; // human typing: normal Enter
    wedgeSeen = true;
    try { sessionStorage.setItem('hub-wedge-seen', '1'); } catch (_) { /* private */ }
    const win = Number(el.getAttribute('data-hub-wedge-debounce')) || 800;
    const prev = lastWedge.get(el);
    el.dispatchEvent(new CustomEvent('hub:wedge', { bubbles: true, detail: { code } }));
    if (Pure.duplicate(prev, code, Date.now(), win)) {
      e.preventDefault();
      el.value = '';
      toast(t('js.dev_dup', 'Leitura repetida ignorada.'));
      return;
    }
    lastWedge.set(el, { code, at: Date.now() });
    if (e.key === 'Tab') e.preventDefault();
    const form = el.form;
    if (form) {
      form.dataset.hubVia = 'leitor';
      if (e.key === 'Tab' || el.tagName === 'TEXTAREA') { e.preventDefault(); form.requestSubmit ? form.requestSubmit() : null; }
    }
  }
  document.addEventListener('keydown', onWedgeKey, true);
  // Keeps the focus in the reader's field (only with a physical keyboard/reader — on a phone without a reader
  // giving the focus back would open the virtual keyboard on every tap).
  document.addEventListener('focusout', (e) => {
    const el = e.target;
    if (!el.matches || !el.matches('[data-hub-wedge]') || !inScope(el) || !keepFocusWanted()) return;
    setTimeout(() => {
      const a = document.activeElement;
      if (!el.isConnected || document.querySelector('[data-hub-layer]')) return;
      if (a && a !== document.body && a.matches && a.matches('input, select, textarea, button, a[href], [tabindex], summary')) return;
      try { el.focus({ preventScroll: true }); } catch (_) { /* none */ }
    }, 250);
  });

  // ---- offline queue (IndexedDB) ------------------------------------------------------------
  const DB = 'hub-offline';
  let dbp = null;
  function db() {
    if (dbp) return dbp;
    dbp = new Promise((resolve, reject) => {
      if (!window.indexedDB) { reject(new Error('sem IndexedDB')); return; }
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = () => {
        const d = r.result;
        if (!d.objectStoreNames.contains('queue')) d.createObjectStore('queue', { keyPath: 'id', autoIncrement: true }).createIndex('app', 'app');
        if (!d.objectStoreNames.contains('errors')) d.createObjectStore('errors', { keyPath: 'id', autoIncrement: true }).createIndex('app', 'app');
      };
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => { dbp = null; reject(r.error); };
    });
    return dbp;
  }
  async function tx(store, mode, fn) {
    const d = await db();
    return new Promise((resolve, reject) => {
      const x = d.transaction(store, mode);
      const s = x.objectStore(store);
      let out;
      Promise.resolve(fn(s)).then((v) => { out = v; });
      x.oncomplete = () => resolve(out);
      x.onerror = () => reject(x.error);
      x.onabort = () => reject(x.error);
    });
  }
  const req2p = (r) => new Promise((resolve, reject) => { r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
  async function all(store, appId) {
    return tx(store, 'readonly', (s) => req2p(appId ? s.index('app').getAll(appId) : s.getAll()));
  }
  const enqueue = (item) => tx('queue', 'readwrite', (s) => req2p(s.add(item)));
  const dequeue = (id) => tx('queue', 'readwrite', (s) => req2p(s.delete(id)));
  const addError = (e) => tx('errors', 'readwrite', (s) => req2p(s.add(e)));
  async function clearErrors(appId) {
    const list = await all('errors', appId);
    await tx('errors', 'readwrite', (s) => Promise.all(list.map((x) => req2p(s.delete(x.id)))));
  }

  let syncing = false;
  async function refreshStatus(extra) {
    const boxes = [...document.querySelectorAll('[data-hub-offline-status]')].filter(inScope);
    const lists = [...document.querySelectorAll('[data-hub-offline-errors]')].filter(inScope);
    if (!boxes.length && !lists.length) return;
    let pending = 0, errors = [];
    try { pending = (await all('queue', app)).length; errors = await all('errors', app); } catch (_) { /* no IndexedDB */ }
    const online = navigator.onLine !== false;
    const state = errors.length ? 'error' : pending ? 'pending' : online ? 'online' : 'offline';
    let text;
    if (syncing && pending) text = fmt(t('js.dev_q_sending', 'Enviando {n} pendente(s)…'), { n: pending });
    else if (pending) text = fmt(online ? t('js.dev_q_pending_on', '{n} pendente(s) — enviando em seguida') : t('js.dev_q_pending', 'Sem conexão — {n} pendente(s), enviados quando a conexão voltar'), { n: pending });
    else if (!online) text = t('js.dev_q_offline', 'Sem conexão — as leituras ficam guardadas neste aparelho');
    else text = t('js.dev_q_online', 'Conectado');
    if (errors.length) text += ` · ${fmt(t('js.dev_q_errors', '{n} com erro'), { n: errors.length })}`;
    if (extra) text = extra;
    for (const b of boxes) {
      b.dataset.state = state;
      b.dataset.pending = String(pending);
      const tx2 = b.querySelector('[data-hub-offline-text]') || b;
      tx2.textContent = text;
    }
    for (const l of lists) {
      l.hidden = errors.length === 0;
      const items = errors.slice(-20).map((er) => {
        const li = document.createElement('li');
        li.textContent = `${er.label || ''} · HTTP ${er.status}: ${er.message || ''}`.trim();
        return li;
      });
      if (errors.length) {
        const li = document.createElement('li');
        li.className = 'hub-offline-clear';
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'secondary-button';
        b.textContent = t('js.dev_q_clear', 'Limpar a lista de erros');
        b.addEventListener('click', async () => { await clearErrors(app); refreshStatus(); });
        li.append(b);
        items.push(li);
      }
      l.replaceChildren(...items);
    }
  }

  function insertResponse(targetId, html) {
    const target = targetId ? ref(targetId) : null;
    if (!html || !html.trim()) return;
    if (target && window.HubKit) { window.HubKit.insert(target, html, 'outerHTML'); return; }
    const content = appRoot()?.querySelector('.workspace-content');
    if (content && window.HubSafe) { window.HubSafe.setHTML(content, html, { app }); window.HubKit?.mount(content); }
  }

  async function send(item) {
    let res;
    try {
      res = await fetch(`${fragPrefix}${item.path}`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { accept: 'text/html', 'x-hub-request': '1', 'content-type': 'application/x-www-form-urlencoded', 'Idempotency-Key': item.key, 'x-hub-offline': item.queued ? '1' : '0' },
        body: item.body,
      });
    } catch (_) { return { status: 0, html: '', fb: null }; }
    const html = await res.text().catch(() => '');
    return { status: res.status, html, fb: res.headers.get('x-hub-feedback'), toast: res.headers.get('x-hub-toast') };
  }

  async function sync() {
    if (syncing || navigator.onLine === false || !app) return;
    let items;
    try { items = await all('queue', app); } catch (_) { return; }
    if (!items.length) return;
    syncing = true;
    refreshStatus();
    let sent = 0, last = null;
    try {
      for (const it of items.sort((a, b) => a.id - b.id)) {
        const r = await send(it);
        const out = Pure.replayOutcome(r.status);
        if (out === 'login') { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`; return; }
        if (out === 'retry') break;
        await dequeue(it.id);
        if (out === 'error') await addError({ app, label: it.label, status: r.status, message: Pure.plain(r.html), at: Date.now(), path: it.path });
        else { sent++; last = { it, r }; }
      }
    } finally {
      syncing = false;
    }
    if (last) insertResponse(last.it.target, last.r.html);
    if (sent) toast(fmt(t('js.dev_q_synced', '{n} envio(s) pendente(s) concluído(s).'), { n: sent }));
    refreshStatus();
  }

  function afterSubmit(form) {
    form.querySelectorAll('[data-hub-clear]').forEach((f) => { f.value = ''; });
    const w = form.querySelector('[data-hub-wedge]');
    if (w && (keepFocusWanted() || document.activeElement === w)) { try { w.focus({ preventScroll: true }); } catch (_) { /* none */ } }
  }

  // Capture: after hub-kit (validation/confirmation), before hub.js (which would send via XHR).
  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (!form.matches || !form.matches('form[data-hub-offline-queue]') || !inScope(form)) return;
    if ((form.getAttribute('method') || 'get').toLowerCase() !== 'post' || form.querySelector('input[type=file]')) return;
    const path = ownPath(form.getAttribute('action') || location.href);
    if (!path) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const data = new FormData(form, e.submitter || undefined);
    const body = new URLSearchParams([...data].filter(([, v]) => typeof v === 'string')).toString();
    const item = { app, path, body, key: uuid(), label: form.getAttribute('data-hub-offline-queue') || '', target: (form.getAttribute('data-hub-target') || '').replace(/^#/, ''), at: Date.now() };
    afterSubmit(form);
    const queueIt = async () => {
      try {
        await enqueue({ ...item, queued: true });
        feedback('queued', form.closest('[data-hub-feedback]')?.getAttribute('data-hub-feedback') ?? '');
        toast(t('js.dev_q_saved', 'Sem conexão: envio guardado neste aparelho.'));
      } catch (_) {
        toast(t('js.dev_q_nodb', 'Sem conexão e sem espaço para guardar o envio.'));
      }
      refreshStatus();
    };
    if (Pure.queueFirst(navigator.onLine)) { await queueIt(); return; }
    // With a pending queue, this submission goes to the end of it (order guaranteed).
    let pending = 0;
    try { pending = (await all('queue', app)).length; } catch (_) { pending = 0; }
    if (pending) { await queueIt(); sync(); return; }
    const r = await send(item);
    const out = Pure.replayOutcome(r.status);
    if (out === 'login') { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`; return; }
    if (r.status === 0 || (out === 'retry' && r.status >= 502)) { await queueIt(); return; }
    insertResponse(item.target, r.html);
    if (r.fb) feedback(r.fb, form.closest('[data-hub-feedback]')?.getAttribute('data-hub-feedback'));
    if (r.toast) { let m = r.toast; try { m = decodeURIComponent(m); } catch (_) { /* cru */ } toast(m); }
  }, true);
  window.addEventListener('online', () => { refreshStatus(); sync(); });
  window.addEventListener('offline', () => refreshStatus());
  setInterval(() => { if (navigator.onLine !== false) sync(); }, 15000);

  // ---- photo (capture + compression) ---------------------------------------------------------
  const compressed = new WeakSet();
  async function toJpeg(source, w0, h0) {
    const [w, h] = Pure.fit(w0, h0, 1600);
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    c.getContext('2d').drawImage(source, 0, 0, w, h);
    const blob = await new Promise((res) => c.toBlob(res, 'image/jpeg', 0.8));
    return { blob, w, h };
  }
  function putFile(input, blob, previewSel) {
    const name = `foto-${new Date().toISOString().replace(/[:.]/g, '-')}.jpg`;
    const file = new File([blob], name, { type: 'image/jpeg' });
    const dt = new DataTransfer();
    dt.items.add(file);
    compressed.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    const pv = previewSel ? ref(previewSel) : null;
    if (pv) {
      const img = document.createElement('img');
      img.alt = t('js.dev_photo_preview', 'Prévia da foto');
      img.src = URL.createObjectURL(blob);
      pv.replaceChildren(img);
    }
    return file;
  }
  async function openCapture(opts) {
    const ui = cameraLayer(t('js.dev_capture_title', 'Fotografar'), 'capture');
    let stream = null;
    const prevFocus = document.activeElement;
    const done = () => { stopStream(stream); ui.host.remove(); document.removeEventListener('keydown', esc, true); try { prevFocus?.focus?.(); } catch (_) { /* none */ } };
    const esc = (e) => { if (e.key === 'Escape') { e.preventDefault(); done(); } };
    document.addEventListener('keydown', esc, true);
    ui.close.addEventListener('click', done);
    const shot = document.createElement('button');
    shot.type = 'button';
    shot.className = 'hub-camera-shot';
    shot.textContent = t('js.dev_shoot', 'Fotografar');
    shot.disabled = true;
    ui.bar.prepend(shot);
    ui.close.focus();
    ui.status.textContent = t('js.dev_opening', 'Abrindo a câmera…');
    try { stream = await openCamera(); } catch (e) {
      ui.status.textContent = cameraError(e);
      if (opts.fallback) {
        const pick = document.createElement('button');
        pick.type = 'button';
        pick.className = 'hub-camera-shot';
        pick.textContent = t('js.dev_pick', 'Escolher foto do aparelho');
        pick.addEventListener('click', () => { done(); opts.fallback(); });
        ui.bar.prepend(pick);
      }
      opts.onError && opts.onError(e);
      return;
    }
    ui.video.srcObject = stream;
    ui.video.play().catch(() => {});
    ui.status.textContent = t('js.dev_capture_hint', 'Enquadre e toque em Fotografar.');
    shot.disabled = false;
    shot.addEventListener('click', async () => {
      shot.disabled = true;
      const { blob, w, h } = await toJpeg(ui.video, ui.video.videoWidth, ui.video.videoHeight);
      done();
      opts.onPhoto(blob, w, h);
    });
  }
  async function compressInput(input, previewSel) {
    const f = input.files && input.files[0];
    if (!f || compressed.has(f) || !/^image\//.test(f.type)) return;
    try {
      const bmp = await createImageBitmap(f);
      const { blob } = await toJpeg(bmp, bmp.width, bmp.height);
      if (blob && blob.size < f.size) putFile(input, blob, previewSel);
      else { compressed.add(f); const pv = previewSel ? ref(previewSel) : null; if (pv) { const img = document.createElement('img'); img.alt = t('js.dev_photo_preview', 'Prévia da foto'); img.src = URL.createObjectURL(f); pv.replaceChildren(img); } }
    } catch (_) { /* format the browser does not decode: keeps the original */ }
  }
  document.addEventListener('change', (e) => {
    const input = e.target;
    if (!input.matches || !input.matches('input[type=file]') || !inScope(input) || !input.id) return;
    const btn = [...document.querySelectorAll('[data-hub-capture]')].find((b) => inScope(b) && String(b.getAttribute('data-hub-capture')).replace(/^#/, '') === input.id);
    if (btn) compressInput(input, btn.getAttribute('data-hub-capture-preview'));
  });
  Object.assign(P, { keepFocusWanted, db, all, refreshStatus, sync, putFile, openCapture });
})();
