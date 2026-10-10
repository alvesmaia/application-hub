// Portal do desenvolvedor (/developers/*): «Copiar prompt» (data-hub-copy-prompt), copiar blocos de código
// (data-dev-copy) e filtro da página (data-dev-filter). Página do hub, não de app.
(() => {
  'use strict';
  const COPIED_MS = 2200;

  // Tema do visitante sem sessão (public_base.html, carregado no <head> para não piscar): padrão = SO
  // (data-theme="auto"); a escolha fica só no localStorage deste navegador.
  const root = document.documentElement;
  const THEME_KEY = 'hub-public-theme';
  const setTheme = (v) => {
    root.setAttribute('data-theme', v);
    const meta = document.querySelector('meta[name=color-scheme]');
    if (meta) meta.setAttribute('content', v === 'dark' ? 'dark' : v === 'auto' ? 'light dark' : 'light');
  };
  if (root.hasAttribute('data-dev-public')) {
    try { const v = localStorage.getItem(THEME_KEY); if (v === 'light' || v === 'dark') setTheme(v); } catch { /* sem storage */ }
    // Botão sol/lua (como na Cloudflare): alterna claro ↔ escuro a partir do tema em uso.
    document.addEventListener('click', (e) => {
      const btn = e.target instanceof Element ? e.target.closest('[data-dev-theme]') : null;
      if (!btn) return;
      const cur = root.getAttribute('data-theme');
      const dark = cur === 'dark' || (cur === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
      const v = dark ? 'light' : 'dark';
      setTheme(v);
      try { localStorage.setItem(THEME_KEY, v); } catch { /* sem storage */ }
    });
  }

  // Copia com a API assíncrona; fora de contexto seguro (http://ip) cai para seleção + execCommand.
  async function copyText(text, field) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* tenta o modo antigo */ }
    }
    let tmp = null;
    let el = field;
    if (!el) {
      tmp = document.createElement('textarea');
      tmp.value = text;
      tmp.setAttribute('readonly', '');
      tmp.className = 'visually-hidden';
      document.body.appendChild(tmp);
      el = tmp;
    }
    try {
      el.focus();
      el.select();
      return document.execCommand('copy');
    } catch (_) {
      return false;
    } finally {
      if (tmp) tmp.remove();
    }
  }

  function flash(btn, ok, onEnd) {
    const idle = btn.querySelector('[data-copy-idle], [data-dev-copy-idle]');
    const done = btn.querySelector('[data-copy-done], [data-dev-copy-done]');
    if (!ok) return;
    btn.classList.add('is-copied');
    if (idle) idle.hidden = true;
    if (done) done.hidden = false;
    clearTimeout(btn._hubCopyT);
    btn._hubCopyT = setTimeout(() => {
      btn.classList.remove('is-copied');
      if (idle) idle.hidden = false;
      if (done) done.hidden = true;
      if (onEnd) onEnd();
    }, COPIED_MS);
  }

  document.addEventListener('click', async (e) => {
    const t0 = e.target instanceof Element ? e.target : null;
    if (!t0) return;
    const pbtn = t0.closest('[data-hub-copy-prompt-button]');
    if (pbtn) {
      const box = pbtn.closest('[data-hub-copy-prompt]');
      const field = box && box.querySelector('[data-hub-copy-prompt-text]');
      const status = box && box.querySelector('[data-hub-copy-prompt-status]');
      if (!field) return;
      const ok = await copyText(field.value, field.hidden ? null : field);
      pbtn.focus();
      if (status) {
        const msg = ok ? box.querySelector('[data-copy-done]') : box.querySelector('[data-copy-fail]');
        status.textContent = msg ? msg.textContent : '';
      }
      box.classList.toggle('is-copied', ok);
      flash(pbtn, ok, () => { box.classList.remove('is-copied'); if (status) status.textContent = ''; });
      return;
    }
    const cbtn = t0.closest('[data-dev-copy]');
    if (cbtn) {
      const box = cbtn.closest('.dev-code');
      const pre = box && box.querySelector('pre');
      if (!pre) return;
      flash(cbtn, await copyText(pre.textContent.replace(/\n$/, '')));
    }
  });

  // Barra lateral: gaveta no celular, «/» foca o filtro, filtro esconde itens (abre os grupos com acerto).
  const norm = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  document.addEventListener('click', (e) => {
    const b = e.target instanceof Element ? e.target.closest('[data-dev-nav-toggle]') : null;
    if (!b) return;
    const open = b.getAttribute('aria-expanded') !== 'true';
    b.setAttribute('aria-expanded', String(open));
    b.closest('[data-dev-nav]').classList.toggle('is-open', open);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    const inp = document.querySelector('[data-dev-nav-filter]');
    if (!inp || !inp.offsetParent) return;
    e.preventDefault();
    inp.focus();
  });
  document.addEventListener('input', (e) => {
    const inp = e.target instanceof Element ? e.target.closest('[data-dev-nav-filter]') : null;
    if (!inp) return;
    const nav = inp.closest('[data-dev-nav]');
    const q = norm(inp.value.trim());
    let any = false;
    nav.querySelectorAll('.dev-nav-group').forEach((g) => {
      const label = norm(g.querySelector('summary').textContent);
      let hits = 0;
      g.querySelectorAll('li').forEach((li) => {
        const hit = !q || label.includes(q) || norm(li.textContent).includes(q);
        li.hidden = !hit;
        if (hit) hits += 1;
      });
      g.hidden = hits === 0;
      if (q && hits) g.open = true;
      if (hits) any = true;
    });
    const empty = nav.querySelector('[data-dev-nav-empty]');
    if (empty) empty.hidden = any;
  });

  const ready = (fn) => (document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn());
  ready(() => {
    /* tema: o botão sol/lua usa só CSS (data-theme) */
  });

  // «Nesta página»: destaca o título visível (o primeiro que cruza o topo da janela).
  ready(() => {
  const toc = document.querySelector('[data-dev-toc]');
  if (toc) {
    const links = new Map([...toc.querySelectorAll('a[href^="#"]')].map((a) => [decodeURIComponent(a.hash.slice(1)), a]));
    const heads = [...links.keys()].map((id) => document.getElementById(id)).filter(Boolean);
    // Trilho ativo (como na Cloudflare): um caminho só pelo trilho de todos os itens, curvas incluídas; o
    // trecho do item atual aparece por stroke-dasharray/-dashoffset, que a transição anima ao trocar de item.
    const NS = 'http://www.w3.org/2000/svg';
    const rail = document.createElementNS(NS, 'svg');
    rail.setAttribute('class', 'dev-toc-rail');
    rail.setAttribute('aria-hidden', 'true');
    const active = document.createElementNS(NS, 'path');
    rail.appendChild(active);
    toc.appendChild(rail);
    toc.classList.add('has-rail');
    let spans = new Map(); // link → [início no caminho, altura]
    const draw = () => {
      const all = [...links.values()];
      if (!all.length || !toc.offsetParent) return;
      const box = toc.getBoundingClientRect();
      let d = '';
      let px = null;
      let pb = 0;
      spans = new Map();
      for (const a of all) {
        const r = a.getBoundingClientRect();
        const x = r.left - box.left + parseFloat(getComputedStyle(a).borderLeftWidth) / 2;
        const top = r.top - box.top;
        if (px === null) d = `M ${x} ${top}`;
        else if (x !== px) { const m = (pb + top) / 2; d += ` C ${px} ${m}, ${x} ${m}, ${x} ${top}`; }
        else d += ` L ${x} ${top}`;
        active.setAttribute('d', d);
        spans.set(a, [active.getTotalLength(), r.height]);
        d += ` L ${x} ${top + r.height}`;
        px = x; pb = top + r.height;
      }
      active.setAttribute('d', d);
      rail.dataset.len = String(active.getTotalLength());
      place();
    };
    const place = () => {
      const cur = [...links.values()].find((a) => a.getAttribute('aria-current') === 'true');
      const s = cur && spans.get(cur);
      rail.classList.toggle('on', Boolean(s));
      if (!s) return;
      active.style.strokeDasharray = `${s[1]} ${Number(rail.dataset.len) + 1}`;
      active.style.strokeDashoffset = String(-s[0]);
      if (!rail.classList.contains('ready')) requestAnimationFrame(() => rail.classList.add('ready')); // 1º: sem animar
    };
    const mark = () => {
      // último título que já passou da linha de leitura (30 % da janela); antes do primeiro, o primeiro
      const line = innerHeight * 0.3;
      const end = innerHeight + scrollY >= document.documentElement.scrollHeight - 2; // fim da página: o último
      const cur = end ? heads.at(-1) : heads.reduce((c, h) => (h.getBoundingClientRect().top <= line ? h : c), heads[0] || null);
      links.forEach((a, id) => { if (cur && id === cur.id) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current'); });
      place();
    };
    draw();
    mark();
    addEventListener('resize', draw);
    document.fonts?.ready.then(draw);
    let pinned = 0; // clique no «Nesta página»: o item clicado fica ativo até a rolagem suave terminar
    toc.addEventListener('click', (e) => {
      const a = e.target instanceof Element ? e.target.closest('a[href^="#"]') : null;
      if (!a) return;
      links.forEach((l) => l.removeAttribute('aria-current'));
      a.setAttribute('aria-current', 'true');
      place();
      pinned = Date.now() + 1200;
    });
    let tick = 0;
    addEventListener('scroll', () => {
      if (!tick) tick = requestAnimationFrame(() => { tick = 0; if (Date.now() > pinned) mark(); });
    }, { passive: true });
  }
  });

})();
