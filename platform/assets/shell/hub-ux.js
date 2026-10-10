/* hub-ux.js — experiência do shell (onda 9), em todas as páginas do hub:
 *
 *  - Busca global (paleta ⌘K / Ctrl+K): diálogo modal com combobox + listbox agrupado
 *    (ARIA 1.2), navegação por setas/Home/End/Enter/Esc, foco preso no diálogo e
 *    devolvido ao fechar, buscas recentes. Nas páginas do hub consulta
 *    `/api/me/search` (plataforma + apps federados); dentro de um app a CSP só deixa
 *    falar com o gateway do app, então a paleta usa o índice local (apps e páginas do
 *    usuário, publicado em `data-hub-palette`) e oferece "Buscar em todas as aplicações"
 *    (abre `/search?q=`).
 *  - Tour guiado: passos conforme o que o usuário vê (`[data-tour]`), diálogo com foco,
 *    Voltar/Próximo/Pular, Esc interrompe (retomado depois), estado no servidor
 *    (`/api/me/onboarding`). Começa sozinho no primeiro acesso ao Início.
 *  - Inbox ao vivo: contador do sino atualizado pela conexão SSE compartilhada entre
 *    abas (`logshare.js`, trava do navegador + BroadcastChannel) e anunciado em
 *    `aria-live`.
 *  - Menus <details> do header: Esc fecha e devolve o foco; abrir um fecha o outro.
 *
 * Sem dependências; nenhum script inline (CSP). Textos em `data-hub-i18n`.
 */
(function () {
  'use strict';

  // ---- Lógica pura (testada em node: `require('hub-ux.js')`) --------------------------
  const Pure = {
    fold(s) {
      return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    },
    /** Relevância (mesma regra do servidor): igual 1, começa 0,8, palavra 0,6, contém 0,4. */
    score(text, q) {
      const t = Pure.fold(text);
      const n = Pure.fold(q).trim();
      if (!n) return 0;
      if (t === n) return 1;
      if (t.startsWith(n)) return 0.8;
      if (t.split(/[^\p{L}\p{N}]+/u).some((w) => w.startsWith(n))) return 0.6;
      if (t.includes(n)) return 0.4;
      return 0;
    },
    /** Resultados locais (índice da paleta) para `q`, melhores primeiro. */
    local(index, q, max = 8) {
      return (index || [])
        .map((h) => ({ ...h, score: Math.max(Pure.score(h.title, q), Pure.score(h.subtitle, q) * 0.8) }))
        .filter((h) => h.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, max);
    },
    /** Próximo índice ativo na lista (setas, Home, End), com volta ao início/fim. */
    move(cur, len, key) {
      if (!len) return -1;
      if (key === 'Home') return 0;
      if (key === 'End') return len - 1;
      if (key === 'ArrowDown') return cur < 0 ? 0 : (cur + 1) % len;
      if (key === 'ArrowUp') return cur <= 0 ? len - 1 : cur - 1;
      return cur;
    },
    /** Lista de buscas recentes (mais nova primeiro, sem repetição, até `max`). */
    remember(list, q, max = 8) {
      const v = String(q || '').trim();
      if (!v) return list || [];
      return [v, ...(list || []).filter((x) => x !== v)].slice(0, max);
    },
    /** Passos do tour que existem na página, na ordem canônica. */
    tourSteps(available) {
      const order = ['home', 'apps', 'search', 'inbox', 'user', 'admin'];
      return order.filter((k) => available.includes(k));
    },
    /** Índice para retomar o tour a partir do estado salvo (`step:N`). */
    resumeAt(state, total) {
      const m = /^step:(\d+)$/.exec(state || '');
      const n = m ? Number(m[1]) : 0;
      return Math.min(Math.max(n, 0), Math.max(total - 1, 0));
    },
  };
  if (typeof module !== 'undefined' && module.exports && typeof document === 'undefined') {
    module.exports = Pure;
    return;
  }

  const body = document.body;
  if (!body || body.dataset.hubUx !== '1') return;
  let T = {};
  try { T = JSON.parse(body.dataset.hubI18n || '{}'); } catch (_) { T = {}; }
  const t = (k, vars) => {
    let s = T[k] || k;
    if (vars) for (const [a, b] of Object.entries(vars)) s = s.replace(`{${a}}`, String(b));
    return s;
  };
  const inApp = body.classList.contains('in-app');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let paletteIndex = [];
  try { paletteIndex = JSON.parse(body.dataset.hubPalette || '[]'); } catch (_) { paletteIndex = []; }
  const live = document.getElementById('hub-live');
  const announce = (msg) => { if (live) { live.textContent = ''; setTimeout(() => { live.textContent = msg; }, 50); } };
  const store = () => { try { return window.localStorage; } catch (_) { return null; } };

  async function api(method, url, data) {
    const res = await fetch(url, {
      method,
      credentials: 'same-origin',
      headers: data ? { 'content-type': 'application/json', accept: 'application/json' } : { accept: 'application/json' },
      body: data ? JSON.stringify(data) : undefined,
    });
    if (res.status === 204) return {};
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || t('js.error'));
    return j;
  }
  // Uso de recursos (analytics); dentro de apps a CSP não permite — ignorado.
  function feature(name) {
    if (inApp) return;
    api('POST', '/api/me/analytics', { feature: name }).catch(() => {});
  }
  // Busca "final" (analytics + recentes + passo "Usar a busca"): POST de mesma origem —
  // o GET da busca não tem efeitos colaterais.
  function recordSearch(q) {
    if (inApp || !q) return;
    fetch('/api/me/search', {
      method: 'POST',
      credentials: 'same-origin',
      // Sem keepalive: a página continua aberta, e um fetch keepalive nunca dá a rede
      // por ociosa no Chromium (/search?q=a não chegava a networkidle).
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ q }),
    }).catch(() => {});
  }
  const recordForm = document.querySelector('[data-hub-search-record]');
  if (recordForm && recordForm.dataset.hubSearchRecord) recordSearch(recordForm.dataset.hubSearchRecord);
  window.HubUx = { api, t, announce, feature, inApp };

  // ---- Menus do header (<details>) ---------------------------------------------------
  const menus = [...document.querySelectorAll('.platform-header details')];
  menus.forEach((d) => {
    d.addEventListener('toggle', () => {
      if (d.open) {
        menus.forEach((o) => { if (o !== d) o.open = false; });
        if (d.classList.contains('hub-notif-menu')) feature('inbox_open');
      }
    });
    d.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && d.open) {
        d.open = false;
        d.querySelector('summary')?.focus();
      }
    });
  });
  document.addEventListener('click', (e) => {
    menus.forEach((d) => { if (d.open && !d.contains(e.target)) d.open = false; });
  });

  // ---- Confirmação no modal do hub (no lugar do confirm() nativo) --------------------
  // `HubConfirm(mensagem, { danger, ok })` → Promise<boolean>. Esc/Cancelar = false.
  function hubConfirm(message, opts) {
    const o = opts || {};
    const danger = o.danger !== undefined ? o.danger : /excluir|apagar|revogar|desativar|remover|reverter|rotacionar|restaurar|delete|revoke|remove/i.test(message);
    return new Promise((resolve) => {
      const prev = document.activeElement;
      const host = document.createElement('div');
      host.className = 'overlay hub-confirm-overlay';
      host.innerHTML = `<section class="modal hub-confirm" role="alertdialog" aria-modal="true" aria-labelledby="hub-confirm-title" aria-describedby="hub-confirm-msg">
        <div class="modal-header"><h2 id="hub-confirm-title">${esc(o.title || t('js.confirm_title'))}</h2></div>
        <p id="hub-confirm-msg" class="hub-confirm-msg">${esc(message)}</p>
        <div class="modal-actions"><button type="button" class="secondary-button" data-hub-confirm="no">${esc(t('js.cancel'))}</button>
        <button type="button" class="${danger ? 'danger-button' : 'primary-button'}" data-hub-confirm="yes">${esc(o.ok || t('js.confirm_ok'))}</button></div></section>`;
      const done = (v) => {
        host.remove();
        document.removeEventListener('keydown', onKey, true);
        if (prev && prev.focus) prev.focus();
        resolve(v);
      };
      const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); } };
      host.addEventListener('click', (e) => {
        const b = e.target.closest('[data-hub-confirm]');
        if (b) done(b.dataset.hubConfirm === 'yes');
      });
      document.addEventListener('keydown', onKey, true);
      document.body.append(host);
      host.querySelector(danger ? '[data-hub-confirm="no"]' : '[data-hub-confirm="yes"]').focus();
    });
  }
  window.HubConfirm = hubConfirm;

  // ---- Campo de arquivo do hub (rótulo no idioma do usuário, no lugar do nativo) ------
  function enhanceFile(inp) {
    if (inp.dataset.hubFile || inp.hidden || inp.closest('[hidden]') || !inp.parentNode) return;
    inp.dataset.hubFile = '1';
    const wrap = document.createElement('span');
    wrap.className = 'hub-file';
    inp.parentNode.insertBefore(wrap, inp);
    const btn = document.createElement('span');
    btn.className = 'secondary-button hub-file-btn';
    btn.setAttribute('aria-hidden', 'true');
    btn.textContent = t('js.file_choose');
    const name = document.createElement('span');
    name.className = 'hub-file-name';
    name.textContent = t('js.file_none');
    inp.classList.add('hub-file-input');
    wrap.append(inp, btn, name);
    btn.addEventListener('click', () => inp.click());
    inp.addEventListener('change', () => {
      name.textContent = inp.files && inp.files.length ? [...inp.files].map((f) => f.name).join(', ') : t('js.file_none');
      name.title = name.textContent;
    });
  }
  const scanFiles = (root) => root.querySelectorAll && root.querySelectorAll('input[type=file]').forEach(enhanceFile);
  scanFiles(document);
  if (typeof MutationObserver !== 'undefined') {
    new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => { if (n.nodeType === 1) { if (n.matches('input[type=file]')) enhanceFile(n); else scanFiles(n); } })))
      .observe(document.body, { childList: true, subtree: true });
  }

  // Idioma: aplica na hora (sem botão "OK").
  document.addEventListener('change', (e) => {
    const sel = e.target.closest && e.target.closest('select[data-hub-autosubmit]');
    if (sel && sel.form) sel.form.requestSubmit ? sel.form.requestSubmit() : sel.form.submit();
  });
  // Tema (3 ícones, role=radiogroup): aplica na hora e grava pela API de preferências;
  // setas movem/selecionam (padrão de radiogroup). Sem JS o formulário faz POST normal.
  document.querySelectorAll('[data-hub-theme-seg]').forEach((seg) => {
    const btns = [...seg.querySelectorAll('[role=radio]')];
    const pick = (b, focus) => {
      btns.forEach((x) => { const on = x === b; x.setAttribute('aria-checked', String(on)); x.classList.toggle('active', on); x.tabIndex = on ? 0 : -1; });
      if (focus) b.focus();
      const v = b.value;
      document.documentElement.setAttribute('data-theme', v);
      const meta = document.querySelector('meta[name=color-scheme]');
      if (meta) meta.setAttribute('content', v === 'dark' ? 'dark' : v === 'auto' ? 'light dark' : 'light');
      const form = seg.closest('form');
      if (form) {
        fetch(form.action, { method: 'POST', credentials: 'same-origin', redirect: 'manual', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'theme=' + encodeURIComponent(v) }).catch(() => {});
      }
    };
    btns.forEach((b, i) => {
      b.addEventListener('click', (e) => { e.preventDefault(); pick(b, false); });
      b.addEventListener('keydown', (e) => {
        const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (d) { e.preventDefault(); pick(btns[(i + d + btns.length) % btns.length], true); }
        else if (e.key === 'Home' || e.key === 'End') { e.preventDefault(); pick(btns[e.key === 'Home' ? 0 : btns.length - 1], true); }
      });
    });
  });
  // Atalho da busca conforme a plataforma (⌘K no macOS, Ctrl K nos demais).
  if (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '')) {
    document.querySelectorAll('.hub-kbd').forEach((k) => { k.textContent = '⌘K'; });
  }

  // ---- Menus "⋯" de linha (<details class="hub-row-menu">): um aberto por vez; clique
  // fora ou Esc fecha; escolher uma ação fecha o menu.
  document.addEventListener('click', (e) => {
    document.querySelectorAll('details.hub-row-menu[open]').forEach((d) => {
      if (!d.contains(e.target) || e.target.closest('.hub-row-menu-list button, .hub-row-menu-list a')) d.open = false;
    });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('details.hub-row-menu[open]').forEach((d) => { d.open = false; d.querySelector('summary')?.focus(); });
  });

  // Menu "⋯" perto da borda da tela: abre para cima (falta espaço embaixo) e/ou alinhado à
  // esquerda (sairia pela esquerda). `toggle` não borbulha: escuta na captura.
  document.addEventListener('toggle', (e) => {
    const d = e.target;
    if (!(d instanceof HTMLDetailsElement) || !d.classList.contains('hub-row-menu')) return;
    d.classList.remove('hub-row-menu--up', 'hub-row-menu--start');
    if (!d.open) return;
    const list = d.querySelector('.hub-row-menu-list');
    if (!list) return;
    const r = list.getBoundingClientRect();
    const s = d.getBoundingClientRect();
    if (r.bottom > window.innerHeight - 8 && s.top > window.innerHeight - s.bottom) d.classList.add('hub-row-menu--up');
    if (r.left < 8) d.classList.add('hub-row-menu--start');
  }, true);

  // ---- Diálogos modais das telas (Administração/Operações/Insights) -----------------------
  // Genérico para qualquer [role=dialog][aria-modal=true] visível: Tab/Shift+Tab ficam
  // dentro do diálogo e, quando ele some, o foco volta para quem estava focado antes.
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const openDialog = () => [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')].find((d) => d.getClientRects().length && !d.closest('[hidden]'));
  let beforeDialog = null;
  document.addEventListener('focusin', (e) => {
    if (!e.target.closest || !e.target.closest('[role="dialog"]')) beforeDialog = e.target;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const d = openDialog();
    if (!d || d.classList.contains('hub-palette')) return;
    const f = [...d.querySelectorAll(FOCUSABLE)].filter((x) => x.getClientRects().length);
    if (!f.length) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (!d.contains(document.activeElement)) { e.preventDefault(); first.focus(); return; }
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  if (typeof MutationObserver !== 'undefined') {
    new MutationObserver(() => {
      const lost = !document.activeElement || document.activeElement === document.body;
      if (lost && !openDialog() && beforeDialog && beforeDialog.isConnected) beforeDialog.focus();
    }).observe(document.body, { childList: true, subtree: true });
  }

  // ---- Paleta de busca (⌘K) ---------------------------------------------------------
  let palette = null;
  let opener = null;
  let options = [];
  let active = -1;
  let timer = null;
  let seq = 0;
  let lastQ = '';

  function recentList() {
    try { return JSON.parse(store()?.getItem('hub:recent-searches') || '[]'); } catch (_) { return []; }
  }
  function saveRecent(q) {
    try { store()?.setItem('hub:recent-searches', JSON.stringify(Pure.remember(recentList(), q))); } catch (_) { /* privado */ }
  }

  function build() {
    const el = document.createElement('div');
    el.className = 'hub-palette-backdrop';
    el.hidden = true;
    el.innerHTML = `<div class="hub-palette" role="dialog" aria-modal="true" aria-label="${esc(t('js.palette_label'))}">
      <div class="hub-palette-input">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></svg>
        <input type="text" role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls="hub-palette-list" aria-label="${esc(t('js.palette_label'))}" placeholder="${esc(t('js.palette_placeholder'))}" autocomplete="off" spellcheck="false" maxlength="100">
        <button type="button" class="icon-button hub-palette-close" aria-label="${esc(t('common.close'))}">✕</button>
      </div>
      <div id="hub-palette-list" role="listbox" aria-label="${esc(t('js.palette_label'))}" class="hub-palette-list"></div>
      <div class="hub-palette-foot"><span>${esc(t('js.palette_hint'))}</span><span class="hub-palette-status" role="status" aria-live="polite"></span></div>
    </div>`;
    document.body.append(el);
    const input = el.querySelector('input');
    input.addEventListener('input', () => { schedule(input.value); });
    input.addEventListener('keydown', onKey);
    el.querySelector('.hub-palette-close').addEventListener('click', close);
    el.addEventListener('mousedown', (e) => { if (e.target === el) close(); });
    el.querySelector('.hub-palette-list').addEventListener('click', (e) => {
      const o = e.target.closest('[role="option"]');
      if (o) choose(Number(o.dataset.idx));
    });
    // Foco preso no diálogo (Tab cicla entre o campo e o botão fechar).
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'Tab') return;
      const f = [input, el.querySelector('.hub-palette-close')];
      const i = f.indexOf(document.activeElement);
      e.preventDefault();
      f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
    });
    return el;
  }

  function open() {
    if (!palette) palette = build();
    if (!palette.hidden) return;
    opener = document.activeElement;
    palette.hidden = false;
    body.classList.add('hub-modal-open');
    const input = palette.querySelector('input');
    input.value = '';
    render([], '');
    input.focus();
    feature('palette_open');
  }

  function close() {
    if (!palette || palette.hidden) return;
    palette.hidden = true;
    body.classList.remove('hub-modal-open');
    clearTimeout(timer);
    if (opener && opener.focus) opener.focus();
  }

  function schedule(q) {
    clearTimeout(timer);
    const v = q.trim();
    render(localGroups(v), v, !inApp && v.length > 0);
    if (!v) { if (!inApp) remoteRecent(); return; }
    if (inApp) return;
    timer = setTimeout(() => remote(v), 180);
  }

  function localGroups(q) {
    if (!q) return [];
    const hits = Pure.local(paletteIndex, q);
    return hits.length ? [{ id: 'platform', label: hits[0] ? t('js.palette_label') : '', hits }] : [];
  }

  async function remoteRecent() {
    const my = ++seq;
    try {
      const r = await api('GET', '/api/me/search?q=');
      if (my !== seq || palette.querySelector('input').value.trim()) return;
      render([], '', false, r.recent || []);
    } catch (_) { /* sem rede: só recentes locais */ }
  }

  async function remote(q) {
    const my = ++seq;
    lastQ = q;
    try {
      const r = await api('GET', `/api/me/search?q=${encodeURIComponent(q)}`);
      if (my !== seq) return;
      render(r.groups || [], q, false, null, r.partial);
    } catch (_) {
      if (my === seq) render(localGroups(q), q, false);
    }
  }

  // Destaca o termo buscado (sem diferença de maiúsculas/acentos).
  function mark(text, q) {
    const t = String(text || '');
    const n = Pure.fold(q).trim();
    if (!n) return esc(t);
    const folded = [...t].map((c) => Pure.fold(c));
    const i = folded.join('').indexOf(n);
    if (i < 0) return esc(t);
    let pos = 0, start = -1, end = -1;
    folded.forEach((f, k) => { if (start < 0 && pos >= i) start = k; pos += f.length; if (end < 0 && pos >= i + n.length) end = k + 1; });
    const chars = [...t];
    return `${esc(chars.slice(0, start).join(''))}<mark>${esc(chars.slice(start, end).join(''))}</mark>${esc(chars.slice(end).join(''))}`;
  }

  function render(groups, q, loading, recent, partial) {
    const list = palette.querySelector('.hub-palette-list');
    const status = palette.querySelector('.hub-palette-status');
    options = [];
    let html = '';
    if (!q) {
      const rec = recent || recentList();
      if (rec.length) {
        html += `<div role="group" aria-labelledby="hub-pg-recent"><div class="hub-palette-group" id="hub-pg-recent" role="presentation">${esc(t('js.palette_recent'))}</div>`;
        rec.forEach((r) => {
          const idx = options.length;
          options.push({ recent: r });
          html += `<div role="option" id="hub-po-${idx}" data-idx="${idx}" aria-selected="false" class="hub-palette-option"><span>${esc(r)}</span></div>`;
        });
        html += '</div>';
      }
    }
    // ⌘K: até 5 registros de apps (somados), 3 páginas e 3 itens internos; o resto
    // fica em "Ver todos os resultados".
    const caps = { app: 5, platform: groups.some((g) => g.section === 'app') ? 3 : 8, admin: 3 };
    const used = { app: 0, platform: 0, admin: 0 };
    groups = groups.map((g) => {
      const sec = g.section || (g.id === 'platform' ? 'platform' : g.id === 'admin' ? 'admin' : 'app');
      const room = Math.max(0, (caps[sec] ?? 5) - (used[sec] || 0));
      const hits = (g.hits || []).slice(0, room);
      used[sec] = (used[sec] || 0) + hits.length;
      return { ...g, hits };
    });
    groups.forEach((g, gi) => {
      if (!g.hits || !g.hits.length) return;
      html += `<div role="group" aria-labelledby="hub-pg-${gi}"><div class="hub-palette-group" id="hub-pg-${gi}" role="presentation">${esc(g.label)}</div>`;
      g.hits.forEach((h) => {
        const idx = options.length;
        options.push({ url: h.url, q });
        html += `<div role="option" id="hub-po-${idx}" data-idx="${idx}" aria-selected="false" class="hub-palette-option"><span>${mark(h.title, q)}</span><small>${esc([h.kind, h.subtitle].filter(Boolean).join(' · '))}</small></div>`;
      });
      html += '</div>';
    });
    if (q) {
      const idx = options.length;
      options.push({ url: `/search?q=${encodeURIComponent(q)}`, q });
      html += `<div role="group" aria-label="${esc(t('shell.search') || 'Busca')}"><div role="option" id="hub-po-${idx}" data-idx="${idx}" aria-selected="false" class="hub-palette-option hub-palette-all"><span>${esc(t('js.palette_all', { q }))}</span></div></div>`;
    }
    list.innerHTML = html;
    const n = options.filter((o) => o.url && !o.url.startsWith('/search?')).length;
    status.textContent = loading ? t('js.palette_loading') : q ? `${n ? t(n === 1 ? 'js.palette_count_one' : 'js.palette_count', { n }) : t('js.palette_empty')}${partial ? ` ${t('js.palette_partial')}` : ''}` : '';
    setActive(options.length && q ? 0 : -1);
  }

  function setActive(i) {
    const input = palette.querySelector('input');
    const els = palette.querySelectorAll('[role="option"]');
    els.forEach((el) => el.setAttribute('aria-selected', 'false'));
    active = i;
    if (i >= 0 && els[i]) {
      els[i].setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', els[i].id);
      els[i].scrollIntoView({ block: 'nearest' });
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  function choose(i) {
    const o = options[i];
    if (!o) return;
    if (o.recent) {
      const input = palette.querySelector('input');
      input.value = o.recent;
      schedule(o.recent);
      return;
    }
    const q = o.q || lastQ;
    if (q) {
      saveRecent(q);
      if (!inApp) {
        // Busca "final" (analytics + recentes + passo "Usar a busca").
        recordSearch(q);
        feature('palette_select');
      }
    }
    close();
    location.assign(o.url);
  }

  function onKey(e) {
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key) && options.length) {
      if ((e.key === 'Home' || e.key === 'End') && !e.ctrlKey && palette.querySelector('input').value) return;
      e.preventDefault();
      setActive(Pure.move(active, options.length, e.key));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (active >= 0) choose(active);
      else if (e.target.value.trim()) location.assign(`/search?q=${encodeURIComponent(e.target.value.trim())}`);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  }

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (palette && !palette.hidden) close();
      else open();
      return;
    }
    // Esc fecha a paleta mesmo com o foco fora do campo (ex.: toque na lista no celular).
    if (e.key === 'Escape' && palette && !palette.hidden && !palette.querySelector('input')?.contains(e.target)) { e.preventDefault(); close(); }
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-hub-palette-open]')) { e.preventDefault(); open(); }
  });

  // ---- Tour guiado -----------------------------------------------------------------
  const TOUR = {
    home: ['js.tour_home_t', 'js.tour_home_b'],
    apps: ['js.tour_apps_t', 'js.tour_apps_b'],
    search: ['js.tour_search_t', 'js.tour_search_b'],
    inbox: ['js.tour_inbox_t', 'js.tour_inbox_b'],
    user: ['js.tour_user_t', 'js.tour_user_b'],
    admin: ['js.tour_admin_t', 'js.tour_admin_b'],
  };
  let tour = null;

  function saveTour(state) {
    if (inApp) return Promise.resolve();
    body.dataset.hubTour = state;
    return api('PUT', '/api/me/onboarding', { tour: state }).catch(() => {});
  }

  function visibleTarget(k) {
    const el = document.querySelector(`[data-tour="${k}"]`);
    return el && el.getClientRects().length ? el : null;
  }

  function startTour(at) {
    if (inApp) { location.assign('/?tour=1'); return; }
    const steps = Pure.tourSteps(Object.keys(TOUR).filter((k) => visibleTarget(k)));
    if (!steps.length) return;
    endTour(null);
    opener = document.activeElement;
    const box = document.createElement('div');
    box.className = 'hub-tour';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'false');
    box.setAttribute('aria-labelledby', 'hub-tour-title');
    box.setAttribute('aria-describedby', 'hub-tour-body');
    document.body.append(box);
    tour = { steps, i: Math.min(at || 0, steps.length - 1), box };
    box.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); endTour(`step:${tour.i}`); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); stepTo(tour.i + 1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); stepTo(tour.i - 1); }
      else if (e.key === 'Tab') {
        const f = [...box.querySelectorAll('button')];
        const i = f.indexOf(document.activeElement);
        e.preventDefault();
        f[(i + (e.shiftKey ? f.length - 1 : 1) + f.length) % f.length].focus();
      }
    });
    box.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.tour === 'next') stepTo(tour.i + 1);
      else if (b.dataset.tour === 'back') stepTo(tour.i - 1);
      else if (b.dataset.tour === 'skip') endTour('skipped');
    });
    feature('tour_start');
    stepTo(tour.i);
  }

  function stepTo(i) {
    if (!tour) return;
    if (i >= tour.steps.length) { endTour('done'); announce(t('js.tour_done')); return; }
    if (i < 0) return;
    tour.i = i;
    const k = tour.steps[i];
    const target = visibleTarget(k);
    document.querySelectorAll('.hub-tour-target').forEach((x) => x.classList.remove('hub-tour-target'));
    if (target) target.classList.add('hub-tour-target');
    const [tt, tb] = TOUR[k];
    const last = i === tour.steps.length - 1;
    tour.box.innerHTML = `<p class="hub-tour-step">${esc(t('js.tour_step', { n: i + 1, t: tour.steps.length }))}</p>
      <h2 id="hub-tour-title">${esc(t(tt))}</h2><p id="hub-tour-body">${esc(t(tb))}</p>
      <div class="hub-tour-actions"><button type="button" class="secondary-button" data-tour="skip">${esc(t('js.tour_skip'))}</button>
      <span></span>${i > 0 ? `<button type="button" class="secondary-button" data-tour="back">${esc(t('js.tour_back'))}</button>` : ''}
      <button type="button" class="primary-button" data-tour="next">${esc(last ? t('js.tour_done') : t('js.tour_next'))}</button></div>`;
    if (target) {
      const r = target.getBoundingClientRect();
      const top = Math.min(window.innerHeight - 220, r.bottom + 12);
      const left = Math.max(12, Math.min(window.innerWidth - 340, r.left));
      tour.box.style.top = `${Math.max(12, top)}px`;
      tour.box.style.left = `${left}px`;
    }
    tour.box.querySelector('[data-tour="next"]').focus();
    saveTour(`step:${i}`);
  }

  function endTour(state) {
    if (!tour) return;
    tour.box.remove();
    document.querySelectorAll('.hub-tour-target').forEach((x) => x.classList.remove('hub-tour-target'));
    tour = null;
    if (state) saveTour(state);
    if (opener && opener.focus) opener.focus();
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-hub-tour-start]');
    if (b) { e.preventDefault(); menus.forEach((d) => { d.open = false; }); startTour(0); }
    const s = e.target.closest('[data-step-action]');
    if (s) {
      e.preventDefault();
      if (s.dataset.stepAction === '#search') open();
      if (s.dataset.stepAction === '#theme') {
        const m = document.querySelector('.hub-user-menu');
        if (m) { m.open = true; (m.querySelector('.hub-theme-btn[aria-checked=true]') || m.querySelector('.hub-seg-btn'))?.focus(); }
      }
    }
  });
  // Primeiro acesso ao Início (ou retomada pedida com ?tour=1).
  if (!inApp && location.pathname === '/') {
    const st = body.dataset.hubTour || '';
    const asked = /[?&]tour=1\b/.test(location.search);
    if (asked || st === '' || /^step:/.test(st)) {
      const steps = Pure.tourSteps(Object.keys(TOUR).filter((k) => visibleTarget(k)));
      setTimeout(() => startTour(asked ? 0 : Pure.resumeAt(st, steps.length)), 400);
    }
  }

  // ---- Inbox ao vivo ---------------------------------------------------------------
  const badge = document.querySelector('[data-hub-unread]');
  const bell = document.querySelector('.hub-notif-menu');
  function setUnread(n) {
    if (!badge || !bell) return;
    const prev = Number(bell.dataset.hubNotifications || 0);
    bell.dataset.hubNotifications = String(n);
    badge.textContent = String(Math.min(n, 99));
    badge.classList.toggle('hidden', n <= 0);
    const s = bell.querySelector('summary');
    if (s) s.setAttribute('aria-label', (T['shell.inbox_label'] || 'Inbox: {n}').replace('{n}', n));
    const u = document.querySelector('[data-inbox-unread]');
    if (u) u.textContent = u.textContent.replace(/\d+\s*$/, String(n));
    if (n > prev) announce(t(n - prev === 1 ? 'js.new_items_one' : 'js.new_items', { n: n - prev }));
    document.dispatchEvent(new CustomEvent('hub:inbox', { detail: { unread: n, prev } }));
  }
  if (!inApp && bell && typeof EventSource !== 'undefined') {
    const S = window.HubLogShare;
    const onLine = (d) => { if (d && typeof d.unread === 'number') setUnread(d.unread); };
    const share = S
      ? S.createLogShare({ name: 'hub-inbox', event: 'inbox', openSource: () => new EventSource('/api/me/stream'), onLine })
      : (() => {
        let src = null;
        return {
          start() { src = new EventSource('/api/me/stream'); src.addEventListener('inbox', (ev) => onLine(JSON.parse(ev.data))); },
          stop() { if (src) src.close(); },
        };
      })();
    share.start();
    window.addEventListener('pagehide', () => share.stop());
  }
  // ---- Rail da plataforma: expandido / recolhido / oculto ------------------------------
  // Botão no início do header (e Ctrl+B fora de campos de texto) alterna os 3 modos em
  // ciclo. A escolha fica à parte dentro dos apps (`app`) e fora (`hub`): localStorage
  // (falhas ignoradas) espelhado no cookie `hub_rail_<ctx>`, que o servidor usa para já
  // desenhar a próxima página no modo certo.
  const NARROW_Q = '(max-width: 1023px), (pointer: coarse) and (max-height: 600px)';
  (function railToggle() {
    const btn = document.querySelector('[data-hub-rail-toggle]');
    if (!btn) return;
    const ctx = ['app', 'docs'].includes(btn.dataset.railCtx) ? btn.dataset.railCtx : 'hub';
    const MODES = ['expanded', 'collapsed', 'hidden'];
    const key = `hub-rail-mode:${ctx}`;
    const current = () => MODES.find((m) => body.classList.contains(`rail-${m}`)) || ({ app: 'hidden', docs: 'collapsed' }[ctx] || 'expanded');
    const nextOf = (m) => MODES[(MODES.indexOf(m) + 1) % MODES.length];
    function apply(m, persist) {
      MODES.forEach((x) => body.classList.toggle(`rail-${x}`, x === m));
      btn.setAttribute('aria-expanded', String(m !== 'hidden'));
      const label = `${t('js.rail_menu')}: ${t(`js.rail_${m}`)} — ${t('js.rail_next', { next: t(`js.rail_${nextOf(m)}`) })}`;
      btn.setAttribute('aria-label', label);
      btn.title = label;
      if (!persist) return;
      try { localStorage.setItem(key, m); } catch (_) { /* sem armazenamento */ }
      try { document.cookie = `hub_rail_${ctx}=${m}; path=/; max-age=31536000; samesite=lax`; } catch (_) { /* sem cookies */ }
    }
    let saved = null;
    try { saved = localStorage.getItem(key); } catch (_) { saved = null; }
    // Cookie perdido (ou outro navegador) mas a escolha está no localStorage: reaplica.
    if (MODES.includes(saved) && saved !== current()) apply(saved, true);
    else apply(current(), false);
    function cycle() {
      const m = nextOf(current());
      apply(m, true);
      announce(`${t('js.rail_menu')}: ${t(`js.rail_${m}`)}`);
    }
    btn.addEventListener('click', cycle);
    document.addEventListener('keydown', (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || (e.key || '').toLowerCase() !== 'b') return;
      if (window.matchMedia(NARROW_Q).matches) return; // sem rail em tela estreita
      const el = e.target;
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
      e.preventDefault();
      cycle();
    });
  })();

  // ---- Menu ☰ em tela estreita (NARROW_Q, mesma consulta do hub.css) ----------------------------------------------------
  // O rail não aparece no celular: o botão "menu" (☰, canto direito do header) abre o mesmo
  // `#hub-rail` como painel de cima para baixo logo abaixo do header (`body.hub-drawer-open`),
  // sobre um fundo escurecido; o ícone vira ✕. Fecha no ✕, ao tocar num item ou no fundo, com
  // Esc ou ao voltar para tela larga; prende o foco (botão + painel) e devolve ao botão.
  (function mobileDrawer() {
    const btn = document.querySelector('[data-hub-drawer-toggle]');
    const rail = document.getElementById('hub-rail');
    if (!btn || !rail) return;
    const mq = window.matchMedia(NARROW_Q);
    const MODES = ['expanded', 'collapsed', 'hidden'];
    let backdrop = null;
    let saved = null;
    const isOpen = () => body.classList.contains('hub-drawer-open');
    const header = document.querySelector('.platform-header');
    const setHeaderH = () => document.documentElement.style.setProperty('--hub-header-h', `${Math.round(header?.getBoundingClientRect().height || 41)}px`);
    setHeaderH();
    window.addEventListener('resize', setHeaderH);
    const focusables = () => [btn].concat(Array.from(rail.querySelectorAll('a[href], button:not([disabled]), select, input, [tabindex]:not([tabindex="-1"])'))
      .filter((el) => el.getClientRects().length > 0));
    let zSaved = '';
    let zPrio = '';
    function setOpen(open, restoreFocus = true) {
      if (open === isOpen()) return;
      body.classList.toggle('hub-drawer-open', open);
      btn.setAttribute('aria-expanded', String(open));
      btn.setAttribute('aria-label', (open ? btn.dataset.labelClose : btn.dataset.labelOpen) || '');
      if (open) {
        // Dentro dos apps o hub.js fixa o z-index do rail inline (!important, contenção do
        // app); com a gaveta aberta ele precisa ficar acima do fundo escurecido.
        zSaved = rail.style.getPropertyValue('z-index');
        zPrio = rail.style.getPropertyPriority('z-index');
        if (zSaved) rail.style.setProperty('z-index', 'calc(var(--z-overlay) + 1)', 'important');
        // A gaveta usa o visual do rail expandido (sem gravar a preferência).
        document.documentElement.style.setProperty('--hub-header-h', `${Math.round(header?.getBoundingClientRect().bottom || 41)}px`);
        saved = MODES.find((m) => body.classList.contains(`rail-${m}`)) || null;
        MODES.forEach((m) => body.classList.toggle(`rail-${m}`, m === 'expanded'));
        backdrop = document.createElement('div');
        backdrop.className = 'hub-drawer-backdrop';
        backdrop.addEventListener('click', () => setOpen(false));
        body.appendChild(backdrop);
        rail.setAttribute('role', 'dialog');
        rail.setAttribute('aria-modal', 'true');
        rail.querySelectorAll('.rail-group').forEach((g) => {
          if (g.querySelector('.rail-subitem.active')) g.querySelector('[data-rail-group-toggle]')?.setAttribute('aria-expanded', 'true');
        });
        rail.scrollTop = 0;
        (rail.querySelector('[aria-current="page"]') || focusables()[1])?.focus({ preventScroll: false });
      } else {
        if (saved) MODES.forEach((m) => body.classList.toggle(`rail-${m}`, m === saved));
        if (zSaved) rail.style.setProperty('z-index', zSaved, zPrio);
        backdrop?.remove();
        backdrop = null;
        rail.removeAttribute('role');
        rail.removeAttribute('aria-modal');
        if (restoreFocus) btn.focus();
      }
    }
    btn.addEventListener('click', () => setOpen(!isOpen()));
    document.addEventListener('keydown', (e) => {
      if (!isOpen()) return;
      if (e.key === 'Escape') { e.preventDefault(); setOpen(false); return; }
      if (e.key !== 'Tab') return;
      const f = focusables();
      if (!f.length) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (!rail.contains(document.activeElement) && document.activeElement !== btn) { e.preventDefault(); first.focus(); } else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    rail.addEventListener('click', (e) => { if (isOpen() && e.target.closest('a[href]')) setOpen(false, false); });
    mq.addEventListener('change', () => { if (!mq.matches) setOpen(false, false); });
    window.addEventListener('pageshow', () => setOpen(false, false));
    window.addEventListener('popstate', () => setOpen(false, false));
  })();

  // ---- Seções administrativas do rail (recolhíveis) -----------------------------------
  // Estado por usuário no localStorage (falhas ignoradas); a seção da página atual
  // sempre abre na carga; as demais começam fechadas. No rail recolhido cada seção mostra só o ícone (com dica) e
  // divisores entre as seções; o recolhimento vale só no rail expandido.
  (function railGroups() {
    const menu = document.getElementById('hub-admin-menu');
    if (!menu) return;
    const who = (document.querySelector('.user-copy strong')?.textContent || '').trim();
    const key = `hub-admin-groups:${who}`;
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (_) { saved = {}; }
    const set = (btn, open) => btn.setAttribute('aria-expanded', String(open));
    menu.querySelectorAll('[data-rail-group]').forEach((g) => {
      const btn = g.querySelector('[data-rail-group-toggle]');
      if (!btn) return;
      const hasActive = !!g.querySelector('.rail-subitem.active');
      // Padrão: só a seção da página atual aberta (o rail cabe a 1280×720); a escolha do
      // usuário (abrir/fechar) fica lembrada.
      set(btn, hasActive || saved[g.dataset.railGroup] === true);
      btn.addEventListener('click', () => {
        const open = btn.getAttribute('aria-expanded') !== 'true';
        set(btn, open);
        saved[g.dataset.railGroup] = open;
        try { localStorage.setItem(key, JSON.stringify(saved)); } catch (_) { /* sem armazenamento */ }
      });
    });
  })();
})();

/* Modo de desenvolvimento (scripts/dev-mode.sh reload-on): só quando o servidor põe
 * <meta name="hub-dev-reload"> (HUB_DEV_MODE=1 + HUB_DEV_RELOAD=1; nunca em produção).
 * Escuta o SSE /_dev/reload: "reload" (asset alterado/app reiniciado) recarrega a página;
 * "hello" traz o id da partida do hub — se mudou desde a primeira conexão, o hub foi
 * reiniciado (binário novo) e a página também recarrega. Reconexão do próprio EventSource. */
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const meta = document.querySelector('meta[name="hub-dev-reload"]');
  if (!meta || typeof EventSource === 'undefined') return;
  let boot = null;
  const es = new EventSource(meta.getAttribute('content') || '/_dev/reload');
  es.addEventListener('hello', (e) => {
    if (boot === null) boot = e.data;
    else if (e.data !== boot) location.reload();
  });
  es.addEventListener('reload', () => location.reload());
})();

/* ---- Filtros por coluna, estilo planilha (`data-hub-colfilter`; guia em docs/DESIGN-TOKENS.md) ----
 * <table data-hub-colfilter data-hub-colfilter-empty="Texto">; cada <th data-hub-col="chave"
 * data-hub-col-label="Rótulo" [data-hub-col-type="list|date"]> ganha um funil que abre um
 * popover (no celular, folha de baixo para cima): ordenar, lista de valores com busca e
 * "Selecionar todos" (ou intervalo de datas), Limpar/Aplicar. O valor de cada célula vem de
 * `data-hub-value` (datas em ms) ou do texto. Estado na URL (`f.<chave>=a,b`, `f.<chave>=de..até`,
 * `sort=<chave>:asc|desc`) — compartilhável. Linha "Filtros ativos" acima da tabela; no
 * layout de cartões (sem cabeçalho), o botão "Filtros" abre as mesmas opções. */
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const body = document.body;
  if (!body) return;
  let T = {};
  try { T = JSON.parse(body.dataset.hubI18n || '{}'); } catch (_) { T = {}; }
  const t = (k, d) => T[k] || d;
  const L = {
    filter: t('js.cf_filter', 'Filtrar'), asc: t('js.cf_asc', 'Ordenar A → Z'), desc: t('js.cf_desc', 'Ordenar Z → A'),
    search: t('js.cf_search', 'Buscar valores'), all: t('js.cf_all', 'Selecionar todos'), clear: t('js.cf_clear', 'Limpar'),
    apply: t('js.cf_apply', 'Aplicar'), from: t('js.cf_from', 'De'), to: t('js.cf_to', 'Até'), active: t('js.cf_active', 'Filtros ativos:'),
    clearAll: t('js.cf_clear_all', 'Limpar tudo'), filters: t('js.cf_filters', 'Filtros'), remove: t('js.cf_remove', 'Remover filtro'),
    empty: t('js.cf_empty', 'Nenhum resultado com estes filtros.'), close: t('js.cf_close', 'Fechar'),
  };
  const FUNNEL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16l-6 7.5V19l-4 1.5v-8Z"/></svg>';
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const dayMs = (s, end) => { if (!s) return null; const d = new Date(s + 'T00:00:00'); return d.getTime() + (end ? 86399999 : 0); };

  function setup(table) {
    const heads = [...table.querySelectorAll('thead th')];
    const cols = heads.map((th, i) => ({ th, i, key: th.dataset.hubCol, label: th.dataset.hubColLabel || th.textContent.trim(), type: th.dataset.hubColType || 'list' })).filter((c) => c.key);
    if (!cols.length || !table.tBodies[0]) return;
    const tbody = table.tBodies[0];
    const rows = [...tbody.rows];
    const value = (r, c) => { const td = r.cells[c.i]; return td ? (td.dataset.hubValue != null ? td.dataset.hubValue : td.textContent.trim()) : ''; };
    const text = (r, c) => { const td = r.cells[c.i]; return td ? td.textContent.trim().replace(/\s+/g, ' ') : ''; };
    const state = { f: {}, sort: null };
    const url = new URL(location.href);
    for (const c of cols) {
      const v = url.searchParams.get('f.' + c.key);
      if (v) state.f[c.key] = c.type === 'date' ? v : v.split(',').filter(Boolean);
    }
    const sp = (url.searchParams.get('sort') || '').split(':');
    if (cols.some((c) => c.key === sp[0])) state.sort = { key: sp[0], dir: sp[1] === 'desc' ? 'desc' : 'asc' };

    const bar = el('div', 'hub-cf-bar');
    bar.hidden = true;
    bar.setAttribute('data-hub-cf-bar', '');
    const mobileBtn = el('button', 'secondary-button hub-cf-mobile');
    mobileBtn.type = 'button';
    mobileBtn.innerHTML = FUNNEL;
    mobileBtn.append(' ' + L.filters);
    mobileBtn.setAttribute('aria-haspopup', 'dialog');
    const top = el('div', 'hub-cf-top');
    top.append(mobileBtn, bar);
    table.parentNode.insertBefore(top, table);
    const emptyRow = el('div', 'hub-cf-empty', table.dataset.hubColfilterEmpty || L.empty);
    emptyRow.hidden = true;
    table.parentNode.insertBefore(emptyRow, table.nextSibling);

    const labelOf = (c, v) => { const r = rows.find((x) => value(x, c) === v); return r ? text(r, c) : v; };
    const pass = (r) => cols.every((c) => {
      const f = state.f[c.key];
      if (!f) return true;
      const v = value(r, c);
      if (c.type === 'date') {
        const [a, b] = String(f).split('..');
        const n = Number(v); const lo = dayMs(a, false); const hi = dayMs(b, true);
        return (lo == null || n >= lo) && (hi == null || n <= hi);
      }
      return f.includes(v);
    });
    function apply() {
      let shown = 0;
      for (const r of rows) { const ok = pass(r); r.hidden = !ok; if (ok) shown++; }
      if (state.sort) {
        const c = cols.find((x) => x.key === state.sort.key);
        const k = (r) => { const v = value(r, c); const n = Number(v); return v !== '' && !isNaN(n) ? n : text(r, c).toLowerCase(); };
        const sorted = [...rows].sort((a, b) => { const x = k(a), y = k(b); const d = x < y ? -1 : x > y ? 1 : 0; return state.sort.dir === 'desc' ? -d : d; });
        sorted.forEach((r) => tbody.appendChild(r));
      }
      emptyRow.hidden = shown > 0 || rows.length === 0;
      for (const c of cols) {
        const on = !!state.f[c.key];
        c.btn.classList.toggle('on', on);
        c.btn.setAttribute('aria-pressed', String(on));
        c.th.setAttribute('aria-sort', state.sort && state.sort.key === c.key ? (state.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
      }
      bar.textContent = '';
      const act = cols.filter((c) => state.f[c.key]);
      bar.hidden = !act.length;
      top.classList.toggle('has-active', act.length > 0);
      if (act.length) {
        bar.append(el('span', 'hub-cf-bar-label', L.active));
        for (const c of act) {
          const f = state.f[c.key];
          const desc = c.type === 'date' ? String(f).replace('..', ' – ') : f.map((v) => labelOf(c, v)).join(', ');
          const chip = el('span', 'hub-cf-chip');
          const lab = el('span', null, c.label + ': ' + desc);
          lab.title = c.label + ': ' + desc;
          chip.append(lab);
          const x = el('button', 'hub-cf-chip-x', '✕');
          x.type = 'button';
          x.setAttribute('aria-label', L.remove + ': ' + c.label);
          x.addEventListener('click', () => { delete state.f[c.key]; commit(); });
          chip.append(x);
          bar.append(chip);
        }
        const all = el('button', 'hub-cf-clear-all', L.clearAll);
        all.type = 'button';
        all.addEventListener('click', () => { state.f = {}; commit(); });
        bar.append(all);
      }
    }
    function commit() {
      const u = new URL(location.href);
      [...u.searchParams.keys()].filter((k) => k.startsWith('f.') || k === 'sort').forEach((k) => u.searchParams.delete(k));
      for (const [k, v] of Object.entries(state.f)) u.searchParams.set('f.' + k, Array.isArray(v) ? v.join(',') : v);
      if (state.sort) u.searchParams.set('sort', state.sort.key + ':' + state.sort.dir);
      history.replaceState(history.state, '', u.pathname + u.search + u.hash);
      apply();
    }

    let open = null;
    function close(focus) {
      if (!open) return;
      const { pop, back, btn } = open;
      pop.remove(); if (back) back.remove();
      open = null;
      if (focus && btn) btn.focus();
    }
    function sheetHead(pop, title) {
      const head = el('div', 'hub-cf-head');
      head.append(el('strong', null, title));
      const x = el('button', 'hub-cf-close', '✕');
      x.type = 'button'; x.setAttribute('aria-label', L.close);
      x.addEventListener('click', () => close(true));
      head.append(x);
      pop.append(head);
    }
    function panel(c, btn) {
      close(false);
      const sheet = window.matchMedia('(max-width: 767px)').matches;
      const pop = el('div', 'hub-cf-pop' + (sheet ? ' hub-cf-sheet' : ''));
      pop.setAttribute('role', 'dialog');
      pop.setAttribute('aria-label', L.filter + ': ' + c.label);
      pop.setAttribute('data-hub-cf-pop', c.key);
      sheetHead(pop, c.label);
      const sorts = el('div', 'hub-cf-sorts');
      for (const [dir, lab] of [['asc', L.asc], ['desc', L.desc]]) {
        const b = el('button', 'hub-cf-sort' + (state.sort && state.sort.key === c.key && state.sort.dir === dir ? ' active' : ''), lab);
        b.type = 'button';
        b.addEventListener('click', () => { state.sort = { key: c.key, dir }; commit(); close(true); });
        sorts.append(b);
      }
      pop.append(sorts);
      let read;
      if (c.type === 'date') {
        const [a, b] = String(state.f[c.key] || '').split('..');
        const wrap = el('div', 'hub-cf-dates');
        const mk = (lab, v) => { const l = el('label', 'hub-cf-date'); l.append(el('span', null, lab)); const i = el('input'); i.type = 'date'; i.value = v || ''; l.append(i); wrap.append(l); return i; };
        const ia = mk(L.from, a); const ib = mk(L.to, b);
        pop.append(wrap);
        read = () => (ia.value || ib.value ? (ia.value || '') + '..' + (ib.value || '') : null);
      } else {
        const vals = [...new Set(rows.map((r) => value(r, c)))].filter((v) => v !== '').sort((p, q) => labelOf(c, p).localeCompare(labelOf(c, q)));
        const cur = state.f[c.key];
        const list = el('div', 'hub-cf-list');
        if (vals.length > 8) {
          const s = el('input', 'hub-cf-search'); s.type = 'search'; s.placeholder = L.search; s.setAttribute('aria-label', L.search);
          s.addEventListener('input', () => { const q = s.value.toLowerCase(); list.querySelectorAll('label').forEach((lb) => { lb.hidden = !lb.textContent.toLowerCase().includes(q); }); });
          pop.append(s);
        }
        const allL = el('label', 'hub-cf-opt hub-cf-all');
        const allC = el('input'); allC.type = 'checkbox';
        allL.append(allC, el('span', null, L.all));
        pop.append(allL);
        const boxes = vals.map((v) => {
          const lb = el('label', 'hub-cf-opt'); const cb = el('input'); cb.type = 'checkbox'; cb.value = v;
          cb.checked = !cur || cur.includes(v);
          lb.append(cb, el('span', null, labelOf(c, v)));
          list.append(lb);
          return cb;
        });
        const sync = () => { const n = boxes.filter((b) => b.checked).length; allC.checked = n === boxes.length; allC.indeterminate = n > 0 && n < boxes.length; };
        boxes.forEach((b) => b.addEventListener('change', sync));
        allC.addEventListener('change', () => { boxes.forEach((b) => { if (!b.closest('label').hidden) b.checked = allC.checked; }); sync(); });
        sync();
        pop.append(list);
        read = () => { const sel = boxes.filter((b) => b.checked).map((b) => b.value); return sel.length === boxes.length ? null : sel; };
      }
      const acts = el('div', 'hub-cf-actions');
      const clr = el('button', 'secondary-button', L.clear); clr.type = 'button';
      clr.addEventListener('click', () => { delete state.f[c.key]; commit(); close(true); });
      const ok = el('button', 'primary-button', L.apply); ok.type = 'button';
      ok.addEventListener('click', () => { const v = read(); if (v == null) delete state.f[c.key]; else state.f[c.key] = v; commit(); close(true); });
      acts.append(clr, ok);
      pop.append(acts);
      let back = null;
      if (sheet) {
        back = el('div', 'hub-cf-backdrop');
        back.addEventListener('click', () => close(true));
        document.body.append(back, pop);
      } else {
        document.body.append(pop);
        const r = btn.getBoundingClientRect();
        const w = pop.offsetWidth;
        pop.style.top = Math.round(r.bottom + 4 + window.scrollY) + 'px';
        pop.style.left = Math.round(Math.max(8 + window.scrollX, Math.min(r.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - w - 8))) + 'px';
      }
      open = { pop, back, btn };
      const first = pop.querySelector('input,button.hub-cf-sort');
      if (first) first.focus();
    }
    mobileBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      close(false);
      const back = el('div', 'hub-cf-backdrop');
      const pop = el('div', 'hub-cf-pop hub-cf-sheet');
      pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', L.filters);
      sheetHead(pop, L.filters);
      for (const c of cols) {
        const b = el('button', 'hub-cf-colpick' + (state.f[c.key] ? ' on' : ''), c.label); b.type = 'button';
        b.addEventListener('click', (ev) => { ev.stopPropagation(); panel(c, mobileBtn); });
        pop.append(b);
      }
      back.addEventListener('click', () => close(true));
      document.body.append(back, pop);
      open = { pop, back, btn: mobileBtn };
      const f = pop.querySelector('.hub-cf-colpick');
      if (f) f.focus();
    });
    for (const c of cols) {
      const b = el('button', 'hub-cf-btn');
      b.type = 'button';
      b.innerHTML = FUNNEL;
      b.setAttribute('aria-label', L.filter + ': ' + c.label);
      b.title = L.filter + ': ' + c.label;
      b.setAttribute('aria-haspopup', 'dialog');
      b.setAttribute('data-hub-cf-col', c.key);
      b.addEventListener('click', (e) => { e.stopPropagation(); if (open && open.btn === b) close(true); else panel(c, b); });
      const wrap = el('span', 'hub-cf-th');
      while (c.th.firstChild) wrap.append(c.th.firstChild);
      wrap.append(b);
      c.th.append(wrap);
      c.btn = b;
    }
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && open) close(true); });
    document.addEventListener('click', (e) => { if (open && !open.back && !open.pop.contains(e.target)) close(false); });
    apply();
  }
  document.querySelectorAll('table[data-hub-colfilter]').forEach(setup);
})();

/* ---- Menus <details class="hub-row-menu">: setas ↑/↓ percorrem os itens; ao abrir pelo
 * teclado, o foco vai para o primeiro item. ---- */
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const items = (d) => [...d.querySelectorAll('.hub-row-menu-list button:not(:disabled), .hub-row-menu-list a[href]')];
  document.addEventListener('keydown', (e) => {
    const d = e.target.closest && e.target.closest('details.hub-row-menu');
    if (!d || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return;
    if (!d.open) { if (e.key === 'ArrowDown' && e.target.tagName === 'SUMMARY') { e.preventDefault(); d.open = true; const f = items(d)[0]; if (f) f.focus(); } return; }
    const list = items(d);
    if (!list.length) return;
    e.preventDefault();
    const i = list.indexOf(document.activeElement);
    const n = e.key === 'ArrowDown' ? (i + 1) % list.length : (i <= 0 ? list.length - 1 : i - 1);
    list[n].focus();
  });

  // Onda 13 (PWA): service worker do hub (casco + página offline; nunca guarda respostas
  // autenticadas). Só em contexto seguro (HTTPS ou localhost).
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => { navigator.serviceWorker.register('/hub-sw.js', { scope: '/' }).catch(() => {}); });
  }
})();

/* ---- Skeleton ao navegar entre páginas do hub (Início, Aplicações, Status, conta, Administração):
 * ao clicar num link do hub, o conteúdo vira o esqueleto da página (cabeçalho + cartões ou
 * tabela, mesma largura do `.page-container`: sem salto de layout) até o servidor responder.
 * Sem JS, a navegação é a normal. Animação desligada em `prefers-reduced-motion` (hub.css). ---- */
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const root = document.getElementById('app-root');
  const body = document.body;
  if (!root || !body || body.dataset.hubUx !== '1') return;
  let T = {};
  try { T = JSON.parse(body.dataset.hubI18n || '{}'); } catch (_) { T = {}; }
  const CARDS = /^\/(applications|status|account\/settings|admin)?$/;
  const hubPage = (p) => p === '/' || /^\/(applications|status|inbox|search|account\/(settings|tokens|authorized-apps))$/.test(p) || /^\/admin(\/[a-z-]+)?$/.test(p);
  const el = (cls, tag, kids) => {
    const n = document.createElement(tag || 'i');
    n.className = cls;
    (kids || []).forEach((k) => n.appendChild(k));
    return n;
  };
  const blk = (cls) => el('skeleton-block ' + cls);
  const many = (n, f) => Array.from({ length: n }, f);
  function build(path) {
    const page = el('page-container component-loading hub-page-skeleton', 'div');
    page.setAttribute('role', 'status');
    page.setAttribute('aria-busy', 'true');
    page.appendChild(el('skeleton-page-heading', 'div', [el('', 'div', [blk('eyebrow-skeleton'), blk('title-skeleton'), blk('subtitle-skeleton')]), blk('action-skeleton')]));
    if (CARDS.test(path)) {
      page.appendChild(el('skeleton-metric-row', 'div', many(3, () => blk('metric-skeleton'))));
      page.appendChild(el('skeleton-panel-row', 'div', [
        el('skeleton-panel chart-skeleton', 'section', [blk('panel-title-skeleton'), el('skeleton-chart-bars', 'div', many(6, () => el('', 'i')))]),
        el('skeleton-panel list-skeleton', 'section', [blk('panel-title-skeleton'), ...many(4, () => blk('list-line'))]),
      ]));
    } else {
      page.appendChild(el('skeleton-panel table-skeleton hub-table-sk', 'section', [blk('panel-title-skeleton'), ...many(8, () => blk('table-line'))]));
    }
    const cap = el('component-loading-caption', 'span');
    cap.textContent = T['js.loading_page'] || 'Carregando página';
    page.appendChild(cap);
    return page;
  }
  let saved = null;
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if ((a.target && a.target !== '_self') || a.hasAttribute('download') || a.hasAttribute('data-hub-no-skeleton')) return;
    let u;
    try { u = new URL(a.href, location.href); } catch (_) { return; }
    if (u.origin !== location.origin || !hubPage(u.pathname)) return;
    if (u.pathname === location.pathname && u.search === location.search) return;
    setTimeout(() => {
      if (e.defaultPrevented || saved) return;
      saved = document.createDocumentFragment();
      while (root.firstChild) saved.appendChild(root.firstChild);
      root.appendChild(build(u.pathname));
      // navegação cancelada/sem resposta: devolve a página
      setTimeout(() => { if (saved) { root.textContent = ''; root.appendChild(saved); saved = null; } }, 15000);
    }, 0);
  });
  window.addEventListener('pageshow', (e) => { if (e.persisted && saved) location.reload(); });
  document.addEventListener('change', (e) => {
    const s = e.target;
    if (s && s.matches && s.matches('select[data-hub-autosubmit]') && s.form) s.form.submit();
  });
})();

// ---- Abas da página, gaveta de detalhes da linha e filtro multisseleção (Administração) ----
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  function pick(t, focus, sync) {
    $$('[role=tab]', t.closest('[data-ptabs]')).forEach((x) => {
      const on = x === t;
      x.setAttribute('aria-selected', String(on));
      x.tabIndex = on ? 0 : -1;
      const p = document.getElementById(x.getAttribute('aria-controls'));
      if (p) p.hidden = !on;
    });
    if (focus) t.focus();
    if (sync) { const u = new URL(location.href); u.searchParams.set('tab', t.dataset.ptab); u.hash = ''; history.replaceState(null, '', u); }
  }
  $$('[data-ptabs]').forEach((g) => {
    const tabs = $$('[role=tab]', g);
    const key = new URLSearchParams(location.search).get('tab') || location.hash.slice(1);
    pick(tabs.find((x) => x.dataset.ptab === key) || tabs[0], false, false);
    tabs.forEach((x, i) => {
      x.addEventListener('click', () => pick(x, false, true));
      x.addEventListener('keydown', (e) => {
        const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (d) { e.preventDefault(); pick(tabs[(i + d + tabs.length) % tabs.length], true, true); }
      });
    });
  });

  const dr = document.querySelector('[data-rowdrawer]');
  if (dr) {
    const bd = document.querySelector('[data-rowdrawer-backdrop]');
    const body = dr.querySelector('[data-rowdrawer-body]');
    let opener = null;
    const close = () => { dr.hidden = true; bd.hidden = true; document.body.classList.remove('ops-drawer-open'); if (opener && opener.isConnected) opener.focus(); };
    const open = (tr) => {
      const src = tr.querySelector('.row-detail-src');
      if (!src) return;
      opener = tr;
      dr.querySelector('[data-rowdrawer-title]').textContent = tr.dataset.detailTitle || '';
      body.replaceChildren(...[...src.cloneNode(true).childNodes]);
      dr.hidden = false; bd.hidden = false;
      document.body.classList.add('ops-drawer-open');
      dr.querySelector('[data-rowdrawer-close]').focus();
    };
    document.addEventListener('click', (e) => {
      if (e.target.closest('[data-rowdrawer-close],[data-rowdrawer-backdrop]')) { close(); return; }
      const tr = e.target.closest('tr[data-detail-row]');
      if (tr && !e.target.closest('button,a,details,input,select,form')) open(tr);
    });
    document.addEventListener('keydown', (e) => {
      const tr = e.target.closest && e.target.closest('tr[data-detail-row]');
      if (tr && e.target === tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); open(tr); }
      if (e.key === 'Escape' && !dr.hidden && !document.querySelector('dialog[open]')) close();
    });
  }

  document.addEventListener('change', (e) => {
    const m = e.target.closest && e.target.closest('[data-multi]');
    if (!m) return;
    const on = $$('input:checked', m).map((i) => i.value);
    const lab = m.querySelector('[data-multi-label]');
    lab.textContent = on.length ? `${lab.dataset.base} (${on.length})` : lab.dataset.base;
    $$(`${m.dataset.multi} tr[data-apps]`).forEach((r) => r.classList.toggle('multi-off', on.length > 0 && !r.dataset.apps.split(' ').some((a) => on.includes(a))));
  });
})();
