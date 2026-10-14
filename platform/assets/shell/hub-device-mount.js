/* hub-device-mount.js — PWA installation, diagnostics, camera preferences, the click/change
 * delegation and the mount of every data-hub-* device behavior; publishes window.HubDevice.
 * Last part of hub-device.js (split only for size), loaded after hub-device-forms.js. */
(function () {
  'use strict';
  const P = window.__hubDeviceParts;
  if (!P) return;
  const { Pure, fragPrefix, inScope, appRoot, t, toast, ref, ownPath, uuid, audioCtx, beep, vibrate,
    feedback, scanFeedback, openCamera, cameraError, stopStream, torchCapable, setTorch,
    openScanner, deliver, keepFocusWanted, db, all, refreshStatus, sync, putFile, openCapture } = P;
  // ---- installation (PWA) --------------------------------------------------------------------
  let installEvt = null;
  const standalone = () => (window.matchMedia && matchMedia('(display-mode: standalone)').matches) || navigator.standalone === true;
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); installEvt = e; refreshInstall(); });
  window.addEventListener('appinstalled', () => { installEvt = null; refreshInstall(true); });
  function refreshInstall(justInstalled) {
    const root = appRoot();
    if (!root) return;
    const done = justInstalled || standalone();
    const ios = Pure.isIOS(navigator.userAgent, navigator.maxTouchPoints);
    root.querySelectorAll('[data-hub-install]').forEach((b) => { b.hidden = done || !installEvt; });
    root.querySelectorAll('[data-hub-install-done]').forEach((b) => { b.hidden = !done; });
    root.querySelectorAll('[data-hub-install-ios]').forEach((b) => { b.hidden = done || !ios; });
    root.querySelectorAll('[data-hub-install-unavailable]').forEach((b) => { b.hidden = done || Boolean(installEvt) || ios; });
  }
  async function install() {
    if (!installEvt) return false;
    const e = installEvt;
    installEvt = null;
    await e.prompt();
    const c = await e.userChoice.catch(() => ({ outcome: 'dismissed' }));
    refreshInstall(c.outcome === 'accepted');
    return c.outcome === 'accepted';
  }

  // ---- diagnostics -------------------------------------------------------------------------
  const DIAG = { ok: ['js.dev_ok', 'OK'], fail: ['js.dev_fail', 'Falhou'], unsupported: ['js.dev_unsup', 'Não suportado'], pending: ['js.dev_pending', 'Aguardando teste'], running: ['js.dev_running', 'Testando…'] };
  function setDiag(row, state, detail) {
    if (!row) return;
    const cell = row.querySelector('[data-hub-diag-status]');
    if (!cell) return;
    cell.dataset.diagState = state;
    const [k, fb] = DIAG[state] || DIAG.pending;
    cell.textContent = detail ? `${t(k, fb)} · ${detail}` : t(k, fb);
    row.dataset.diagState = state;
  }
  const rowOf = (scope, check) => scope.querySelector(`[data-hub-diag-check="${check}"]`);
  function autoDiag(scope) {
    scope.querySelectorAll('[data-hub-diag-check]').forEach((r) => { if (!r.dataset.diagState) setDiag(r, 'pending'); });
    setDiag(rowOf(scope, 'secure'), window.isSecureContext ? 'ok' : 'fail', location.protocol.replace(':', ''));
    setDiag(rowOf(scope, 'online'), navigator.onLine === false ? 'fail' : 'ok', navigator.onLine === false ? t('js.dev_offline', 'sem conexão') : t('js.dev_online', 'online'));
    setDiag(rowOf(scope, 'pwa'), standalone() ? 'ok' : 'pending', standalone() ? '' : t('js.dev_not_installed', 'aberto no navegador'));
    db().then(() => setDiag(rowOf(scope, 'storage'), 'ok', 'IndexedDB')).catch(() => setDiag(rowOf(scope, 'storage'), 'unsupported'));
    const vib = rowOf(scope, 'vibrate');
    if (vib && !navigator.vibrate) setDiag(vib, 'unsupported');
    if (!window.isSecureContext || !navigator.mediaDevices) ['camera', 'torch', 'scan', 'capture'].forEach((c) => setDiag(rowOf(scope, c), 'unsupported', window.isSecureContext ? '' : 'HTTPS'));
  }
  async function runDiag(row) {
    const check = row.getAttribute('data-hub-diag-check');
    setDiag(row, 'running');
    try {
      if (check === 'camera') {
        const s = await openCamera();
        const st = s.getVideoTracks()[0]?.getSettings?.() || {};
        stopStream(s);
        setDiag(row, 'ok', st.width ? `${st.width}×${st.height}${st.facingMode ? ` · ${st.facingMode}` : ''}` : '');
      } else if (check === 'torch') {
        const s = await openCamera();
        if (!torchCapable(s)) { stopStream(s); setDiag(row, 'unsupported'); return; }
        await setTorch(s, true);
        await new Promise((r) => setTimeout(r, 800));
        stopStream(s);
        setDiag(row, 'ok');
      } else if (check === 'scan') {
        await openScanner({ continuous: false, onRead: (code, f) => { setDiag(row, 'ok', `${code}${f ? ` · ${f}` : ''}`); return true; }, onClose: (n) => { if (!n && row.dataset.diagState === 'running') setDiag(row, 'pending'); }, onError: (e) => setDiag(row, 'fail', cameraError(e)) });
        const host = document.querySelector('.hub-camera');
        if (host && host.dataset.decoder) row.dataset.decoder = host.dataset.decoder;
      } else if (check === 'beep') {
        const ok = beep('ok');
        setDiag(row, ok ? 'ok' : (audioCtx() ? 'fail' : 'unsupported'));
      } else if (check === 'vibrate') {
        if (!navigator.vibrate) { setDiag(row, 'unsupported'); return; }
        setDiag(row, vibrate('warn') ? 'ok' : 'fail');
      } else if (check === 'capture') {
        await openCapture({ onPhoto: (blob, w, h) => setDiag(row, 'ok', `${w}×${h} · ${Math.round(blob.size / 1024)} KB`), onError: (e) => setDiag(row, 'fail', cameraError(e)) });
      }
    } catch (e) {
      setDiag(row, e && (e.name === 'NotSupportedError') ? 'unsupported' : 'fail', cameraError(e));
    }
  }
  function diagReport(scope) {
    const rows = [...scope.querySelectorAll('[data-hub-diag-check]')].map((r) => {
      const label = (r.querySelector('th') || r.firstElementChild)?.textContent.trim() || r.getAttribute('data-hub-diag-check');
      return { label, result: r.querySelector('[data-hub-diag-status]')?.textContent.trim() || '' };
    });
    const info = [...scope.querySelectorAll('[data-hub-diag-info]')].map((x) => [x.getAttribute('data-hub-diag-info'), x.textContent.trim()]);
    info.push(['Tela', `${screen.width}×${screen.height} @${window.devicePixelRatio || 1}x`]);
    return Pure.report(rows, info, { ua: navigator.userAgent, when: new Date().toLocaleString() });
  }

  /** Choices made in the camera layer → button (next opening) + app
   *  (`data-hub-scan-prefs`: POST mode/reader/pause, the app stores them per user). */
  let prefsSeq = 0;
  async function savePrefs(btn, path, p) {
    if (p.mode) btn.setAttribute('data-hub-scan-mode', p.mode);
    if (p.reader) { btn.setAttribute('data-hub-scan-reader', p.reader); btn.setAttribute('data-hub-scan-cooldown', String(p.cooldown)); }
    if (!path) return;
    const body = new URLSearchParams();
    if (p.mode) body.set('modo', p.mode);
    if (p.reader) { body.set('leitor', p.reader); body.set('pausa', String(p.cooldown)); }
    const seq = ++prefsSeq;
    try {
      const res = await fetch(`${fragPrefix}${path}`, { method: 'POST', credentials: 'same-origin', headers: { accept: 'text/html', 'x-hub-request': '1', 'content-type': 'application/x-www-form-urlencoded', 'Idempotency-Key': uuid() }, body });
      if (!res.ok && seq === prefsSeq) toast(t('js.dev_prefs_fail', 'Não foi possível salvar a escolha.'));
    } catch (_) {
      if (seq === prefsSeq) toast(t('js.dev_prefs_fail', 'Não foi possível salvar a escolha.'));
    }
  }

  // ---- delegation ---------------------------------------------------------------------------
  document.addEventListener('click', (e) => {
    const t0 = e.target;
    if (!inScope(t0)) return;
    const sc = t0.closest('[data-hub-scan]');
    if (sc) {
      e.preventDefault();
      const target = ref(sc.getAttribute('data-hub-scan-target'));
      if (!target) return;
      const fmtTarget = ref(sc.getAttribute('data-hub-scan-format-target'));
      const modes = Pure.scanModes(sc.getAttribute('data-hub-scan-formats'), sc.getAttribute('data-hub-scan-modes'), sc.getAttribute('data-hub-scan-mode-labels'), sc.getAttribute('data-hub-scan-wrong-hint'));
      const modeTarget = ref(sc.getAttribute('data-hub-scan-mode-target'));
      const readerMode = Pure.readerMode(sc.getAttribute('data-hub-scan-reader'));
      const prefsPath = ownPath(sc.getAttribute('data-hub-scan-prefs'));
      openScanner({
        modes,
        reader: readerMode ? { mode: readerMode, cooldown: sc.getAttribute('data-hub-scan-cooldown'), cooldowns: sc.getAttribute('data-hub-scan-cooldowns') } : null,
        onPrefs: (p) => savePrefs(sc, prefsPath, p),
        mode: Pure.modeIndex(modes, sc.getAttribute('data-hub-scan-mode') || ''),
        dedupMs: Math.min(60000, Math.max(0, Number(sc.getAttribute('data-hub-scan-dedup')) || 0)),
        onMode: (key) => {
          if (!modeTarget || modeTarget.tagName !== 'INPUT') return;
          modeTarget.value = key;
          modeTarget.dispatchEvent(new Event('change', { bubbles: true }));
        },
        formats: Pure.parseFormats(String(sc.getAttribute('data-hub-scan-formats') || '').split('|')[0]),
        continuous: sc.hasAttribute('data-hub-scan-continuous'),
        title: sc.getAttribute('data-hub-scan-title') || undefined,
        wrongHint: String(sc.getAttribute('data-hub-scan-wrong-hint') || '').split('|')[0].slice(0, 160) || null,
        onRead: (code, fmt) => { deliver(target, code, true, fmt, fmtTarget && fmtTarget.tagName === 'INPUT' ? fmtTarget : null); return false; },
      });
      return;
    }
    const cap = t0.closest('[data-hub-capture]');
    if (cap && cap.tagName !== 'INPUT') {
      e.preventDefault();
      const input = ref(cap.getAttribute('data-hub-capture'));
      if (!input || input.type !== 'file') return;
      openCapture({ onPhoto: (blob) => putFile(input, blob, cap.getAttribute('data-hub-capture-preview')), fallback: () => input.click() });
      return;
    }
    if (t0.closest('[data-hub-install]')) { e.preventDefault(); install(); return; }
    if (t0.closest('[data-hub-print]')) { e.preventDefault(); window.print(); return; }
    const run = t0.closest('[data-hub-diag-run]');
    if (run) { e.preventDefault(); const row = run.closest('[data-hub-diag-check]'); if (row) runDiag(row); return; }
    const copy = t0.closest('[data-hub-diag-copy]');
    if (copy) {
      e.preventDefault();
      const scope = copy.closest('[data-hub-diag]');
      if (!scope) return;
      const text = diagReport(scope);
      copy.dataset.report = text;
      const ok = () => toast(t('js.dev_copied', 'Relatório copiado.'));
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(ok, () => toast(t('js.kit_copy_fail', 'Não foi possível copiar.')));
      else toast(t('js.kit_copy_fail', 'Não foi possível copiar.'));
    }
  }, true);
  document.addEventListener('hub:wedge', (e) => {
    const el = e.target;
    if (el.matches && el.matches('[data-hub-diag-wedge]')) {
      setDiag(el.closest('[data-hub-diag-check]'), 'ok', e.detail.code);
      setTimeout(() => { el.value = ''; }, 50);
    }
  });

  function mount(root) {
    if (!root || !root.querySelectorAll) return;
    scanFeedback(root);
    refreshStatus();
    refreshInstall();
    (root.matches?.('[data-hub-diag]') ? [root] : [...root.querySelectorAll('[data-hub-diag]')]).forEach(autoDiag);
    if (keepFocusWanted()) {
      const w = root.querySelector?.('input[data-hub-wedge]:not([data-hub-diag-wedge])');
      if (w && !document.querySelector('[data-hub-layer]') && (!document.activeElement || document.activeElement === document.body || !inScope(document.activeElement))) {
        try { w.focus({ preventScroll: true }); } catch (_) { /* none */ }
      }
    }
  }
  // Every insertion into the app content (hub.js, hub-kit, offline queue) goes through here.
  const obs = new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1) mount(n);
  });
  const start = () => {
    const root = appRoot();
    if (root) obs.observe(root, { childList: true, subtree: true });
    if (root) mount(root);
    sync();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();

  window.HubDevice = Object.freeze({ pure: Pure, feedback, beep, vibrate, openScanner, openCapture, sync, refreshStatus, install, deliver, queue: { all: (id) => all('queue', id), errors: (id) => all('errors', id) } });
  delete window.__hubDeviceParts;
})();
