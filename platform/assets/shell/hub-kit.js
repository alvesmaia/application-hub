/* hub-kit.js — declarative `data-hub-*` behaviors for the apps (wave 12, item 2.1).
 *
 * By default an app does NOT run its own JavaScript (the app page CSP only accepts the hub's
 * scripts and the rare app scripts approved by the administration, with SRI). Everything the
 * apps used to do with JS becomes an attribute interpreted here — full documentation with
 * examples in docs/CONTRATO-APP.md, §25:
 *
 *   partial update       data-hub-get / data-hub-post="/apps/{app}/…", data-hub-target="#id",
 *                        data-hub-swap="outerHTML|innerHTML|beforeend|afterbegin|none",
 *                        data-hub-trigger="click|change|submit|input|load|every 5s",
 *                        data-hub-include="#form" (fields sent along)
 *   confirmation         data-hub-confirm="Text"   (links, buttons, forms)
 *   modal                data-hub-modal="#template-id"; inside: data-hub-close;
 *                        <form data-hub-dismiss data-hub-toast="…"> closes without sending
 *   table                <table data-hub-table data-hub-page-size="10">, <th data-hub-sort>,
 *                        <input data-hub-filter="#tabela|#lista" data-hub-filter-empty="#vazio">
 *   upload with progress <form enctype="multipart/form-data"> + [data-hub-progress]
 *                        (<progress> or text) — handled by hub.js
 *   charts               data-hub-chart='{"type":"bar|line|area|donut",…}' and
 *                        data-hub-charts='[TimeSeriesChart config]' (pre-rendered chart)
 *   toast                data-hub-toast="Message" (click) or X-Hub-Toast header in the response
 *   copy                 data-hub-copy="text" or data-hub-copy-from="#id"
 *   tabs                 [data-hub-tabs] > [data-hub-tab="#panel"]
 *   dependent selects    <select data-hub-depends="#parent" data-hub-options='{"v":[["id","label"]]}'>
 *   validation           <form data-hub-validate> + data-hub-msg-required / -type / -pattern /
 *                        -min / -max / -minlength / -maxlength on the fields
 *   mobile menu          data-hub-menu="open|close" (app sidebar on narrow screens)
 *   real time            data-hub-ws="/apps/{app}/ws/…" (WebSocket through the gateway) +
 *                        data-hub-ws-target="#id" / data-hub-ws-swap="outerHTML|…": messages
 *                        {"html":"…","target":"#id","swap":"…"} (or {"fragments":[…]}) go
 *                        through hub-safe.js; {"event":"name","data":…} becomes the `hub:ws` event;
 *                        reconnects with increasing wait; while the socket is down, the
 *                        data-hub-get + data-hub-trigger="every 2s" of the same element polls
 *                        data-hub-sse="/apps/{app}/…" (SSE through the gateway, via /_frag): same
 *                        messages in the `data:` of each event, -target/-swap, `hub:sse` event
 *
 * All received HTML goes through hub-safe.js; URLs of data-hub-get/post are only valid inside
 * the app itself (`/apps/{app}/…`, fetched through the gateway at /_frag); texts are inserted via textContent. */
(function () {
  'use strict';
  const Pure = {
    /** `every 5s` / `every 500ms` → ms (minimum 1 s); any other trigger → null. */
    everyMs(trigger) {
      const m = /(?:^|\s)every\s+(\d+(?:\.\d+)?)(ms|s)\b/.exec(String(trigger || ''));
      if (!m) return null;
      const v = Number(m[1]) * (m[2] === 's' ? 1000 : 1);
      return Math.max(1000, Math.round(v));
    },
    /** Default trigger for the element. */
    defaultTrigger(tag, type) {
      if (tag === 'form') return 'submit';
      if (tag === 'select' || (tag === 'input' && ['checkbox', 'radio', 'file', 'date', 'range'].includes(type))) return 'change';
      if (tag === 'input' || tag === 'textarea') return 'input';
      return 'click';
    },
    /** Text normalized for filtering (lowercase, no accents). */
    fold(s) {
      return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    },
    /** Cell comparison for sorting (`num`, `date` dd/mm/yyyy or pt-BR text). */
    compare(a, b, kind) {
      if (kind === 'num') {
        const n = (s) => { const v = parseFloat(String(s).replace(/[^\d,.-]/g, '').replace(/\.(?=\d{3}(\D|$))/g, '').replace(',', '.')); return Number.isFinite(v) ? v : -Infinity; };
        return n(a) - n(b);
      }
      if (kind === 'date') {
        const d = (s) => { const m = /(\d{2})\/(\d{2})\/(\d{4})/.exec(s); return m ? `${m[3]}${m[2]}${m[1]}` : String(s); };
        return d(a) < d(b) ? -1 : d(a) > d(b) ? 1 : 0;
      }
      return String(a).localeCompare(String(b), 'pt-BR', { sensitivity: 'base', numeric: true });
    },
    /** Element reference in a data-hub-* attribute: only an id (`id` or `#id`,
     *  `^[A-Za-z][\w-]*$`). No selector built from app text (wave 12, H1). */
    idToken(v) {
      const t = String(v == null ? '' : v).trim().replace(/^#/, '');
      return /^[A-Za-z][\w-]*$/.test(t) ? t : null;
    },
    /** WebSocket reconnection wait (ms): 1 s, 2 s, 4 s… up to 30 s, ±25 %. */
    wsBackoff(attempt, rand) {
      const base = Math.min(30000, 1000 * 2 ** Math.max(0, Math.min(5, attempt | 0)));
      const r = typeof rand === 'number' ? rand : Math.random();
      return Math.round(base * (0.75 + 0.5 * r));
    },
    /** App socket path on the gateway: `/{app}/ws` or `/{app}/ws/…`. */
    isWsPath(p) {
      return /^\/[a-z0-9][a-z0-9-]*\/ws(?:[/?]|$)/.test(String(p || ''));
    },
    /** Text received on the socket → list of messages ({html,target,swap} | {event,data} |
     *  {text,target}); text that is not JSON is HTML for the default target. Max. 20 per frame. */
    wsMessages(data) {
      const s = String(data == null ? '' : data);
      let m;
      try { m = JSON.parse(s); } catch (_) { return s.trim() ? [{ html: s }] : []; }
      if (!m || typeof m !== 'object') return [];
      const list = Array.isArray(m) ? m : Array.isArray(m.fragments) ? m.fragments : [m];
      return list.filter((x) => x && typeof x === 'object').slice(0, 20);
    },
    /** Pages of `total` items in blocks of `size`. */
    pages(total, size) {
      return size > 0 ? Math.max(1, Math.ceil(total / size)) : 1;
    },
  };
  if (typeof module !== 'undefined' && module.exports && typeof document === 'undefined') {
    module.exports = Pure;
    return;
  }
  if (window.HubKit) return;
  // Private scope shared with hub-kit-mount.js, loaded right after this one; removed from
  // window once hub-kit-mount.js has run.
  const P = (window.__hubKitParts = {});
  const mount = (...args) => P.mount(...args); // defined in a later part
  const me = document.querySelector('script[data-hub-app]');
  // hub-kit loads BEFORE hub.js (which carries data-hub-app): the app id is read right away.
  const curApp = () => { const m = document.querySelector('script[data-hub-app]'); return (m && m.dataset.hubApp) || document.documentElement.dataset.hubApp || ''; };
  const frag = (me && me.dataset.hubFrag) || '/_frag';
  const Safe = window.HubSafe;
  const SCOPE = '#app-root, [data-hub-layer]';
  const inScope = (el) => Boolean(el && el.closest && el.closest(SCOPE));
  const appRoot = () => document.getElementById('app-root');
  /** Element named in a data-hub-* attribute: a bare id, inside the app content (or of
   *  a hub layer opened by it) and never #app-root itself. */
  function ref(value, opts) {
    const id = Pure.idToken(value);
    if (!id) { if (value) console.warn('data-hub-*: use só um id (#id)', value); return null; }
    const el = document.getElementById(id);
    const root = appRoot();
    if (!el || !root) return null;
    if (el === root) return null;
    if (root.contains(el)) return el;
    const layer = el.closest('[data-hub-layer]');
    if (layer && (!opts || !opts.appOnly)) return el;
    return null;
  }

  function t(k, fallback) {
    try {
      const d = JSON.parse(document.getElementById('hub-i18n')?.textContent || '{}');
      return d[k] || fallback;
    } catch (_) { return fallback; }
  }

  // ---- toasts ----
  let toastTimer = 0;
  function toast(message) {
    let el = document.getElementById('toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast';
      el.className = 'toast';
      el.setAttribute('role', 'status');
      document.body.append(el);
    }
    el.textContent = String(message || '').slice(0, 300);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
  }

  // ---- the app's own paths ----
  function ownPath(url) {
    if (!url) return null;
    let u;
    try { u = new URL(url, location.href); } catch (_) { return null; }
    if (u.origin !== location.origin) return null;
    // Public `/apps/{app}/…` (or the old `/{app}/…`) → path on the gateway `/{app}/…`.
    const p = u.pathname.startsWith('/apps/') ? u.pathname.slice(5) : u.pathname;
    const app = curApp();
    if (!app || (p !== `/${app}` && !p.startsWith(`/${app}/`))) return null;
    return p + u.search;
  }

  function idem() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
  }

  // ---- safe insertion ----
  function insert(target, html, swap) {
    const frag = Safe.sanitize(html, { app: curApp() });
    const nodes = [...frag.childNodes].filter((n) => n.nodeType === 1);
    switch (swap) {
      case 'none': return [];
      case 'innerHTML': target.replaceChildren(frag); mount(target); return [target];
      case 'beforeend': target.append(frag); break;
      case 'afterbegin': target.prepend(frag); break;
      default: target.replaceWith(frag);
    }
    nodes.forEach((n) => mount(n));
    return nodes;
  }

  function busy(el, on) {
    el.toggleAttribute('aria-busy', on);
    if ('disabled' in el && el.tagName !== 'FORM') el.disabled = on;
  }

  async function request(el, evt) {
    const method = el.hasAttribute('data-hub-post') ? 'POST' : 'GET';
    const path = ownPath(el.getAttribute(method === 'POST' ? 'data-hub-post' : 'data-hub-get'));
    if (!path) { console.warn('data-hub-get/post fora do app ignorado', el); return; }
    const sel = el.getAttribute('data-hub-target');
    const target = sel ? ref(sel) : el;
    if (!target || !inScope(target)) return;
    const swap = el.getAttribute('data-hub-swap') || 'outerHTML';
    let url = path;
    let body = null;
    const data = new FormData();
    const form = el.tagName === 'FORM' ? el : el.form || null;
    if (form) for (const [k, v] of new FormData(form, evt && evt.submitter)) data.append(k, v);
    const inc = ref(el.getAttribute('data-hub-include'));
    if (inc) {
      if (inc.tagName === 'FORM') for (const [k, v] of new FormData(inc)) data.append(k, v);
      else if (inc.name) data.append(inc.name, inc.value);
    }
    if (!form && el.name && 'value' in el) data.append(el.name, el.value);
    if (method === 'GET') {
      const q = new URLSearchParams();
      for (const [k, v] of data) if (typeof v === 'string') q.append(k, v);
      const qs = q.toString();
      if (qs) url += (url.includes('?') ? '&' : '?') + qs;
    } else {
      const hasFile = [...data.values()].some((v) => v instanceof File);
      body = hasFile ? data : new URLSearchParams([...data].filter(([, v]) => typeof v === 'string'));
    }
    busy(el, true);
    try {
      const headers = { accept: 'text/html', 'x-hub-request': '1' };
      if (method === 'POST') headers['Idempotency-Key'] = idem();
      const res = await fetch(`${frag}${url}`, { method, body, headers, credentials: 'same-origin' });
      if (res.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`; return; }
      const html = await res.text();
      const note = res.headers.get('x-hub-toast');
      if (target.isConnected && html.trim()) insert(target, html, swap);
      else if (target.isConnected && swap !== 'none' && !res.ok) toast(t('js.kit_error', 'Não foi possível concluir a operação.'));
      if (note) { let msg = note; try { msg = decodeURIComponent(note); } catch (_) { /* raw text */ } toast(msg); }
      else if (res.ok && el.dataset.hubToast) toast(el.dataset.hubToast);
    } catch (_) {
      toast(t('js.kit_offline', 'Sem conexão com a aplicação. Tente de novo.'));
    } finally {
      busy(el, false);
    }
  }

  // ---- confirmation ----
  function dialog(build, onClose) {
    const host = document.createElement('div');
    host.className = 'overlay';
    host.setAttribute('data-hub-layer', '');
    const box = document.createElement('section');
    box.className = 'modal';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    host.append(box);
    const prev = document.activeElement;
    const close = (v) => {
      host.remove();
      document.removeEventListener('keydown', esc, true);
      try { prev && prev.focus && prev.focus(); } catch (_) { /* none */ }
      onClose && onClose(v);
    };
    // Clicking outside does not close (what was typed would be lost); Esc only closes without changes.
    const esc = (e) => { if (e.key === 'Escape') { e.preventDefault(); if (!host.dataset.dirty) close(false); } };
    document.addEventListener('keydown', esc, true);
    host.addEventListener('input', () => { host.dataset.dirty = '1'; }); host.addEventListener('change', () => { host.dataset.dirty = '1'; });
    host.addEventListener('click', (e) => { if (e.target.closest('[data-hub-close], .modal-close, .modal-cancel')) { e.preventDefault(); close(false); } });
    build(box, close);
    document.body.append(host);
    const focus = box.querySelector('[autofocus], input:not([type=hidden]), select, textarea, button');
    if (focus) focus.focus();
    return { host, close };
  }

  function confirmDialog(text) {
    return new Promise((resolve) => {
      dialog((box, close) => {
        box.classList.add('hub-confirm');
        const h = document.createElement('h2');
        h.id = `hub-confirm-${Date.now()}`;
        h.textContent = t('js.kit_confirm_title', 'Confirmar');
        box.setAttribute('aria-labelledby', h.id);
        const p = document.createElement('p');
        p.className = 'modal-copy';
        p.textContent = String(text).slice(0, 500);
        const actions = document.createElement('div');
        actions.className = 'modal-actions';
        const no = document.createElement('button');
        no.type = 'button';
        no.className = 'secondary-button';
        no.setAttribute('data-hub-close', '');
        no.textContent = t('js.kit_cancel', 'Cancelar');
        const yes = document.createElement('button');
        yes.type = 'button';
        yes.className = 'primary-button';
        yes.setAttribute('data-hub-confirm-ok', '');
        yes.textContent = t('js.kit_ok', 'Confirmar');
        yes.addEventListener('click', () => close(true));
        actions.append(no, yes);
        box.append(h, p, actions);
      }, resolve);
    });
  }

  const confirmed = new WeakSet();
  /** Holds the event until the user confirms; once confirmed, repeats the action once. */
  function guard(e, el, replay, text) {
    if (confirmed.has(el)) { confirmed.delete(el); return false; }
    e.preventDefault();
    e.stopImmediatePropagation();
    confirmDialog(text || el.getAttribute('data-hub-confirm')).then((ok) => {
      if (!ok) return;
      confirmed.add(el);
      replay();
    });
    return true;
  }

  // ---- modais ----
  function openModal(trigger) {
    const tpl = ref(trigger.getAttribute('data-hub-modal'), { appOnly: true });
    if (!tpl || tpl.tagName !== 'TEMPLATE') return;
    dialog((box) => {
      const labelled = tpl.getAttribute('data-hub-modal-labelledby');
      if (labelled) box.setAttribute('aria-labelledby', labelled);
      box.append(document.importNode(tpl.content, true));
      mount(box);
    });
  }

  // ---- form validation ----
  const MSG = [['valueMissing', 'required'], ['typeMismatch', 'type'], ['patternMismatch', 'pattern'], ['rangeUnderflow', 'min'], ['rangeOverflow', 'max'], ['tooShort', 'minlength'], ['tooLong', 'maxlength'], ['stepMismatch', 'step'], ['badInput', 'type']];
  function fieldMessage(f) {
    for (const [k, attr] of MSG) if (f.validity[k]) return f.getAttribute(`data-hub-msg-${attr}`) || f.validationMessage;
    return f.validationMessage;
  }
  function showErrors(form) {
    let first = null;
    form.querySelectorAll('input, select, textarea').forEach((f) => {
      if (!f.willValidate) return;
      const id = `${f.id || f.name || 'campo'}-hub-erro`;
      let msg = form.querySelector(`[data-hub-error-for="${CSS.escape(f.name || f.id)}"]`);
      if (f.checkValidity()) {
        f.removeAttribute('aria-invalid');
        if (msg) msg.remove();
        return;
      }
      if (!msg) {
        msg = document.createElement('small');
        msg.className = 'hub-field-error';
        msg.setAttribute('data-hub-error-for', f.name || f.id);
        msg.id = id;
        f.after(msg);
      }
      msg.textContent = fieldMessage(f);
      f.setAttribute('aria-invalid', 'true');
      f.setAttribute('aria-describedby', msg.id);
      first = first || f;
    });
    if (first) first.focus();
    return !first;
  }
  Object.assign(P, { Pure, frag, inScope, appRoot, ref, t, toast, ownPath, insert, request,
    confirmDialog, guard, openModal, showErrors });
})();
