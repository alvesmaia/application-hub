/* hub-kit.js — comportamentos declarativos `data-hub-*` para os apps (onda 12, item 2.1).
 *
 * Por padrão um app NÃO roda JavaScript próprio (a CSP da página de app só aceita os
 * scripts do hub e os raros scripts de app aprovados pela administração, com SRI). Tudo o
 * que os apps faziam com JS vira atributo interpretado aqui — documentação completa com
 * exemplos em docs/CONTRATO-APP.md, §25:
 *
 *   atualização parcial  data-hub-get / data-hub-post="/apps/{app}/…", data-hub-target="#id",
 *                        data-hub-swap="outerHTML|innerHTML|beforeend|afterbegin|none",
 *                        data-hub-trigger="click|change|submit|input|load|every 5s",
 *                        data-hub-include="#form" (campos enviados junto)
 *   confirmação          data-hub-confirm="Texto"   (links, botões, formulários)
 *   modal                data-hub-modal="#template-id"; dentro: data-hub-close;
 *                        <form data-hub-dismiss data-hub-toast="…"> fecha sem enviar
 *   tabela               <table data-hub-table data-hub-page-size="10">, <th data-hub-sort>,
 *                        <input data-hub-filter="#tabela|#lista" data-hub-filter-empty="#vazio">
 *   upload com progresso <form enctype="multipart/form-data"> + [data-hub-progress]
 *                        (<progress> ou texto) — tratado pelo hub.js
 *   gráficos             data-hub-chart='{"type":"bar|line|area|donut",…}' e
 *                        data-hub-charts='[config TimeSeriesChart]' (gráfico pré-renderizado)
 *   toast                data-hub-toast="Mensagem" (clique) ou cabeçalho X-Hub-Toast na resposta
 *   copiar               data-hub-copy="texto" ou data-hub-copy-from="#id"
 *   abas                 [data-hub-tabs] > [data-hub-tab="#painel"]
 *   selects dependentes  <select data-hub-depends="#pai" data-hub-options='{"v":[["id","rótulo"]]}'>
 *   validação            <form data-hub-validate> + data-hub-msg-required / -type / -pattern /
 *                        -min / -max / -minlength / -maxlength nos campos
 *   menu móvel           data-hub-menu="open|close" (sidebar do app em telas estreitas)
 *   tempo real           data-hub-ws="/apps/{app}/ws/…" (WebSocket pelo gateway) +
 *                        data-hub-ws-target="#id" / data-hub-ws-swap="outerHTML|…": mensagens
 *                        {"html":"…","target":"#id","swap":"…"} (ou {"fragments":[…]}) entram
 *                        pelo hub-safe.js; {"event":"nome","data":…} vira o evento `hub:ws`;
 *                        reconecta com espera crescente; enquanto o socket cai, o
 *                        data-hub-get + data-hub-trigger="every 2s" do mesmo elemento faz polling
 *                        data-hub-sse="/apps/{app}/…" (SSE pelo gateway, via /_frag): mesmas
 *                        mensagens no `data:` de cada evento, -target/-swap, evento `hub:sse`
 *
 * Todo HTML recebido passa pelo hub-safe.js; URLs de data-hub-get/post só valem dentro do
 * próprio app (`/apps/{app}/…`, buscadas via gateway em /_frag); textos entram por textContent. */
(function () {
  'use strict';
  const Pure = {
    /** `every 5s` / `every 500ms` → ms (mínimo 1 s); outro gatilho → null. */
    everyMs(trigger) {
      const m = /(?:^|\s)every\s+(\d+(?:\.\d+)?)(ms|s)\b/.exec(String(trigger || ''));
      if (!m) return null;
      const v = Number(m[1]) * (m[2] === 's' ? 1000 : 1);
      return Math.max(1000, Math.round(v));
    },
    /** Gatilho padrão pelo elemento. */
    defaultTrigger(tag, type) {
      if (tag === 'form') return 'submit';
      if (tag === 'select' || (tag === 'input' && ['checkbox', 'radio', 'file', 'date', 'range'].includes(type))) return 'change';
      if (tag === 'input' || tag === 'textarea') return 'input';
      return 'click';
    },
    /** Texto normalizado para filtro (minúsculas, sem acento). */
    fold(s) {
      return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    },
    /** Comparação de células para ordenação (`num`, `date` dd/mm/aaaa ou texto pt-BR). */
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
    /** Referência a elemento em atributo data-hub-*: só um id (`id` ou `#id`,
     *  `^[A-Za-z][\w-]*$`). Nada de seletor montado com texto do app (onda 12, H1). */
    idToken(v) {
      const t = String(v == null ? '' : v).trim().replace(/^#/, '');
      return /^[A-Za-z][\w-]*$/.test(t) ? t : null;
    },
    /** Espera da reconexão do WebSocket (ms): 1 s, 2 s, 4 s… até 30 s, ±25 %. */
    wsBackoff(attempt, rand) {
      const base = Math.min(30000, 1000 * 2 ** Math.max(0, Math.min(5, attempt | 0)));
      const r = typeof rand === 'number' ? rand : Math.random();
      return Math.round(base * (0.75 + 0.5 * r));
    },
    /** Caminho de socket do app no gateway: `/{app}/ws` ou `/{app}/ws/…`. */
    isWsPath(p) {
      return /^\/[a-z0-9][a-z0-9-]*\/ws(?:[/?]|$)/.test(String(p || ''));
    },
    /** Texto recebido no socket → lista de mensagens ({html,target,swap} | {event,data} |
     *  {text,target}); texto que não é JSON é HTML para o alvo padrão. Máx. 20 por quadro. */
    wsMessages(data) {
      const s = String(data == null ? '' : data);
      let m;
      try { m = JSON.parse(s); } catch (_) { return s.trim() ? [{ html: s }] : []; }
      if (!m || typeof m !== 'object') return [];
      const list = Array.isArray(m) ? m : Array.isArray(m.fragments) ? m.fragments : [m];
      return list.filter((x) => x && typeof x === 'object').slice(0, 20);
    },
    /** Páginas de `total` itens em blocos de `size`. */
    pages(total, size) {
      return size > 0 ? Math.max(1, Math.ceil(total / size)) : 1;
    },
  };
  if (typeof module !== 'undefined' && module.exports && typeof document === 'undefined') {
    module.exports = Pure;
    return;
  }
  if (window.HubKit) return;
  const me = document.querySelector('script[data-hub-app]');
  // O hub-kit carrega ANTES do hub.js (que traz data-hub-app): o id do app é lido na hora.
  const curApp = () => { const m = document.querySelector('script[data-hub-app]'); return (m && m.dataset.hubApp) || document.documentElement.dataset.hubApp || ''; };
  const frag = (me && me.dataset.hubFrag) || '/_frag';
  const Safe = window.HubSafe;
  const SCOPE = '#app-root, [data-hub-layer]';
  const inScope = (el) => Boolean(el && el.closest && el.closest(SCOPE));
  const appRoot = () => document.getElementById('app-root');
  /** Elemento nomeado num atributo data-hub-*: id puro, dentro do conteúdo do app (ou de
   *  uma camada do hub aberta por ele) e nunca o próprio #app-root. */
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

  // ---- caminhos do próprio app ----
  function ownPath(url) {
    if (!url) return null;
    let u;
    try { u = new URL(url, location.href); } catch (_) { return null; }
    if (u.origin !== location.origin) return null;
    // Público `/apps/{app}/…` (ou o antigo `/{app}/…`) → caminho no gateway `/{app}/…`.
    const p = u.pathname.startsWith('/apps/') ? u.pathname.slice(5) : u.pathname;
    const app = curApp();
    if (!app || (p !== `/${app}` && !p.startsWith(`/${app}/`))) return null;
    return p + u.search;
  }

  function idem() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
  }

  // ---- inserção segura ----
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
      if (note) { let msg = note; try { msg = decodeURIComponent(note); } catch (_) { /* texto cru */ } toast(msg); }
      else if (res.ok && el.dataset.hubToast) toast(el.dataset.hubToast);
    } catch (_) {
      toast(t('js.kit_offline', 'Sem conexão com a aplicação. Tente de novo.'));
    } finally {
      busy(el, false);
    }
  }

  // ---- confirmação ----
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
      try { prev && prev.focus && prev.focus(); } catch (_) { /* nada */ }
      onClose && onClose(v);
    };
    // Clicar fora não fecha (o que foi digitado se perderia); Esc só fecha sem alterações.
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
  /** Segura o evento até o usuário confirmar; confirmado, repete a ação uma vez. */
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

  // ---- validação de formulário ----
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

  // ---- tabelas ----
  function rowsOf(container) {
    if (!container) return [];
    if (container.tagName === 'TABLE') return [...container.tBodies].flatMap((b) => [...b.rows]);
    const marked = container.querySelectorAll('[data-hub-filter-item]');
    return marked.length ? [...marked] : [...container.children];
  }
  function paginate(table, page) {
    const size = Number(table.getAttribute('data-hub-page-size')) || 0;
    if (!size) return;
    const rows = rowsOf(table).filter((r) => !r.hasAttribute('data-hub-filtered'));
    const pages = Pure.pages(rows.length, size);
    const cur = Math.min(Math.max(1, page || Number(table.dataset.hubPage) || 1), pages);
    table.dataset.hubPage = String(cur);
    rowsOf(table).forEach((r) => { r.hidden = r.hasAttribute('data-hub-filtered'); });
    rows.forEach((r, i) => { r.hidden = i < (cur - 1) * size || i >= cur * size; });
    let nav = table.nextElementSibling && table.nextElementSibling.matches('nav.hub-pager') ? table.nextElementSibling : null;
    if (!nav) {
      nav = document.createElement('nav');
      nav.className = 'hub-pager';
      nav.setAttribute('aria-label', t('js.kit_pages', 'Paginação'));
      table.after(nav);
    }
    const mk = (label, p, disabled, current) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'secondary-button';
      b.textContent = label;
      b.disabled = disabled;
      if (current) b.setAttribute('aria-current', 'page');
      b.addEventListener('click', () => paginate(table, p));
      return b;
    };
    const info = document.createElement('span');
    info.className = 'hub-muted';
    info.textContent = `${cur} / ${pages}`;
    nav.replaceChildren(mk('‹', cur - 1, cur <= 1), info, mk('›', cur + 1, cur >= pages));
  }
  function sortBy(th) {
    const table = th.closest('table');
    if (!table || !table.tBodies[0]) return;
    const idx = [...th.parentElement.children].indexOf(th);
    const dir = th.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
    th.parentElement.querySelectorAll('th[aria-sort]').forEach((x) => x.removeAttribute('aria-sort'));
    th.setAttribute('aria-sort', dir);
    const kind = th.getAttribute('data-hub-sort') || 'text';
    const body = table.tBodies[0];
    const rows = [...body.rows].sort((a, b) => {
      const c = Pure.compare(a.cells[idx]?.textContent.trim() || '', b.cells[idx]?.textContent.trim() || '', kind);
      return dir === 'ascending' ? c : -c;
    });
    body.append(...rows);
    paginate(table, 1);
  }
  function filter(input) {
    const sel = input.getAttribute('data-hub-filter');
    const container = sel ? ref(sel) : appRoot()?.querySelector('.data-table');
    if (sel && !container) return;
    const scope = container || appRoot();
    const q = Pure.fold(input.value.trim());
    let visible = 0;
    const rows = container ? rowsOf(container) : [];
    rows.forEach((r) => {
      const show = !q || Pure.fold(r.getAttribute('data-hub-filter-text') || r.textContent).includes(q);
      r.toggleAttribute('data-hub-filtered', !show);
      r.hidden = !show;
      if (show) visible++;
    });
    if (container && container.hasAttribute('data-hub-page-size')) paginate(container, 1);
    const e = scope && ref(input.getAttribute('data-hub-filter-empty'));
    if (e) e.hidden = visible > 0;
  }

  // ---- abas ----
  function selectTab(tab) {
    const group = tab.closest('[data-hub-tabs]');
    if (!group) return;
    group.querySelectorAll('[data-hub-tab]').forEach((x) => {
      const on = x === tab;
      x.setAttribute('aria-selected', String(on));
      x.tabIndex = on ? 0 : -1;
      const p = ref(x.getAttribute('data-hub-tab'));
      if (p) p.hidden = !on;
    });
  }

  // ---- selects dependentes ----
  function fillDependent(child) {
    const parent = ref(child.getAttribute('data-hub-depends'));
    if (!parent) return;
    let map = {};
    try { map = JSON.parse(child.getAttribute('data-hub-options') || '{}'); } catch (_) { return; }
    const opts = Array.isArray(map[parent.value]) ? map[parent.value] : [];
    const keep = child.value;
    const first = child.querySelector('option[value=""]');
    child.replaceChildren(...(first ? [first] : []), ...opts.slice(0, 500).map((o) => {
      const [v, l] = Array.isArray(o) ? o : [o, o];
      const op = document.createElement('option');
      op.value = String(v);
      op.textContent = String(l);
      return op;
    }));
    if ([...child.options].some((o) => o.value === keep)) child.value = keep;
    child.disabled = opts.length === 0 && !first;
  }


  // ---- tempo real (WebSocket pelo gateway) ----
  // Um socket por URL; o elemento dono pode ser trocado por um fragmento (outerHTML) — o
  // novo elemento com o mesmo data-hub-ws herda o socket. Sem dono por 3 s → fecha.
  const sockets = new Map();
  const polls = new WeakMap();
  const wsOwners = (url, attr = 'data-hub-ws') => [...document.querySelectorAll(`[${attr}]`)].filter((e) => e.getAttribute(attr) === url && inScope(e));
  function wsState(st, v) { st.state = v; wsOwners(st.url, st.attr).forEach((o) => o.setAttribute(`${st.attr || 'data-hub-ws'}-state`, v)); }
  function wsPoll(el, on) {
    if (on) {
      if (polls.has(el) || !el.hasAttribute('data-hub-get')) return;
      const ms = Pure.everyMs(el.getAttribute('data-hub-trigger') || '') || 2000;
      polls.set(el, setInterval(() => { if (!el.isConnected) { clearInterval(polls.get(el)); polls.delete(el); return; } request(el); }, ms));
    } else if (polls.has(el)) { clearInterval(polls.get(el)); polls.delete(el); }
  }
  function wsApply(st, data) {
    const a = st.attr || 'data-hub-ws';
    const owner = wsOwners(st.url, a)[0];
    if (!owner) return;
    for (const m of Pure.wsMessages(data)) {
      const tsel = m.target != null ? String(m.target) : owner.getAttribute(`${a}-target`);
      const target = tsel ? ref(tsel) : owner;
      if (m.html != null && target && inScope(target)) insert(target, String(m.html), String(m.swap || owner.getAttribute(`${a}-swap`) || 'outerHTML'));
      else if (m.text != null && target && inScope(target)) target.textContent = String(m.text).slice(0, 2000);
      if (m.event) owner.dispatchEvent(new CustomEvent(a === 'data-hub-sse' ? 'hub:sse' : 'hub:ws', { bubbles: true, detail: { event: String(m.event).slice(0, 64), data: m.data } }));
    }
  }
  function wsConnect(st) {
    st.retry = 0;
    if (!wsOwners(st.url).length) { sockets.delete(st.url); return; }
    let ws;
    try {
      ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/apps${st.path}`);
    } catch (_) { wsLost(st); return; }
    st.ws = ws;
    wsState(st, 'connecting');
    ws.onopen = () => {
      const resync = st.failed || st.opened;
      st.failed = false; st.opened = true; st.attempt = 0;
      wsState(st, 'open');
      wsOwners(st.url).forEach((o) => { wsPoll(o, false); if (resync && o.hasAttribute('data-hub-get')) request(o); });
    };
    ws.onmessage = (e) => { if (typeof e.data === 'string') wsApply(st, e.data); };
    ws.onclose = () => { if (st.ws === ws) { st.ws = null; wsLost(st); } };
  }
  function wsLost(st) {
    if (st.closing) return;
    st.failed = true;
    const owners = wsOwners(st.url);
    if (!owners.length) { sockets.delete(st.url); return; }
    wsState(st, 'polling');
    owners.forEach((o) => wsPoll(o, true));
    st.retry = setTimeout(() => wsConnect(st), Pure.wsBackoff(st.attempt++));
  }
  function wsBind(el) {
    const url = el.getAttribute('data-hub-ws') || '';
    const path = ownPath(url);
    if (!path || !Pure.isWsPath(path) || typeof WebSocket === 'undefined') {
      if (url) console.warn('data-hub-ws fora do app (ou sem WebSocket): polling', el);
      el.setAttribute('data-hub-ws-state', 'polling');
      wsPoll(el, true);
      return;
    }
    let st = sockets.get(url);
    if (!st) { st = { url, path, ws: null, attempt: 0, failed: false, opened: false, state: 'connecting', retry: 0, closing: false }; sockets.set(url, st); wsConnect(st); }
    el.setAttribute('data-hub-ws-state', st.state);
    if (st.failed) wsPoll(el, true);
    if (!sweeper) sweeper = setInterval(wsSweep, 3000);
  }
  let sweeper = 0;
  function wsSweep() {
    for (const st of [...sockets.values()]) {
      if (wsOwners(st.url).length) continue;
      st.closing = true;
      clearTimeout(st.retry);
      try { st.ws && st.ws.close(1000); } catch (_) { /* nada */ }
      sockets.delete(st.url);
    }
    for (const st of [...streams.values()]) {
      if (wsOwners(st.url, st.attr).length) continue;
      clearTimeout(st.retry);
      try { st.es && st.es.close(); } catch (_) { /* nada */ }
      streams.delete(st.url);
    }
    if (!sockets.size && !streams.size) { clearInterval(sweeper); sweeper = 0; }
  }

  // ---- tempo real (SSE pelo gateway) ----
  // Mesmo formato de mensagem do data-hub-ws (cada `data:` do evento), um EventSource por
  // URL em /_frag; se o fluxo cai, o data-hub-get + data-hub-trigger faz polling até voltar.
  const streams = new Map();
  function sseConnect(st) {
    const owners = wsOwners(st.url, st.attr);
    if (!owners.length) { streams.delete(st.url); return; }
    let es;
    try { es = new EventSource(`${frag}${st.path}`); } catch (_) { sseLost(st); return; }
    st.es = es;
    wsState(st, 'connecting');
    es.onopen = () => {
      const resync = st.failed;
      st.failed = false; st.attempt = 0;
      wsState(st, 'open');
      wsOwners(st.url, st.attr).forEach((o) => { wsPoll(o, false); if (resync && o.hasAttribute('data-hub-get')) request(o); });
    };
    es.onmessage = (e) => wsApply(st, e.data);
    // O navegador reconecta sozinho; erro com o fluxo fechado (status ≠ 200) = nós.
    es.onerror = () => { if (es.readyState === 2 && st.es === es) { st.es = null; sseLost(st); } else if (!st.failed) { st.failed = true; wsState(st, 'polling'); wsOwners(st.url, st.attr).forEach((o) => wsPoll(o, true)); } };
  }
  function sseLost(st) {
    st.failed = true;
    const owners = wsOwners(st.url, st.attr);
    if (!owners.length) { streams.delete(st.url); return; }
    wsState(st, 'polling');
    owners.forEach((o) => wsPoll(o, true));
    st.retry = setTimeout(() => sseConnect(st), Pure.wsBackoff(st.attempt++));
  }
  function sseBind(el) {
    const url = el.getAttribute('data-hub-sse') || '';
    const path = ownPath(url);
    if (!path || typeof EventSource === 'undefined') {
      if (url) console.warn('data-hub-sse fora do app (ou sem EventSource): polling', el);
      el.setAttribute('data-hub-sse-state', 'polling');
      wsPoll(el, true);
      return;
    }
    let st = streams.get(url);
    if (!st) { st = { url, path, attr: 'data-hub-sse', es: null, attempt: 0, failed: false, state: 'connecting', retry: 0 }; streams.set(url, st); sseConnect(st); }
    el.setAttribute('data-hub-sse-state', st.state);
    if (st.failed) wsPoll(el, true);
    if (!sweeper) sweeper = setInterval(wsSweep, 3000);
  }

  // ---- montagem de um trecho novo ----
  const timers = new WeakMap();
  function mount(scope) {
    if (!scope || !scope.querySelectorAll) return;
    const all = (sel) => (scope.matches && scope.matches(sel) ? [scope] : []).concat([...scope.querySelectorAll(sel)]);
    all('[data-hub-charts]').forEach((n) => {
      try { JSON.parse(n.getAttribute('data-hub-charts')).forEach((cfg) => window.TimeSeriesChart && window.TimeSeriesChart.render(cfg)); } catch (err) { console.error('data-hub-charts inválido', err); }
    });
    if (window.TimeSeriesChart) window.TimeSeriesChart.mountAll(scope.parentNode || scope);
    all('[data-hub-chart]').forEach((n) => {
      try { window.HubChart && window.HubChart.draw(n, JSON.parse(n.getAttribute('data-hub-chart'))); } catch (err) { console.error('data-hub-chart inválido', err); }
    });
    all('table[data-hub-page-size]').forEach((tb) => paginate(tb, 1));
    all('[data-hub-tabs]').forEach((g) => { const cur = g.querySelector('[data-hub-tab][aria-selected="true"]') || g.querySelector('[data-hub-tab]'); if (cur) selectTab(cur); });
    all('select[data-hub-depends]').forEach(fillDependent);
    all('[data-hub-ws]').forEach(wsBind);
    all('[data-hub-sse]').forEach(sseBind);
    all('[data-hub-get], [data-hub-post]').forEach((el) => {
      const trig = el.getAttribute('data-hub-trigger') || '';
      if (/(^|\s)load(\s|$)/.test(trig)) request(el);
      const every = Pure.everyMs(trig);
      // Com data-hub-ws/-sse o polling é o plano B: só liga quando o fluxo cai (wsPoll).
      if (every && !timers.has(el) && !el.hasAttribute('data-hub-ws') && !el.hasAttribute('data-hub-sse')) {
        const id = setInterval(() => { if (!el.isConnected) { clearInterval(id); return; } request(el); }, every);
        timers.set(el, id);
      }
    });
  }

  const triggerOf = (el) => el.getAttribute('data-hub-trigger') || Pure.defaultTrigger(el.tagName.toLowerCase(), (el.getAttribute('type') || '').toLowerCase());
  const wants = (el, kind) => triggerOf(el).split(/\s*,\s*|\s+/).includes(kind);

  // ---- delegação ----
  document.addEventListener('keydown', (e) => {
    const ws = document.getElementById('workspace');
    if (e.key !== 'Escape' || !ws?.classList.contains('menu-open')) return;
    ws.classList.remove('menu-open');
    const bar = ws.querySelector('[data-hub-menu="open"], [data-mobile-menu]');
    bar?.setAttribute('aria-expanded', 'false');
    bar?.focus();
  });
  document.addEventListener('click', (e) => {
    const t0 = e.target;
    if (!inScope(t0)) return;
    const c = t0.closest('[data-hub-confirm]');
    if (c && c.tagName !== 'FORM' && inScope(c) && !(c.type === 'submit' && c.form)) {
      if (guard(e, c, () => c.click())) return;
    }
    // Escolher uma seção (ou tocar no fundo) fecha o painel de navegação do app.
    if (t0.closest('.app-sidebar a[href], .mobile-backdrop')) {
      const ws = document.getElementById('workspace');
      if (ws?.classList.contains('menu-open')) {
        ws.classList.remove('menu-open');
        ws.querySelectorAll('[data-hub-menu="open"], [data-mobile-menu]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
      }
    }
    const menu = t0.closest('[data-hub-menu], [data-mobile-menu], [data-mobile-close]');
    if (menu) {
      const ws = document.getElementById('workspace');
      // O botão "open" alterna (no celular a barra do app abre e fecha o painel de seções).
      const open = (menu.getAttribute('data-hub-menu') === 'open' || menu.hasAttribute('data-mobile-menu')) && !ws?.classList.contains('menu-open');
      ws?.classList.toggle('menu-open', open);
      ws?.querySelectorAll('[data-hub-menu="open"], [data-mobile-menu]').forEach((b) => b.setAttribute('aria-expanded', String(open)));
      return;
    }
    const ext = t0.closest('a[data-hub-external]');
    if (ext) {
      if (guard(e, ext, () => ext.click(), `${t('js.kit_leave', 'Você vai sair do hub para')} ${ext.getAttribute('data-hub-external')}`)) return;
      return;
    }
    const modal = t0.closest('[data-hub-modal]');
    if (modal) { e.preventDefault(); openModal(modal); return; }
    const sort = t0.closest('th[data-hub-sort]');
    if (sort) { sortBy(sort); return; }
    const tab = t0.closest('[data-hub-tab]');
    if (tab) { e.preventDefault(); selectTab(tab); return; }
    const copy = t0.closest('[data-hub-copy], [data-hub-copy-from]');
    if (copy) {
      e.preventDefault();
      const from = copy.getAttribute('data-hub-copy-from');
      const src = from && ref(from);
      const text = copy.hasAttribute('data-hub-copy') ? copy.getAttribute('data-hub-copy') : src ? ('value' in src ? src.value : src.textContent) : '';
      const done = () => toast(copy.getAttribute('data-hub-toast') || t('js.kit_copied', 'Copiado para a área de transferência.'));
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => toast(t('js.kit_copy_fail', 'Não foi possível copiar.')));
      return;
    }
    const act = t0.closest('[data-hub-get], [data-hub-post]');
    if (act && act.tagName !== 'FORM' && wants(act, 'click')) { e.preventDefault(); request(act); return; }
    const tst = t0.closest('[data-hub-toast]');
    if (tst && tst.tagName !== 'FORM') { e.preventDefault(); toast(tst.getAttribute('data-hub-toast')); }
  }, true);

  document.addEventListener('keydown', (e) => {
    const tab = e.target.closest && e.target.closest('[data-hub-tab]');
    if (!tab || !inScope(tab) || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const tabs = [...tab.closest('[data-hub-tabs]').querySelectorAll('[data-hub-tab]')];
    const i = tabs.indexOf(tab);
    const next = e.key === 'Home' ? tabs[0] : e.key === 'End' ? tabs[tabs.length - 1] : tabs[(i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
    e.preventDefault();
    selectTab(next);
    next.focus();
  });

  // Captura: roda antes do hub.js (que envia os formulários do app).
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (!inScope(form)) return;
    if (form.hasAttribute('data-hub-blocked')) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    const sub = e.submitter;
    if (sub && sub.hasAttribute && sub.hasAttribute('data-hub-blocked')) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (form.hasAttribute('data-hub-validate') && !showErrors(form)) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    const conf = (sub && sub.getAttribute && sub.getAttribute('data-hub-confirm') && sub) || (form.hasAttribute('data-hub-confirm') && form);
    if (conf && guard(e, conf, () => HTMLFormElement.prototype.requestSubmit.call(form, sub || undefined))) return;
    if (form.hasAttribute('data-hub-dismiss')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      form.closest('[data-hub-layer]')?.remove();
      if (form.dataset.hubToast) toast(form.dataset.hubToast);
      return;
    }
    if (form.hasAttribute('data-hub-get') || form.hasAttribute('data-hub-post')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      request(form, e);
    }
  }, true);

  document.addEventListener('input', (e) => {
    const el = e.target;
    if (!inScope(el)) return;
    if (el.matches('[data-hub-filter], [data-table-search]')) { filter(el); return; }
    if (el.closest('form[data-hub-validate]') && el.hasAttribute('aria-invalid')) showErrors(el.form);
    if ((el.hasAttribute('data-hub-get') || el.hasAttribute('data-hub-post')) && wants(el, 'input')) {
      clearTimeout(timers.get(el));
      timers.set(el, setTimeout(() => request(el), 300));
    }
  });

  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!inScope(el)) return;
    document.querySelectorAll('#app-root select[data-hub-depends]').forEach((c) => {
      const p = ref(c.getAttribute('data-hub-depends'));
      if (p === el) fillDependent(c);
    });
    if ((el.hasAttribute('data-hub-get') || el.hasAttribute('data-hub-post')) && wants(el, 'change')) request(el);
  });

  window.HubKit = Object.freeze({ mount, toast, insert, confirm: confirmDialog, pure: Pure });
})();
