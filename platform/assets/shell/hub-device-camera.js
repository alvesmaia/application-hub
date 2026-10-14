/* hub-device-camera.js — camera reading (data-hub-scan) and the feedback of the app's answer
 * to a reading. Second part of hub-device.js (split only for size; its header documents the
 * attributes), loaded right after it on the same pages. */
(function () {
  'use strict';
  const P = window.__hubDeviceParts;
  if (!P) return;
  const { FORMATS, DEFAULT_FORMATS, Pure, inScope, t, fmt, toast, vibrate, feedback } = P;
  const seen = new WeakSet();
  function scanFeedback(root) {
    const els = root.matches?.('[data-hub-feedback-result]') ? [root] : [...(root.querySelectorAll?.('[data-hub-feedback-result]') || [])];
    for (const el of els) {
      if (seen.has(el) || !inScope(el)) continue;
      seen.add(el);
      const box = el.closest('[data-hub-feedback]');
      if (!box) continue;
      feedback(el.getAttribute('data-hub-feedback-result'), box.getAttribute('data-hub-feedback') || undefined);
      scanner?.result(el);
    }
  }

  // ---- camera reading -----------------------------------------------------------------
  let zxing = null;
  function loadZxing() {
    if (window.ZXing) return Promise.resolve(window.ZXing);
    if (zxing) return zxing;
    zxing = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.onload = () => (window.ZXing ? resolve(window.ZXing) : reject(new Error('zxing')));
      s.onerror = () => { zxing = null; reject(new Error('zxing')); };
      try { s.src = window.HubSafe ? window.HubSafe.scriptURL('/assets/vendor/zxing.min.js') : '/assets/vendor/zxing.min.js'; } catch (e) { reject(e); return; }
      document.head.append(s);
    });
    return zxing;
  }
  /** Decoder: native (BarcodeDetector) when it covers the formats; otherwise ZXing. */
  async function makeDecoder(formats) {
    if (window.HubDeviceDecoder) return window.HubDeviceDecoder; // test hook (E2E)
    if ('BarcodeDetector' in window) {
      try {
        const sup = await window.BarcodeDetector.getSupportedFormats();
        const use = formats.filter((f) => sup.includes(f));
        if (use.length) {
          const det = new window.BarcodeDetector({ formats: use });
          // `src` = crop of the aiming frame; `aim` = the frame inside it (crop px).
          return { kind: 'nativo', async decode(src, aim) { const r = Pure.pickInside(await det.detect(src), aim); return r ? { text: r.rawValue, format: r.format } : null; } };
        }
      } catch (_) { /* falls back to ZXing */ }
    }
    const Z = await loadZxing();
    const hints = new Map();
    hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, formats.map((f) => Z.BarcodeFormat[FORMATS[f]]).filter((x) => x !== undefined));
    hints.set(Z.DecodeHintType.TRY_HARDER, true);
    const reader = new Z.MultiFormatReader();
    reader.setHints(hints);
    const canvas = document.createElement('canvas');
    const ctx2d = canvas.getContext('2d', { willReadFrequently: true });
    // One scale per frame, in rotation: ZXing's detector finds the code at different sizes
    // depending on the distance (the native BarcodeDetector does not need this).
    const SCALES = [1024, 640, 420];
    let turn = 0;
    return {
      kind: 'zxing',
      async decode(src, aim) {
        const w0 = src.videoWidth || src.width;
        const h0 = src.videoHeight || src.height;
        if (!w0 || !h0) return null;
        const [w, h] = Pure.fit(w0, h0, SCALES[turn++ % SCALES.length]);
        canvas.width = w; canvas.height = h;
        ctx2d.drawImage(src, 0, 0, w, h);
        const lum = Pure.luminance(ctx2d.getImageData(0, 0, w, h).data, w, h);
        let r;
        try {
          r = reader.decodeWithState(new Z.BinaryBitmap(new Z.HybridBinarizer(new Z.RGBLuminanceSource(lum, w, h))));
        } catch (_) { return null; }
        const k = w0 / w;
        const pts = (r.getResultPoints ? r.getResultPoints() || [] : []).map((p) => ({ x: p.getX() * k, y: p.getY() * k }));
        if (!Pure.pickInside([{ cornerPoints: pts.length ? pts : null }], aim)) return null;
        return { text: r.getText(), format: String(Z.BarcodeFormat[r.getBarcodeFormat()] || '').toLowerCase() };
      },
    };
  }
  async function openCamera() {
    if (!window.isSecureContext) throw Object.assign(new Error('insecure'), { name: 'InsecureError' });
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw Object.assign(new Error('unsupported'), { name: 'NotSupportedError' });
    return navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } } });
  }
  function cameraError(e) {
    const n = e && e.name;
    if (n === 'InsecureError') return t('js.dev_insecure', 'A câmera só funciona em conexão segura (HTTPS). Abra o hub pelo endereço https://.');
    if (n === 'NotAllowedError' || n === 'SecurityError') return t('js.dev_denied', 'Permissão da câmera negada. Libere a câmera nas configurações do navegador para este site e tente de novo.');
    if (n === 'NotFoundError' || n === 'OverconstrainedError') return t('js.dev_nocamera', 'Nenhuma câmera encontrada neste aparelho.');
    if (n === 'NotReadableError') return t('js.dev_busy', 'A câmera está em uso por outro app. Feche-o e tente de novo.');
    if (n === 'NotSupportedError') return t('js.dev_unsupported', 'Este navegador não dá acesso à câmera.');
    return t('js.dev_camera_fail', 'Não foi possível abrir a câmera.');
  }
  function stopStream(stream) { try { stream && stream.getTracks().forEach((tr) => tr.stop()); } catch (_) { /* none */ } }
  function torchCapable(stream) {
    try { const tr = stream.getVideoTracks()[0]; return Boolean(tr && tr.getCapabilities && tr.getCapabilities().torch); } catch (_) { return false; }
  }
  async function setTorch(stream, on) {
    const tr = stream.getVideoTracks()[0];
    await tr.applyConstraints({ advanced: [{ torch: on }] });
  }

  /** Segmented selector (buttons with aria-pressed) for the camera's top bar. */
  function segmented(label, items, current, attr) {
    const seg = document.createElement('div');
    seg.className = 'hub-camera-seg';
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', label);
    items.forEach(([key, text]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'hub-camera-mode';
      b.setAttribute(attr, key);
      b.textContent = text;
      b.setAttribute('aria-pressed', String(key === current));
      seg.append(b);
    });
    return seg;
  }
  /** Hub layer with the camera video (reading or photo). `modes` = switch of code type;
   *  `reader` = {mode, cooldown, cooldowns} (Interactive/Automatic + pause) or null. */
  function cameraLayer(title, mode, square, modes, current, reader) {
    const host = document.createElement('div');
    host.className = `hub-camera hub-camera-${mode}`;
    host.setAttribute('data-hub-layer', '');
    host.setAttribute('role', 'dialog');
    host.setAttribute('aria-modal', 'true');
    const hid = `hub-camera-title-${Date.now()}`;
    host.setAttribute('aria-labelledby', hid);
    const head = document.createElement('div');
    head.className = 'hub-camera-head';
    const h = document.createElement('h2');
    h.id = hid;
    h.textContent = title;
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'hub-camera-close';
    close.textContent = t('js.dev_close', 'Fechar');
    head.append(h, close);
    const stage = document.createElement('div');
    stage.className = 'hub-camera-stage';
    const video = document.createElement('video');
    video.setAttribute('playsinline', '');
    video.setAttribute('autoplay', '');
    video.muted = true;
    stage.append(video);
    let aim = null;
    if (mode === 'scan') {
      aim = document.createElement('div');
      aim.className = square ? 'hub-camera-aim hub-camera-aim-square' : 'hub-camera-aim';
      aim.setAttribute('aria-hidden', 'true');
      for (const c of ['tl', 'tr', 'bl', 'br']) { const i = document.createElement('i'); i.className = `hub-camera-corner hub-camera-corner-${c}`; aim.append(i); }
      const laser = document.createElement('i');
      laser.className = 'hub-camera-laser';
      aim.append(laser);
      stage.append(aim);
    }
    // Top bar (over the video): code type + reader mode + pause.
    let seg = null, rseg = null, pause = null, pauseBox = null;
    if ((modes && modes.length) || reader) {
      const top = document.createElement('div');
      top.className = 'hub-camera-top';
      if (modes && modes.length) {
        seg = segmented(t('js.dev_scan_modes', 'Tipo de código'), modes.map((m) => [m.key, m.label]), modes[current] && modes[current].key, 'data-mode');
        seg.classList.add('hub-camera-modes');
        top.append(seg);
      }
      if (reader) {
        rseg = segmented(t('js.dev_reader_modes', 'Modo do leitor'), [['interativo', t('js.dev_reader_interactive', 'Interativo')], ['automatico', t('js.dev_reader_auto', 'Automático')]], reader.mode, 'data-reader');
        rseg.classList.add('hub-camera-readers');
        top.append(rseg);
        pauseBox = document.createElement('label');
        pauseBox.className = 'hub-camera-pause';
        const span = document.createElement('span');
        span.textContent = t('js.dev_reader_pause', 'Pausa');
        pause = document.createElement('select');
        pause.className = 'hub-camera-pause-select';
        for (const ms of reader.cooldowns) {
          const o = document.createElement('option');
          o.value = String(ms);
          o.textContent = ms === 0 ? t('js.dev_reader_nopause', 'Sem pausa') : fmt(t('js.dev_reader_secs', '{n} s'), { n: Math.round(ms / 100) / 10 });
          if (ms === reader.cooldown) o.selected = true;
          pause.append(o);
        }
        pauseBox.append(span, pause);
        pauseBox.hidden = reader.mode !== 'automatico';
        top.append(pauseBox);
      }
      stage.append(top);
      host.classList.add('hub-camera-with-top');
    }
    const status = document.createElement('p');
    status.className = 'hub-camera-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const last = document.createElement('div');
    last.className = 'hub-camera-last';
    last.hidden = true;
    const next = document.createElement('button');
    next.type = 'button';
    next.className = 'hub-camera-next';
    next.hidden = true;
    const bar = document.createElement('div');
    bar.className = 'hub-camera-bar';
    const torch = document.createElement('button');
    torch.type = 'button';
    torch.className = 'hub-camera-torch';
    torch.hidden = true;
    torch.setAttribute('aria-pressed', 'false');
    torch.textContent = t('js.dev_torch', 'Lanterna');
    bar.append(next, torch);
    host.append(head, stage, status, last, bar);
    document.body.append(host);
    return { host, video, stage, status, last, bar, torch, close, seg, rseg, pause, pauseBox, aim, next };
  }

  let scanner = null;
  /** Opens the reading. `opts`: {formats, continuous, onRead(code, fmt) → bool (true = closes)}. */
  async function openScanner(opts) {
    if (scanner) return;
    const prevFocus = document.activeElement;
    const modes = opts.modes || [];
    let modeIdx = modes.length ? Math.min(Math.max(0, opts.mode | 0), modes.length - 1) : 0;
    let formats = modes.length ? modes[modeIdx].formats : opts.formats || DEFAULT_FORMATS;
    let wrongHint = modes.length ? modes[modeIdx].hint || opts.wrongHint : opts.wrongHint;
    const reader = opts.reader && Pure.readerMode(opts.reader.mode)
      ? { mode: Pure.readerMode(opts.reader.mode), cooldowns: Pure.cooldownList(opts.reader.cooldowns), cooldown: 0 }
      : null;
    if (reader) {
      const want = Math.max(0, Math.round(Number(opts.reader.cooldown) || 0));
      reader.cooldown = reader.cooldowns.includes(want) ? want : reader.cooldowns[0];
    }
    const ui = cameraLayer(opts.title || t('js.dev_scan_title', 'Aponte para o código'), 'scan', Pure.is2d(formats), modes, modeIdx, reader);
    ui.host.dataset.formats = formats.join(',');
    if (modes.length) ui.host.dataset.mode = modes[modeIdx].key;
    if (reader) { ui.host.dataset.reader = reader.mode; ui.host.dataset.pause = String(reader.cooldown); }
    // Interactive: the camera waits for "Próximo" — the repeat window goes back to the default.
    const dedupMs = () => (reader && reader.mode === 'interativo' ? 1500 : opts.dedupMs > 0 ? opts.dedupMs : 1500);
    const prefs = () => opts.onPrefs && opts.onPrefs({ mode: modes.length ? modes[modeIdx].key : '', reader: reader ? reader.mode : '', cooldown: reader ? reader.cooldown : 0 });
    // Region of interest: only what is inside the aiming frame goes to the decoder.
    const crop = document.createElement('canvas');
    const cctx = crop.getContext('2d', { willReadFrequently: true });
    const frameOf = () => {
      const v = ui.video;
      const sr = v.getBoundingClientRect();
      const ar = ui.aim ? ui.aim.getBoundingClientRect() : null;
      const aim = ar && ar.width >= 2 && ar.height >= 2 ? { x: ar.left - sr.left, y: ar.top - sr.top, w: ar.width, h: ar.height } : Pure.aimRect(sr.width, sr.height, Pure.is2d(formats));
      const r = Pure.roi(v.videoWidth, v.videoHeight, sr.width, sr.height, aim);
      if (!r || !cctx) return null;
      crop.width = r.sw; crop.height = r.sh;
      cctx.drawImage(v, r.sx, r.sy, r.sw, r.sh, 0, 0, r.sw, r.sh);
      ui.host.dataset.roi = `${r.sx},${r.sy},${r.sw},${r.sh}`;
      return r;
    };
    if (ui.rseg) {
      ui.rseg.addEventListener('click', (e) => {
        const b = e.target.closest('[data-reader]');
        if (!b || b.getAttribute('data-reader') === reader.mode) return;
        reader.mode = b.getAttribute('data-reader');
        ui.rseg.querySelectorAll('[data-reader]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        ui.pauseBox.hidden = reader.mode !== 'automatico';
        ui.host.dataset.reader = reader.mode;
        if (held && reader.mode === 'automatico') release();
        ui.status.textContent = fmt(t('js.dev_scan_mode', 'Lendo: {m}'), { m: b.textContent });
        prefs();
      });
      ui.pause.addEventListener('change', () => {
        reader.cooldown = Math.max(0, Number(ui.pause.value) || 0);
        ui.host.dataset.pause = String(reader.cooldown);
        prefs();
      });
    }
    let holdUntil = 0; // pause (cooldown) after a reading
    let held = null; // app's "Próximo" button: camera stopped until touched
    let stream = null;
    let stop = false;
    let count = 0;
    let lastRead = null;
    const done = () => {
      stop = true;
      stopStream(stream);
      ui.host.remove();
      document.removeEventListener('keydown', esc, true);
      scanner = null;
      try { prevFocus && prevFocus.focus && prevFocus.focus(); } catch (_) { /* none */ }
      opts.onClose && opts.onClose(count);
    };
    const esc = (e) => { if (e.key === 'Escape') { e.preventDefault(); done(); } };
    document.addEventListener('keydown', esc, true);
    ui.close.addEventListener('click', done);
    const release = () => {
      held = null;
      holdUntil = Date.now() + 500; // the app's "Próximo" arrives before the next reading
      ui.next.hidden = true;
      ui.host.removeAttribute('data-held');
      ui.status.textContent = t('js.dev_scan_ready', 'Pronto: aponte para o próximo código.');
    };
    ui.next.addEventListener('click', () => {
      const b = held;
      release();
      if (b && b.isConnected) b.click(); // the app releases the next reading (data-hub-post)
    });
    scanner = {
      close: done,
      result(el) {
        if (!ui.host.isConnected) return;
        ui.last.hidden = false;
        ui.last.dataset.kind = el.getAttribute('data-hub-feedback-result') || '';
        const copy = el.cloneNode(true);
        copy.querySelectorAll('button, [data-hub-scan-next]').forEach((b) => b.remove());
        ui.last.textContent = copy.textContent.replace(/\s+/g, ' ').trim().slice(0, 140);
        const nb = el.querySelector('[data-hub-scan-next]');
        if (nb && inScope(nb)) {
          held = nb;
          ui.next.textContent = nb.textContent.replace(/\s+/g, ' ').trim().slice(0, 40) || t('js.dev_next', 'Próximo');
          ui.next.hidden = false;
          ui.host.setAttribute('data-held', '');
          ui.status.textContent = t('js.dev_scan_held', 'Leitura registrada. Toque em Próximo para ler outro código.');
          try { ui.next.focus(); } catch (_) { /* none */ }
          return;
        }
        const cd = Pure.cooldownMs(el.getAttribute('data-hub-scan-cooldown'));
        if (cd) {
          holdUntil = Date.now() + cd;
          ui.host.setAttribute('data-cooldown', String(cd));
          ui.status.textContent = fmt(t('js.dev_scan_cooldown', 'Pronto para a próxima leitura em {s} s.'), { s: Math.ceil(cd / 1000) });
          setTimeout(() => { if (Date.now() >= holdUntil && ui.host.isConnected && !held) { ui.host.removeAttribute('data-cooldown'); ui.status.textContent = t('js.dev_scan_ready', 'Pronto: aponte para o próximo código.'); } }, cd + 20);
        }
      },
    };
    ui.close.focus();
    ui.status.textContent = t('js.dev_opening', 'Abrindo a câmera…');
    try {
      stream = await openCamera();
    } catch (e) {
      ui.status.textContent = cameraError(e);
      ui.host.classList.add('hub-camera-failed');
      opts.onError && opts.onError(e);
      return;
    }
    if (stop) { stopStream(stream); return; }
    ui.video.srcObject = stream;
    ui.video.play().catch(() => {}); // no await: a flow with no frames would never resolve
    if (torchCapable(stream)) {
      ui.torch.hidden = false;
      ui.torch.addEventListener('click', async () => {
        const on = ui.torch.getAttribute('aria-pressed') !== 'true';
        try { await setTorch(stream, on); ui.torch.setAttribute('aria-pressed', String(on)); } catch (_) { toast(t('js.dev_torch_fail', 'Não foi possível ligar a lanterna.')); }
      });
    }
    let decoder;
    let probe = null;
    const build = async () => {
      decoder = await makeDecoder(formats);
      probe = null;
      // Wrong-mode warning: a second reader, with only the formats of the other mode, looks at
      // one frame every 4 — whatever it finds is not delivered, it only becomes the app's warning.
      if (wrongHint && !window.HubDeviceDecoder) {
        const other = Pure.complement(formats);
        if (other.length) probe = await makeDecoder(other).catch(() => null);
      }
    };
    try {
      await build();
    } catch (_) {
      ui.status.textContent = t('js.dev_decoder_fail', 'Não foi possível carregar o leitor de códigos.');
      return;
    }
    // Switch inside the camera: changes the formats (and the frame) without closing the video.
    if (ui.seg) {
      ui.seg.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-mode]');
        if (!b) return;
        const i = Pure.modeIndex(modes, b.dataset.mode);
        if (i === modeIdx) return;
        modeIdx = i;
        formats = modes[i].formats;
        wrongHint = modes[i].hint || opts.wrongHint;
        ui.seg.querySelectorAll('[data-mode]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        ui.host.dataset.formats = formats.join(',');
        ui.host.dataset.mode = modes[i].key;
        if (ui.aim) ui.aim.classList.toggle('hub-camera-aim-square', Pure.is2d(formats));
        swapping = true;
        try { await build(); } catch (_) { /* keeps the previous one */ }
        swapping = false;
        ui.status.textContent = fmt(t('js.dev_scan_mode', 'Lendo: {m}'), { m: modes[i].label });
        opts.onMode && opts.onMode(modes[i].key);
        prefs();
      });
    }
    ui.host.dataset.decoder = decoder.kind;
    ui.status.textContent = opts.continuous ? t('js.dev_scan_continuous', 'Leitura contínua: aponte para cada código. Toque em Fechar ao terminar.') : t('js.dev_scan_hint', 'Centralize o código no quadro.');
    let busy = false;
    let swapping = false;
    let frame = 0;
    let lastWrong = 0;
    const wrong = () => {
      const now = Date.now();
      if (now - lastWrong < 1500 || !wrongHint) return;
      lastWrong = now;
      ui.status.textContent = wrongHint;
      ui.host.dataset.wrong = String((Number(ui.host.dataset.wrong) || 0) + 1);
      vibrate('error');
    };
    const tick = async () => {
      if (stop) return;
      if (!busy && !swapping && !held && Date.now() >= holdUntil && ui.video.readyState >= 2) {
        busy = true;
        let r = null;
        const roi = frameOf();
        // No stage measurement (layer without CSS), the whole frame.
        const src = roi ? crop : ui.video;
        try { r = await decoder.decode(src, roi ? roi.aim : null); } catch (_) { r = null; }
        if (r && wrongHint && Pure.outside(r.format, formats)) { r = null; wrong(); }
        if (!r && probe && frame++ % 4 === 3) {
          let w = null;
          try { w = await probe.decode(src, roi ? roi.aim : null); } catch (_) { w = null; }
          if (w && w.text) wrong();
        }
        busy = false;
        const now = Date.now();
        if (r && r.text && !held && now >= holdUntil && !Pure.duplicate(lastRead, r.text, now, opts.continuous ? dedupMs() : 0)) {
          lastRead = { code: r.text, at: now };
          count++;
          ui.host.dataset.reads = String(count);
          ui.status.textContent = fmt(t('js.dev_read', 'Lido: {code}'), { code: r.text });
          if (!opts.continuous) vibrate('ok');
          const closeNow = opts.onRead(r.text, r.format || '');
          if (closeNow || !opts.continuous) { done(); return; }
        }
      }
      setTimeout(tick, 120);
    };
    tick();
  }

  /** Puts the code read into the field and submits the form like a keyboard Enter.
   *  `fmtTarget` (optional) receives the format read only during this submission. */
  function deliver(target, code, submit, fmt, fmtTarget) {
    if (fmtTarget) fmtTarget.value = fmt || '';
    target.value = code;
    target.dispatchEvent(new Event('input', { bubbles: true }));
    target.dispatchEvent(new Event('change', { bubbles: true }));
    const form = target.form;
    if (submit && form && !form.hasAttribute('data-hub-scan-nosubmit')) {
      if (form.requestSubmit) form.requestSubmit(); else form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    }
    if (fmtTarget) fmtTarget.value = ''; // the next reading from the physical reader comes without a format
  }
  Object.assign(P, { scanFeedback, openCamera, cameraError, stopStream, torchCapable, setTorch,
    cameraLayer, openScanner, deliver });
})();
