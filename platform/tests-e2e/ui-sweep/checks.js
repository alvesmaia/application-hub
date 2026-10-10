// Varredura de UI — verificações executadas DENTRO da página (injetadas via addInitScript).
// `window.__uiSweep.run({ scope, lightTheme })` devolve uma lista de achados
// `{ check, selector, message, rect }`. Sem dependências; só leitura do DOM.
(() => {
  const JARGON = [
    [/\/api\//, 'caminho de API'],
    [/\b(POST|GET|PUT|PATCH|DELETE) \/\S*/, 'verbo HTTP'],
    [/\bHUB_[A-Z][A-Z0-9_]+/, 'variável de ambiente'],
    [/\bFAQ \d+/i, 'referência a FAQ numerada'],
    [/\bhubt_/, 'prefixo de token'],
    [/\bat \S+ \(\S+:\d+:\d+\)|panicked at|Traceback \(most recent|thread '.*' panicked/, 'stack trace'],
  ];
  const JARGON_SKIP = 'code, pre, kbd, samp, details:not([open]), details > :not(summary), [role="tooltip"], textarea, script, style, template, noscript';

  function parseColor(s) {
    if (!s) return null;
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) {
      // color(srgb r g b / a) — Chromium usa em color-mix()
      const c = s.match(/color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)(?: \/ ([\d.]+))?\)/);
      if (c) return [c[1] * 255, c[2] * 255, c[3] * 255, c[4] === undefined ? 1 : +c[4]];
      return null;
    }
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  }
  const over = (top, bottom) => {
    const a = top[3] + bottom[3] * (1 - top[3]);
    if (a === 0) return [0, 0, 0, 0];
    return [0, 1, 2].map((i) => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / a).concat(a);
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (c) => '#' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');

  /** Fundo efetivo: compõe os fundos dos ancestrais (com alfa). `null` se houver imagem/gradiente. */
  function effectiveBg(el) {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage && cs.backgroundImage !== 'none' && !/^url\(.*\.svg/.test(cs.backgroundImage)) return null;
      const c = parseColor(cs.backgroundColor);
      if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    let base = [255, 255, 255, 1];
    const rootBg = parseColor(getComputedStyle(document.documentElement).backgroundColor);
    if (rootBg && rootBg[3] > 0) base = over(rootBg, base);
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
    return base;
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return false;
    if (el.closest('[aria-hidden="true"], [hidden], [inert]')) return false;
    // recortado por clip (sr-only)
    if (r.width <= 1 && r.height <= 1) return false;
    if (cs.clip === 'rect(0px, 0px, 0px, 0px)' || cs.clipPath === 'inset(50%)') return false;
    // ancestral transparente ou que recorta o elemento por completo (grupo recolhido, aba oculta)
    for (let n = el.parentElement; n && n !== document.documentElement; n = n.parentElement) {
      const ps = getComputedStyle(n);
      if (+ps.opacity < 0.05 || ps.visibility === 'hidden') return false;
      if (ps.overflow !== 'visible' || ps.overflowX !== 'visible' || ps.overflowY !== 'visible') {
        const pr = n.getBoundingClientRect();
        if (pr.width < 1 || pr.height < 1 || r.right <= pr.left || r.left >= pr.right || r.bottom <= pr.top || r.top >= pr.bottom) return false;
      }
    }
    return true;
  }
  function opacityChain(el) {
    let o = 1;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) o *= +getComputedStyle(n).opacity;
    return o;
  }

  function sel(el) {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 4; n = n.parentElement) {
      let p = n.tagName.toLowerCase();
      if (n.id) { parts.unshift(p + '#' + n.id); break; }
      const cls = [...n.classList].filter((c) => !/^(is-|has-)/.test(c)).slice(0, 2);
      if (cls.length) p += '.' + cls.join('.');
      const ds = n.getAttribute('data-hub-modal') || n.getAttribute('name');
      if (ds) p += `[${n.hasAttribute('name') ? 'name' : 'data-hub-modal'}="${ds}"]`;
      parts.unshift(p);
    }
    return parts.join(' > ');
  }
  const rectOf = (el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
  const shortText = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 80);

  function accName(el) {
    const al = el.getAttribute('aria-label');
    if (al && al.trim()) return al.trim();
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map((id) => document.getElementById(id)?.textContent || '').join(' ').trim(); if (t) return t; }
    const t = (el.innerText || el.textContent || '').trim();
    if (t) return t;
    if (el.title && el.title.trim()) return el.title.trim();
    const img = el.querySelector('img[alt]:not([alt=""]), svg title, [aria-label]');
    if (img) return (img.getAttribute('alt') || img.getAttribute('aria-label') || img.textContent || '').trim();
    if (el.tagName === 'INPUT') return el.value || '';
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && l.textContent.trim()) return l.textContent.trim(); }
    if (el.closest('label') && el.closest('label').textContent.trim()) return el.closest('label').textContent.trim();
    return '';
  }

  function textNodesUnder(root) {
    const out = [];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
    });
    for (let n = w.nextNode(); n; n = w.nextNode()) out.push(n);
    return out;
  }

  function run(opts) {
    const scope = (opts.scope && document.querySelector(opts.scope)) || document.body;
    const lightTheme = !!opts.lightTheme;
    const F = [];
    const seen = new Set();
    const add = (check, el, message, extra) => {
      const s = sel(el);
      const k = check + '|' + s + '|' + message;
      if (seen.has(k)) return;
      seen.add(k);
      F.push({ check, selector: s, message, rect: el ? rectOf(el) : null, ...(extra || {}) });
    };

    // 1. Contraste de texto + cor igual ao fundo
    const textEls = new Set();
    for (const t of textNodesUnder(scope)) { const p = t.parentElement; if (p) textEls.add(p); }
    for (const el of scope.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]), select, textarea')) textEls.add(el);
    for (const el of textEls) {
      if (el.closest('script,style,noscript,template,svg,option')) continue;
      if (!visible(el)) continue;
      const cs = getComputedStyle(el);
      const fg = parseColor(cs.color);
      if (!fg) continue;
      const bg = effectiveBg(el);
      if (!bg) continue;
      const fgEff = over([fg[0], fg[1], fg[2], fg[3] * opacityChain(el)], bg);
      const r = ratio(fgEff, bg);
      const size = parseFloat(cs.fontSize);
      const bold = +cs.fontWeight >= 700;
      const large = size >= 24 || (bold && size >= 18.66);
      const need = large ? 3 : 4.5;
      const disabled = el.closest(':disabled, [aria-disabled="true"]');
      const txt = shortText(el.matches('input,select,textarea') ? (el.value || el.placeholder) : [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.nodeValue).join(' '));
      if (!txt) continue;
      if (r < 1.15) add('cor-igual-fundo', el, `texto "${txt}" invisível: cor ${hex(fgEff)} sobre ${hex(bg)} (${r.toFixed(2)}:1)`);
      else if (r < need && !disabled) add('contraste', el, `"${txt}" ${r.toFixed(2)}:1 < ${need}:1 (${hex(fgEff)} sobre ${hex(bg)}, ${size}px)`);
    }

    // 1b. Bordas de controles (UI ≥ 3:1) quando o controle não se distingue pelo fundo
    for (const el of scope.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=range]):not([type=submit]):not([type=button]), select, textarea')) {
      if (!visible(el)) continue;
      const cs = getComputedStyle(el);
      const own = effectiveBg(el), parent = el.parentElement && effectiveBg(el.parentElement);
      if (!own || !parent) continue;
      if (ratio(own, parent) >= 3) continue;
      const bw = parseFloat(cs.borderBottomWidth);
      const bc = parseColor(cs.borderBottomColor);
      if (!bw || !bc) { add('contraste-ui', el, 'controle sem borda nem fundo distinto'); continue; }
      const r = ratio(over(bc, parent), parent);
      if (r < 3) add('contraste-ui', el, `borda do controle ${r.toFixed(2)}:1 < 3:1 (${hex(over(bc, parent))} sobre ${hex(parent)})`);
    }

    // 2. <select> sem opções reais
    for (const s of scope.querySelectorAll('select')) {
      if (!visible(s)) continue;
      const real = [...s.options].filter((o) => o.value !== '' && !o.disabled && !o.hidden);
      if (real.length === 0) add('select-vazio', s, `select "${shortText(accName(s)) || s.name || '?'}" sem opções selecionáveis (${s.options.length} no total)`);
    }

    // 3. Botões/links sem nome acessível
    for (const el of scope.querySelectorAll('button, a[href], [role="button"], [role="menuitem"], [role="tab"], input[type=submit], input[type=button]')) {
      if (!visible(el)) continue;
      if (!accName(el)) add('sem-nome-acessivel', el, `${el.tagName.toLowerCase()} sem nome acessível`);
    }

    // 4. Links sublinhados fora de prosa
    for (const a of scope.querySelectorAll('a[href]')) {
      if (!visible(a)) continue;
      if (!getComputedStyle(a).textDecorationLine.includes('underline')) continue;
      if (a.closest('article, .prose, .markdown, .doc-body, p, .help-text, .hint, small, figcaption, blockquote')) continue;
      add('link-sublinhado', a, `link "${shortText(a.innerText)}" sublinhado fora de texto corrido`);
    }

    // 5. Controles nativos escuros no tema claro
    if (lightTheme) {
      for (const el of scope.querySelectorAll('input:not([type=hidden]), select, textarea')) {
        if (!visible(el)) continue;
        const cs = getComputedStyle(el);
        const kind = el.tagName === 'INPUT' ? `input[type=${el.type}]` : el.tagName.toLowerCase();
        if (/\bdark\b/.test(cs.colorScheme) && !/\blight\b/.test(cs.colorScheme)) { add('controle-escuro', el, `${kind} com color-scheme "${cs.colorScheme}" no tema claro (renderização nativa escura)`); continue; }
        const bg = parseColor(cs.backgroundColor);
        if (bg && bg[3] > 0.5 && lum(bg) < 0.2 && !['checkbox', 'radio'].includes(el.type)) add('controle-escuro', el, `${kind} com fundo escuro ${hex(bg)} no tema claro`);
        if (['checkbox', 'radio'].includes(el.type)) {
          if (bg && bg[3] > 0.5 && lum(bg) < 0.2 && !el.checked) add('controle-escuro', el, `${kind} desmarcado com fundo escuro ${hex(bg)} no tema claro`);
        }
      }
    }

    // 6. Ícone × rótulo desalinhados (rail, menus, navegação)
    for (const it of scope.querySelectorAll('.platform-rail a, .platform-rail button, [role="menu"] [role="menuitem"], [role="menu"] a, nav a, .app-sidebar a, .menu a, .menu button')) {
      if (!visible(it)) continue;
      const icon = it.querySelector('svg, img, .icon, [class*="icon"]');
      if (!icon || !visible(icon)) continue;
      const tn = textNodesUnder(it).find((n) => !icon.contains(n) && n.parentElement && visible(n.parentElement));
      if (!tn) continue;
      const rg = document.createRange(); rg.selectNodeContents(tn);
      const tr = rg.getBoundingClientRect(), ir = icon.getBoundingClientRect();
      if (!tr.height || !ir.height) continue;
      // Só quando lado a lado (mesma linha visual)
      if (tr.top > ir.bottom || tr.bottom < ir.top) continue;
      const d = Math.abs((tr.top + tr.height / 2) - (ir.top + ir.height / 2));
      if (d > 2) add('icone-desalinhado', it, `ícone e rótulo "${shortText(tn.nodeValue)}" desalinhados em ${d.toFixed(1)}px`);
    }

    // 7. Jargão técnico em texto visível
    for (const t of textNodesUnder(scope)) {
      const p = t.parentElement;
      if (!p || p.closest(JARGON_SKIP) || !visible(p)) continue;
      for (const [re, what] of JARGON) {
        const m = t.nodeValue.match(re);
        if (m) { add('jargao', p, `${what}: "${shortText(m[0])}" em "${shortText(t.nodeValue)}"`); break; }
      }
    }

    // 7b. Onda 13: apps vivem em /apps/{id}/… — nenhum link/formulário/comportamento para o
    // caminho antigo /{id}… (ids: os apps conhecidos + o app da página atual).
    {
      const ids = new Set(['ativos', 'fornecedores', 'servicos', 'coletor']);
      const cur = document.querySelector('script[data-hub-app]');
      if (cur && cur.dataset.hubApp) ids.add(cur.dataset.hubApp);
      const attrs = ['href', 'action', 'formaction', 'data-hub-get', 'data-hub-post', 'data-hub-poll'];
      for (const el of document.querySelectorAll(attrs.map((a) => '[' + a + ']').join(','))) {
        for (const a of attrs) {
          const v = el.getAttribute(a);
          if (!v || !v.startsWith('/') || v.startsWith('//')) continue;
          const first = v.split(/[/?#]/)[1];
          if (ids.has(first)) { add('link-sem-apps', el, a + '="' + shortText(v) + '" aponta para o caminho antigo (use /apps/' + first + '…)'); break; }
        }
      }
    }

    // 8. Texto truncado sem dica
    for (const el of textEls) {
      if (el.matches('input,select,textarea') || !visible(el)) continue;
      const cs = getComputedStyle(el);
      if (!(cs.textOverflow === 'ellipsis' || cs.overflow === 'hidden' || cs.overflowX === 'hidden' || +cs.webkitLineClamp > 0)) continue;
      const trunc = el.scrollWidth > el.clientWidth + 1 || (+cs.webkitLineClamp > 0 && el.scrollHeight > el.clientHeight + 1);
      if (!trunc) continue;
      if (el.closest('[title], [aria-label], [data-tooltip], [data-hub-tooltip]')) continue;
      add('truncado-sem-dica', el, `texto "${shortText(el.innerText)}" truncado sem title/tooltip`);
    }

    // 9. Imagens sem alt
    for (const img of scope.querySelectorAll('img')) {
      if (!visible(img)) continue;
      if (!img.hasAttribute('alt') && !img.closest('[aria-hidden="true"]') && img.getAttribute('role') !== 'presentation') add('imagem-sem-alt', img, `imagem ${img.getAttribute('src') || ''} sem alt`);
    }

    // 10. Estouro horizontal (só na página inteira)
    if (!opts.scope) {
      const de = document.documentElement;
      if (de.scrollWidth > de.clientWidth + 1) {
        let worst = null, right = de.clientWidth;
        for (const el of document.body.querySelectorAll('*')) {
          const r = el.getBoundingClientRect();
          if (r.right > right + 1 && r.width > 0 && visible(el)) { worst = el; right = r.right; }
        }
        add('estouro-horizontal', worst || de, `página com rolagem horizontal: scrollWidth ${de.scrollWidth} > ${de.clientWidth}`);
      }
      for (const el of document.querySelectorAll('main *, #app-root *')) {
        const cs = getComputedStyle(el);
        if (!/(auto|scroll)/.test(cs.overflowX) || el.matches('pre, code, textarea, .table-scroll, [data-scroll-x]')) continue;
        if (el.scrollWidth > el.clientWidth + 2 && visible(el) && el.clientWidth > 200 && !el.querySelector('table')) add('estouro-horizontal', el, `área com rolagem horizontal interna (${el.scrollWidth} > ${el.clientWidth})`);
      }
    }
    // 11. Estouro horizontal INTERNO: contêiner que recorta (overflow hidden/clip) com controles cortados
    if (!opts.scope) {
      for (const el of document.querySelectorAll('main *, #app-root *')) {
        const cs = getComputedStyle(el);
        if (!/(hidden|clip)/.test(cs.overflowX)) continue;
        if (el.scrollWidth <= el.clientWidth + 2 || !visible(el)) continue;
        const cr = el.getBoundingClientRect();
        for (const c of el.querySelectorAll('a[href], button, input, select, textarea, [role="button"], [tabindex]')) {
          if (c.closest('details:not([open]) > :not(summary), [hidden], [aria-hidden="true"]')) continue;
          const r = c.getBoundingClientRect();
          if (r.width < 1 || r.height < 1 || getComputedStyle(c).visibility === 'hidden') continue;
          if (r.right > cr.right + 1) {
            add('estouro-interno', c, `controle "${shortText(accName(c))}" cortado pela borda direita do contêiner (${Math.round(r.right)} > ${Math.round(cr.right)})`);
            break;
          }
        }
      }
    }
    return F;
  }

  /** Foco visível: compara o estilo do elemento focado com o do mesmo elemento sem foco. */
  function focusSnapshot() {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const cs = getComputedStyle(el);
    return {
      selector: sel(el), rect: rectOf(el), name: shortText(accName(el)),
      style: [cs.outlineStyle, cs.outlineWidth, cs.outlineColor, cs.boxShadow, cs.backgroundColor, cs.borderColor, cs.color, cs.textDecorationLine].join('|'),
      inShell: !!el.closest('.platform-header, .platform-rail'),
    };
  }
  function blurredStyle() {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    el.blur();
    const cs = getComputedStyle(el);
    const s = [cs.outlineStyle, cs.outlineWidth, cs.outlineColor, cs.boxShadow, cs.backgroundColor, cs.borderColor, cs.color, cs.textDecorationLine].join('|');
    return s;
  }

  window.__uiSweep = { run, focusSnapshot, blurredStyle };
})();
