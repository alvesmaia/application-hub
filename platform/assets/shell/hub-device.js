/* hub-device.js — device features as declarative `data-hub-*` behaviors
 * (wave 13; hub JavaScript, the app still has no script of its own — CONTRATO-APP §13/§25).
 * Kept apart from hub-kit.js only because of its size: it is loaded on the same pages as
 * the apps and follows the same rules (references only by id inside the app, texts via
 * textContent, app HTML through the sanitizer, URLs only from the app's own /_frag).
 *
 *   camera reading       data-hub-scan (button) + data-hub-scan-target="#field"
 *                        [data-hub-scan-continuous] [data-hub-scan-formats="ean_13,qr_code,…"]
 *                        [data-hub-scan-format-target="#field"] (receives the format read)
 *                        [data-hub-scan-wrong-hint="Text"] (code of the other type → warning)
 *                        square aiming frame only with 2D formats (qr_code/data_matrix)
 *                        switching INSIDE the camera: data-hub-scan-formats="lin…|qr_code,…"
 *                        (groups separated by |) + data-hub-scan-modes="barras|qr" +
 *                        data-hub-scan-mode-labels="Código de barras|QR" + data-hub-scan-mode
 *                        (current) [+ data-hub-scan-mode-target="#field": receives the key and a
 *                        `change`]; data-hub-scan-wrong-hint also accepts one text per group
 *                        data-hub-scan-dedup="ms" (same code ignored within the window; default 1500)
 *                        on the response: [data-hub-scan-next] (app button) → the camera stops and
 *                        shows "Próximo"; data-hub-scan-cooldown="ms" → wait before reading again
 *                        reader INSIDE the camera: data-hub-scan-reader="interativo|automatico"
 *                        + data-hub-scan-cooldown="ms" (current pause, on the button) +
 *                        [data-hub-scan-cooldowns="0|1000|…"] + [data-hub-scan-prefs="/apps/…"]
 *                        (POST mode/reader/pause on every change in the top bar of the layer)
 *                        only the aiming frame is read (video crop + code ≥ 90 % inside)
 *                        native BarcodeDetector; without it, @zxing/library (pure JS, served from
 *                        /assets/vendor/, loaded only when needed — no 'wasm-unsafe-eval')
 *   keyboard-wedge reader data-hub-wedge (field): fast burst + Enter/Tab = reading; repetition
 *                        within data-hub-wedge-debounce ms (default 800) is discarded
 *   feedback             data-hub-feedback="beep vibrate flash" (container) + element
 *                        data-hub-feedback-result="ok|warn|error" in the response (or X-Hub-Feedback
 *                        header on offline queue submissions)
 *   photo                data-hub-capture="#input-file" [data-hub-capture-preview="#id"]
 *                        camera → JPEG ≤ 1600 px (0.8) → file put in the form's <input type=file>
 *   offline queue        <form method=post data-hub-offline-queue="Label">: without network, the
 *                        submission goes to IndexedDB and is resent in order, with the SAME
 *                        Idempotency-Key, when the connection comes back; [data-hub-offline-status],
 *                        [data-hub-offline-text], [data-hub-offline-errors] show the queue
 *   installation (PWA)   data-hub-install (button), data-hub-install-ios / -done / -unavailable
 *   diagnostics          [data-hub-diag] > [data-hub-diag-check="secure|online|pwa|camera|torch|
 *                        scan|wedge|beep|vibrate|capture|storage"] + [data-hub-diag-status],
 *                        [data-hub-diag-run], [data-hub-diag-wedge], [data-hub-diag-info],
 *                        [data-hub-diag-copy]
 *   printing             data-hub-print (button)
 */
(function () {
  'use strict';
  /** Accepted formats (BarcodeDetector names) → name in ZXing.BarcodeFormat. */
  const FORMATS = { ean_13: 'EAN_13', ean_8: 'EAN_8', upc_a: 'UPC_A', upc_e: 'UPC_E', code_128: 'CODE_128', code_39: 'CODE_39', itf: 'ITF', codabar: 'CODABAR', qr_code: 'QR_CODE', data_matrix: 'DATA_MATRIX', pdf417: 'PDF_417' };
  const DEFAULT_FORMATS = ['ean_13', 'ean_8', 'upc_a', 'code_128', 'code_39', 'qr_code', 'data_matrix'];
  const Pure = {
    FORMATS,
    /** Only 2D formats (QR/DataMatrix/PDF417)? → square frame. */
    is2d(formats) {
      const two = ['qr_code', 'data_matrix', 'pdf417', 'aztec'];
      return formats.length > 0 && formats.every((f) => two.includes(f));
    },
    /** Formats of the "other mode" (2D ↔ linear), to recognize and warn. */
    complement(formats) {
      const two = ['qr_code', 'data_matrix'];
      const lin = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf'];
      return Pure.is2d(formats) ? lin : two.filter((f) => !formats.includes(f));
    },
    /** Format read outside the requested ones? (the native one sometimes returns another) */
    outside(fmt, formats) {
      return Boolean(fmt) && fmt in FORMATS && !formats.includes(fmt);
    },
    /** `"ean_13, qr_code,xyz"` → valid formats (empty = default). */
    parseFormats(v) {
      const out = String(v || '').split(/[\s,]+/).map((s) => s.trim().toLowerCase()).filter((s) => s in FORMATS);
      return out.length ? [...new Set(out)] : DEFAULT_FORMATS.slice();
    },
    /** Groups of the switch inside the camera: `formats` with `|` + keys and labels in the
     *  same order → [{key, label, formats, hint}] (fewer than 2 valid groups → []). */
    scanModes(formats, keys, labels, hints) {
      const split = (v) => String(v == null ? '' : v).split('|').map((x) => x.trim());
      const fs = split(formats);
      if (fs.length < 2) return [];
      const ks = split(keys);
      const ls = split(labels);
      const hs = split(hints);
      const out = [];
      fs.forEach((f, i) => {
        const list = String(f).split(/[\s,]+/).map((x) => x.trim().toLowerCase()).filter((x) => x in FORMATS);
        const key = /^[\w-]{1,32}$/.test(ks[i] || '') ? ks[i] : `m${i}`;
        if (list.length) out.push({ key, label: (ls[i] || key).slice(0, 40), formats: [...new Set(list)], hint: (hs.length > 1 ? hs[i] : hs[0] || '').slice(0, 160) });
      });
      return out.length >= 2 ? out : [];
    },
    /** Index of mode `key` (or 0). */
    modeIndex(modes, key) {
      const i = modes.findIndex((m) => m.key === key);
      return i < 0 ? 0 : i;
    },
    /** Camera pause after a reading (`data-hub-scan-cooldown`): 0–10000 ms. */
    cooldownMs(v) {
      const n = Math.round(Number(v));
      return Number.isFinite(n) && n > 0 ? Math.min(n, 10000) : 0;
    },
    /** Keyboard-wedge burst: ≥ `min` keys with average interval ≤ `maxAvg` ms. */
    isBurst(times, min = 4, maxAvg = 35) {
      if (!Array.isArray(times) || times.length < min) return false;
      const gaps = times.slice(1).map((t, i) => t - times[i]);
      const avg = gaps.reduce((a, b) => a + b, 0) / gaps.length;
      return avg <= maxAvg && Math.max(...gaps) <= maxAvg * 4;
    },
    /** Same reading within the window? (`last` = {code, at}). */
    duplicate(last, code, now, windowMs) {
      return Boolean(last && last.code === code && now - last.at < windowMs);
    },
    /** Dimensions reduced to at most `max` px on the longer side (never enlarged). */
    fit(w, h, max = 1600) {
      const s = Math.min(1, max / Math.max(w || 1, h || 1));
      return [Math.max(1, Math.round(w * s)), Math.max(1, Math.round(h * s))];
    },
    /** Value of `data-hub-feedback-result` / `X-Hub-Feedback` → type (or null). */
    feedbackKind(v) {
      const k = String(v || '').trim().toLowerCase();
      return ['ok', 'warn', 'error', 'queued'].includes(k) ? k : null;
    },
    /** Tones (Hz, ms) and vibration per type. */
    tones(kind) {
      return { ok: [[1760, 90]], warn: [[1100, 80], [0, 60], [1100, 80]], error: [[330, 160], [0, 50], [250, 260]], queued: [[880, 70], [0, 40], [1320, 70]] }[kind] || [];
    },
    vibration(kind) {
      return { ok: [60], warn: [60, 60, 60], error: [220, 80, 220], queued: [40, 40, 40] }[kind] || [];
    },
    /** Response to a queue resend: 'done' (leaves the queue), 'error' (goes to the error list —
     *  conflict/validation), 'retry' (network/server: try later), 'login'. */
    replayOutcome(status) {
      if (status === 401) return 'login';
      if (status >= 200 && status < 400) return 'done';
      if (status === 0 || status === 408 || status === 425 || status === 429 || status >= 500) return 'retry';
      return 'error';
    },
    /** Submission that goes straight to the queue (without trying the network)? */
    queueFirst(online) { return online === false; },
    isIOS(ua, touchPoints) {
      return /iphone|ipad|ipod/i.test(String(ua || '')) || (/macintosh/i.test(String(ua || '')) && (touchPoints || 0) > 1);
    },
    /** Text of the diagnostics report. */
    report(rows, info, meta) {
      const lines = [`Diagnóstico do aparelho — ${meta && meta.when ? meta.when : ''}`.trim()];
      for (const r of rows) lines.push(`- ${r.label}: ${r.result}${r.detail ? ` (${r.detail})` : ''}`);
      if (info && info.length) { lines.push(''); for (const [k, v] of info) lines.push(`${k}: ${v}`); }
      if (meta && meta.ua) { lines.push(''); lines.push(`Navegador: ${meta.ua}`); }
      return lines.join('\n');
    },
    /** Text of an HTML response for the error list (no tags, up to 160). */
    plain(html) {
      return String(html || '').replace(/<style[\s\S]*?<\/style>/gi, ' ').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim().slice(0, 160);
    },
    /** Aiming frame on the stage (CSS px): barcode = wide rectangle (80 % × 30 %),
     *  QR/2D = square with 60 % of the smaller side; centered. */
    aimRect(stageW, stageH, square) {
      const w = square ? Math.min(stageW, stageH) * 0.6 : stageW * 0.8;
      const h = square ? w : stageH * 0.3;
      return { x: (stageW - w) / 2, y: (stageH - h) / 2, w, h };
    },
    /** `object-fit: cover`: scale and offset (CSS px) of the video `vw×vh` (video px) on a
     *  stage `sw×sh`. DPR is not involved: stage and frame are measured in CSS px and the crop
     *  comes out in video px (the camera's real resolution), so the ratio cancels out. */
    coverMap(vw, vh, sw, sh) {
      const scale = Math.max(sw / vw, sh / vh);
      return { scale, offX: (sw - vw * scale) / 2, offY: (sh - vh * scale) / 2 };
    },
    /** Region of interest in video px: `aim` (CSS px on the stage) → {sx, sy, sw, sh} (crop,
     *  with a `margin` on each side, limited to the video) + `aim` in CROP coordinates
     *  (to check that the code fell inside the frame). */
    roi(vw, vh, stageW, stageH, aim, margin = 0.1) {
      if (!vw || !vh || !stageW || !stageH || !aim || !aim.w || !aim.h) return null;
      const m = Pure.coverMap(vw, vh, stageW, stageH);
      const ax = (aim.x - m.offX) / m.scale;
      const ay = (aim.y - m.offY) / m.scale;
      const aw = aim.w / m.scale;
      const ah = aim.h / m.scale;
      const x0 = Math.max(0, Math.floor(ax - aw * margin));
      const y0 = Math.max(0, Math.floor(ay - ah * margin));
      const x1 = Math.min(vw, Math.ceil(ax + aw * (1 + margin)));
      const y1 = Math.min(vh, Math.ceil(ay + ah * (1 + margin)));
      if (x1 - x0 < 2 || y1 - y0 < 2) return null;
      return { sx: x0, sy: y0, sw: x1 - x0, sh: y1 - y0, aim: { x: ax - x0, y: ay - y0, w: aw, h: ah } };
    },
    /** Box of points [{x,y}] → {x,y,w,h} (the line of a 1D code gets 1 px of height). */
    boxOf(points) {
      const ps = (points || []).filter((p) => p && Number.isFinite(p.x) && Number.isFinite(p.y));
      if (!ps.length) return null;
      const xs = ps.map((p) => p.x);
      const ys = ps.map((p) => p.y);
      const x = Math.min(...xs);
      const y = Math.min(...ys);
      return { x, y, w: Math.max(1, Math.max(...xs) - x), h: Math.max(1, Math.max(...ys) - y) };
    },
    /** Fraction of the area of `box` inside `rect` (0–1). */
    insideFraction(box, rect) {
      if (!box || !rect || !(box.w > 0) || !(box.h > 0)) return 0;
      const ix = Math.max(0, Math.min(box.x + box.w, rect.x + rect.w) - Math.max(box.x, rect.x));
      const iy = Math.max(0, Math.min(box.y + box.h, rect.y + rect.h) - Math.max(box.y, rect.y));
      return (ix * iy) / (box.w * box.h);
    },
    /** First result with the code (cornerPoints / boundingBox) ≥ `min` inside `aim`;
     *  a result without position is accepted (the crop already limited the image to the frame). */
    pickInside(results, aim, min = 0.9) {
      for (const r of results || []) {
        if (!r) continue;
        const bb = r.boundingBox;
        const box = Pure.boxOf(r.cornerPoints) || (bb && bb.width > 0 ? { x: bb.x, y: bb.y, w: bb.width, h: Math.max(1, bb.height) } : null);
        if (!box || !aim || Pure.insideFraction(box, aim) >= min) return r;
      }
      return null;
    },
    /** Offered pauses (`data-hub-scan-cooldowns="0|1000|…"`, ms, 0–10000). */
    cooldownList(v) {
      const out = String(v == null ? '' : v).split(/[|,\s]+/).filter(Boolean).map((x) => Math.round(Number(x))).filter((n) => Number.isFinite(n) && n >= 0 && n <= 10000);
      return out.length ? [...new Set(out)] : [0, 1000, 2000, 3000, 5000];
    },
    /** Reader mode: `interativo` | `automatico` (other value → null = no selector). */
    readerMode(v) {
      const k = String(v || '').trim().toLowerCase();
      return k === 'interativo' || k === 'automatico' ? k : null;
    },
    /** Luminance (Uint8ClampedArray RGBA → Uint8ClampedArray gray) for ZXing. */
    luminance(rgba, w, h) {
      const out = new Uint8ClampedArray(w * h);
      for (let i = 0, j = 0; j < out.length; i += 4, j++) out[j] = (rgba[i] * 299 + rgba[i + 1] * 587 + rgba[i + 2] * 114) / 1000;
      return out;
    },
  };
  if (typeof module !== 'undefined' && module.exports && typeof document === 'undefined') {
    module.exports = Pure;
    return;
  }
  if (window.HubDevice) return;

  const me = document.querySelector('script[data-hub-app]');
  const app = me ? me.dataset.hubApp : '';
  const fragPrefix = (me && me.dataset.hubFrag) || '/_frag';
  const SCOPE = '#app-root, [data-hub-layer]';
  const inScope = (el) => Boolean(el && el.closest && el.closest(SCOPE));
  const appRoot = () => document.getElementById('app-root');
  function t(k, fallback) {
    try {
      const d = JSON.parse(document.getElementById('hub-i18n')?.textContent || '{}');
      return d[k] || fallback;
    } catch (_) { return fallback; }
  }
  const fmt = (s, vars) => String(s).replace(/\{(\w+)\}/g, (_, k) => (vars && k in vars ? String(vars[k]) : `{${k}}`));
  const toast = (m) => (window.HubKit ? window.HubKit.toast(m) : null);
  /** Element by id inside the app content (same rule as hub-kit). */
  function ref(value) {
    const id = String(value == null ? '' : value).trim().replace(/^#/, '');
    if (!/^[A-Za-z][\w-]*$/.test(id)) return null;
    const el = document.getElementById(id);
    const root = appRoot();
    if (!el || !root || el === root) return null;
    return root.contains(el) || el.closest('[data-hub-layer]') ? el : null;
  }
  function ownPath(url) {
    if (!url || !app) return null;
    let u;
    try { u = new URL(url, location.href); } catch (_) { return null; }
    if (u.origin !== location.origin) return null;
    // Public `/apps/{app}/…` (or the old `/{app}/…`) → path on the gateway `/{app}/…`.
    const p = u.pathname.startsWith('/apps/') ? u.pathname.slice(5) : u.pathname;
    if (p !== `/${app}` && !p.startsWith(`/${app}/`)) return null;
    return p + u.search;
  }
  const uuid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`);

  // ---- feedback: beep, vibration, flash ------------------------------------------------------
  let audio = null;
  function audioCtx() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    if (!audio) { try { audio = new AC(); } catch (_) { return null; } }
    if (audio.state === 'suspended') audio.resume().catch(() => {});
    return audio;
  }
  // Audio only after a user gesture (browser policy).
  const unlock = () => { audioCtx(); };
  document.addEventListener('pointerdown', unlock, { capture: true, once: true });
  document.addEventListener('keydown', unlock, { capture: true, once: true });
  function beep(kind) {
    const ctx = audioCtx();
    if (!ctx) return false;
    let at = ctx.currentTime + 0.01;
    for (const [hz, ms] of Pure.tones(kind)) {
      if (hz > 0) {
        const o = ctx.createOscillator();
        const g = ctx.createGain();
        o.type = kind === 'error' ? 'square' : 'sine';
        o.frequency.value = hz;
        g.gain.setValueAtTime(0.0001, at);
        g.gain.exponentialRampToValueAtTime(0.25, at + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, at + ms / 1000);
        o.connect(g).connect(ctx.destination);
        o.start(at);
        o.stop(at + ms / 1000 + 0.02);
      }
      at += ms / 1000;
    }
    return ctx.state === 'running';
  }
  function vibrate(kind) {
    try { return Boolean(navigator.vibrate && navigator.vibrate(Pure.vibration(kind))); } catch (_) { return false; }
  }
  function flash(kind) {
    const el = document.createElement('div');
    el.className = `hub-flash hub-flash-${kind}`;
    el.setAttribute('aria-hidden', 'true');
    document.body.append(el);
    setTimeout(() => el.remove(), 450);
  }
  function feedback(kind, opts) {
    const k = Pure.feedbackKind(kind);
    if (!k) return;
    const want = String(opts == null ? 'beep vibrate flash' : opts);
    if (/beep/.test(want)) beep(k);
    if (/vibrate/.test(want)) vibrate(k);
    if (/flash/.test(want)) flash(k);
    document.dispatchEvent(new CustomEvent('hub:feedback', { detail: { kind: k } }));
  }
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
})();
