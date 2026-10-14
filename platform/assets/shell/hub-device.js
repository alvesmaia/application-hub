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
  // Private scope shared with the hub-device-*.js part files loaded right after this one;
  // removed from window once the last part has run.
  const P = (window.__hubDeviceParts = {});

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
  Object.assign(P, { FORMATS, DEFAULT_FORMATS, Pure, app, fragPrefix, inScope, appRoot, t, fmt,
    toast, ref, ownPath, uuid, audioCtx, beep, vibrate, feedback });
})();
