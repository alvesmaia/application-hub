/* hub-safe.js — sanitizador do HTML dos apps + políticas Trusted Types (onda 12, item 2.1).
 *
 * Os apps rodam na MESMA origem do hub (sem domínio por app). Por isso nenhum HTML vindo
 * de um app entra no documento do shell sem passar por aqui:
 *   - o HTML é interpretado num documento inerte (DOMParser/`<template>`: nada executa,
 *     nada carrega) e percorrido com uma LISTA DE PERMISSÃO de elementos (HTML + SVG);
 *     os nós limpos são importados direto (sem serializar de novo → sem mXSS);
 *   - some por inteiro: <script>, <iframe>/<frame>, <object>/<embed>, <base>, <meta>
 *     (inclusive http-equiv), <link>, <style>, <noscript>, animações SVG (<set>/<animate>
 *     reescrevem atributos), <foreignObject>; elemento desconhecido é desembrulhado
 *     (fica o conteúdo, sai a marca);
 *   - atributos: `on*` (handlers inline), `srcdoc`, `is`, `ping` saem; URLs (`href`, `src`,
 *     `action`, `formaction`, `xlink:href`, `poster`, `srcset`…) só relativas, http(s),
 *     mailto/tel e `data:image/*` em imagens — `javascript:`/`vbscript:`/`data:` saem;
 *     `<use>` do SVG só referencia `#id` local; `id`/`name` que sobrescreveriam
 *     propriedades de `document`/formulários (DOM clobbering) ou ids do shell saem;
 *   - formulário (ou botão com `formaction`) que aponta para fora do próprio app —
 *     sobretudo /login, /account/*, /oauth/* — perde o destino e ganha
 *     `data-hub-blocked` (o hub.js não o envia; a CSP `form-action` também barra).
 *
 * Trusted Types (Chromium; a CSP das páginas de app exige `require-trusted-types-for
 * 'script'`): políticas `hub-parse` (só para interpretar HTML no documento inerte),
 * `hub-svg` (marcação SVG gerada pelos gráficos do hub, também limpa), `hub-script`
 * (URLs de script: só `/assets/` do hub e os scripts de app APROVADOS, com SRI) e
 * `default` (qualquer outra atribuição de HTML em string passa pelo sanitizador; script
 * inline e URL de script desconhecida são recusados). Em navegadores sem Trusted Types
 * o sanitizador continua sendo chamado explicitamente pelo hub.js/hub-kit.js.
 *
 * Sem DOM (node): `require('hub-safe.js')` devolve as regras puras (testadas em
 * js-tests/); o corpus de XSS roda num Chromium real (tests-e2e). */
(function () {
  'use strict';
  const DROP = new Set(['script', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet', 'base', 'meta', 'link', 'style', 'noscript', 'noembed', 'noframes', 'xmp', 'plaintext', 'portal', 'fencedframe', 'title', 'head', 'foreignobject', 'animate', 'animatemotion', 'animatetransform', 'set', 'discard', 'handler', 'listener', 'param', 'math', 'slot']);
  const HTML_TAGS = 'a abbr address article aside audio b bdi bdo blockquote br button caption center cite code col colgroup data datalist dd del details dfn dialog div dl dt em fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr i img input ins kbd label legend li main mark menu meter nav ol optgroup option output p picture pre progress q rp rt ruby s samp search section select small source span strong sub summary sup table tbody td template textarea tfoot th thead time tr track u ul var video wbr';
  const SVG_TAGS = 'svg g path circle ellipse line polyline polygon rect text tspan textpath defs lineargradient radialgradient stop clippath mask pattern symbol use desc marker image filter feblend fecolormatrix fecomposite feflood fegaussianblur femerge femergenode feoffset';
  const ALLOWED = new Set(`${HTML_TAGS} ${SVG_TAGS}`.split(' '));
  const URL_ATTRS = new Set(['href', 'src', 'action', 'formaction', 'xlink:href', 'poster', 'cite', 'background', 'data', 'codebase', 'longdesc', 'manifest', 'itemtype']);
  // `popover`/`popovertarget`/`command*` abririam a "top layer" sem JavaScript, acima da
  // contenção de #app-root (M1).
  const DROP_ATTRS = new Set(['srcdoc', 'is', 'ping', 'attributionsrc', 'nonce', 'popover', 'popovertarget', 'popovertargetaction', 'command', 'commandfor', 'interestfor']);
  /** Nomes de campo que sobrescreveriam propriedades do `<form>` (L4). */
  const FIELD_NAMES = new Set(['action', 'method', 'submit', 'target', 'elements', 'enctype', 'encoding', 'reset', 'requestsubmit', 'length', 'checkvalidity', 'reportvalidity', 'acceptcharset', 'novalidate', 'dataset', 'attributes', 'getattribute', 'hasattribute', 'queryselector', 'closest', 'matches']);
  /** Caminhos do hub que um formulário de app nunca alcança (credenciais, conta, OAuth,
   *  plano de controle e fragmentos de OUTROS apps). */
  const RESERVED = ['/login', '/logout', '/account', '/oauth', '/auth', '/api', '/admin', '/mcp', '/.well-known', '/_frag', '/_staging', '/assets'];
  /** Ids do shell que o app não pode reaproveitar. */
  const NAMED = new Set(['form', 'img', 'embed', 'object', 'iframe']);
  const SHELL_IDS = new Set(['app-root', 'toast', 'hub-live', 'hub-i18n', 'main']);

  /** Remove o que o navegador ignora antes do esquema (controle/espaço) e dentro dele (TAB/LF). */
  function normUrl(v) {
    return String(v == null ? '' : v).replace(/[\u0000- \u007f-\u009f]+/g, '').toLowerCase();
  }

  /** URL aceitável num atributo (`img` = contexto de imagem, aceita `data:image/*`). */
  function safeUrl(v, img) {
    const n = normUrl(v);
    const m = /^([a-z][a-z0-9+.-]*):/.exec(n);
    // Relativa (inclusive `//host`: a origem é conferida onde importa — formulários — e a
    // CSP limita imagens/conexões à própria origem).
    if (!m) return true;
    const scheme = m[1];
    if (scheme === 'http' || scheme === 'https' || scheme === 'mailto' || scheme === 'tel') return true;
    if (scheme === 'data' && img) return /^data:image\/(png|jpe?g|gif|webp|avif|bmp);/.test(n) || /^data:image\/(png|jpe?g|gif|webp|avif|bmp),/.test(n);
    return false;
  }

  /** Caminho reservado do hub? */
  function reservedPath(path) {
    const p = String(path || '').toLowerCase();
    return RESERVED.some((r) => p === r || p.startsWith(`${r}/`) || p.startsWith(`${r}?`));
  }

  /** Destino de formulário permitido: relativo/mesma origem e DENTRO do próprio app. */
  function formTargetOk(action, app, origin, base) {
    if (action == null || String(action).trim() === '') return true;
    if (!safeUrl(action, false)) return false;
    let u;
    try { u = new URL(String(action), base || origin || 'http://hub.invalid/'); } catch (_) { return false; }
    const o = origin || 'http://hub.invalid';
    if (u.origin !== o) return false;
    if (reservedPath(u.pathname)) return false;
    if (!app) return false;
    // Páginas do app em `/apps/{app}/…` (o antigo `/{app}/…` segue aceito).
    const p = u.pathname.startsWith('/apps/') ? u.pathname.slice(5) : u.pathname;
    return p === `/${app}` || p.startsWith(`/${app}/`);
  }

  /** `id`/`name` perigoso (DOM clobbering ou id do shell). `probe` = objetos a checar. */
  function clobbers(value, probe) {
    const v = String(value || '');
    if (!v) return false;
    if (SHELL_IDS.has(v) || v.startsWith('hub-')) return true;
    return (probe || []).some((o) => { try { return v in o; } catch (_) { return true; } });
  }

  /** Destino de um elemento: 'drop' | 'keep' | 'unwrap'. */
  function tagRule(name) {
    const n = String(name || '').toLowerCase();
    if (DROP.has(n)) return 'drop';
    return ALLOWED.has(n) ? 'keep' : 'unwrap';
  }

  /** Atributo permitido? (`tag` minúsculo, `name` minúsculo.) Regras de URL e formulário
   *  ficam em `clean` (precisam do contexto). */
  function attrAllowed(tag, name, value) {
    if (name.startsWith('on') || DROP_ATTRS.has(name)) return false;
    if (name === 'style' && /javascript:|expression\s*\(|-moz-binding|behavior\s*:|@import/i.test(value || '')) return false;
    if (URL_ATTRS.has(name)) {
      if (tag === 'use' || (tag === 'image' && name !== 'src')) return /^#[\w-]+$/.test(String(value || '').trim()) || (tag === 'image' && safeUrl(value, true));
      return safeUrl(value, tag === 'img' || tag === 'source' || tag === 'image' || tag === 'video' || tag === 'audio');
    }
    if (name === 'srcset') return String(value || '').split(',').every((c) => safeUrl(c.trim().split(/\s+/)[0] || '', true));
    return true;
  }

  /** Host de um link que sai da origem do hub (ou null). */
  function externalHost(href, origin, base) {
    let u;
    try { u = new URL(String(href), base || origin || 'http://hub.invalid/'); } catch (_) { return null; }
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    return u.origin !== (origin || 'http://hub.invalid') ? u.host : null;
  }


  /** Divide uma lista de seletores nas vírgulas de topo (respeita (), [] e aspas). */
  function splitSelectors(text) {
    const out = [];
    let depth = 0, quote = '', cur = '';
    for (const ch of String(text || '')) {
      if (quote) { cur += ch; if (ch === quote) quote = ''; continue; }
      if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
      if (ch === '(' || ch === '[') depth++;
      if (ch === ')' || ch === ']') depth--;
      if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; continue; }
      cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  }

  /** Seletor de app dentro de `@scope (#app-root, …)`. Dentro de `@scope` todo seletor sem
   *  `:scope` é relativo à raiz do escopo (`:scope <sel>`), então ancestrais fora da área do
   *  app deixariam de casar. Reescrita explícita:
   *  - só raiz do documento (`:root`, `html`, `body` com filtros) → `<raiz> :scope` (as
   *    variáveis do app valem só na área dele); com pseudo-elemento (`body::before`) sai;
   *  - prefixos de raiz (`:root[data-theme=dark] .x`) → `<raiz> :scope .x`;
   *  - referência à própria área (`#app-root`, `:where(#app-root)`) → `:scope`;
   *  - o resto → `:scope <sel>` (nunca alcança nada fora da área do app). */
  function scopeSelector(sel) {
    const t = String(sel || '').trim();
    if (!t) return null;
    const ROOT = /^(:root|html|body)(?![\w-])[^\s>+~]*/i;
    if (new RegExp(`${ROOT.source}$`, 'i').test(t)) return t.includes('::') ? null : `${t} :where(:scope)`;
    let rest = t;
    const pre = [];
    for (;;) {
      const m = ROOT.exec(rest);
      if (!m) break;
      const after = rest.slice(m[0].length);
      const comb = /^\s*[>+~]?\s*/.exec(after)[0];
      if (!comb.trim() && !/^\s/.test(after)) break;
      if (/[+~]/.test(comb)) break;
      pre.push(m[0]);
      rest = after.slice(comb.length);
    }
    if (pre.some((x) => x.includes('::'))) return null;
    const ar = /^(?::where\(\s*#app-root\s*\)|:is\(\s*#app-root\s*\)|#app-root)(?![\w-])([^\s>+~]*)/i.exec(rest);
    rest = ar ? `:where(:scope)${ar[1]}${rest.slice(ar[0].length)}` : `:where(:scope) ${rest}`;
    return pre.length ? `${pre.join(' ')} ${rest}` : rest;
  }

  const Rules = { externalHost, FIELD_NAMES, safeUrl, splitSelectors, scopeSelector, formTargetOk, reservedPath, tagRule, attrAllowed, clobbers, RESERVED };
  if (typeof module !== 'undefined' && module.exports && typeof document === 'undefined') {
    module.exports = Rules;
    return;
  }
  if (window.HubSafe) return;

  const tt = window.trustedTypes && window.trustedTypes.createPolicy ? window.trustedTypes : null;
  const mk = (name, rules) => { try { return tt ? tt.createPolicy(name, rules) : null; } catch (_) { return null; } };
  // Só interpreta HTML num documento SEM contexto de navegação (nada executa nem carrega).
  const parsePolicy = mk('hub-parse', { createHTML: (s) => s });
  const inert = document.implementation.createHTMLDocument('');
  const formProbe = inert.createElement('form');

  function clean(root, ctx) {
    const walker = [...root.childNodes];
    while (walker.length) {
      const node = walker.shift();
      if (node.nodeType === 8 || node.nodeType === 7) { node.remove(); continue; } // comentários/PI
      if (node.nodeType !== 1) continue;
      const tag = node.localName.toLowerCase();
      const rule = tagRule(tag);
      if (rule === 'drop') { node.remove(); ctx.removed++; continue; }
      if (rule === 'unwrap') {
        const kids = [...node.childNodes];
        node.replaceWith(...kids);
        walker.unshift(...kids);
        ctx.removed++;
        continue;
      }
      for (const a of [...node.attributes]) {
        const name = a.name.toLowerCase();
        let ok = attrAllowed(tag, name, a.value);
        // DOM clobbering: `id` contra os ids do shell/`document`; `name` só nos elementos que
        // viram propriedade de `document` (form/img/embed/object/iframe). Campos de
        // formulário mantêm `name` (title, status… são nomes legítimos de campo).
        if (ok && name === 'id' && clobbers(a.value, [document])) ok = false;
        if (ok && name === 'name' && NAMED.has(tag) && clobbers(a.value, [document, formProbe])) ok = false;
        if (ok && name === 'name' && !NAMED.has(tag) && FIELD_NAMES.has(String(a.value).toLowerCase())) ok = false;
        if (ok && name === 'action' && tag === 'form' && !formTargetOk(a.value, ctx.app, ctx.origin, ctx.base)) {
          node.setAttribute('data-hub-blocked', '');
          ok = false;
        }
        if (ok && name === 'formaction' && !formTargetOk(a.value, ctx.app, ctx.origin, ctx.base)) {
          node.setAttribute('data-hub-blocked', '');
          ok = false;
        }
        if (ok && name === 'target' && tag === 'form') ok = false; // POST em outra janela escaparia do hub.js
        if (!ok) { node.removeAttribute(a.name); ctx.removed++; }
      }
      if (tag === 'form' && !node.hasAttribute('action') && !ctx.app) node.setAttribute('data-hub-blocked', '');
      if (tag === 'a' && node.getAttribute('target')) node.setAttribute('rel', 'noopener noreferrer');
      // Link para fora do hub (L2): marcado; o hub-kit confirma antes de sair.
      if (tag === 'a' && node.hasAttribute('href')) {
        const host = externalHost(node.getAttribute('href'), ctx.origin, ctx.base);
        if (host) { node.setAttribute('data-hub-external', host); node.setAttribute('rel', 'noopener noreferrer'); }
      }
      if (tag === 'template' && node.content) clean(node.content, ctx);
      walker.unshift(...node.childNodes);
    }
    return root;
  }

  function parse(html, svg) {
    const t = inert.createElement('template');
    const src = svg ? `<svg xmlns="http://www.w3.org/2000/svg">${html}</svg>` : String(html == null ? '' : html);
    t.innerHTML = parsePolicy ? parsePolicy.createHTML(src) : src;
    return t;
  }

  /** HTML de app → `DocumentFragment` limpo, pronto para inserir. `opts.app` = id do app
   *  (formulários só para `/apps/{app}/…`). `opts.report` recebe quantas remoções houve. */
  function sanitize(html, opts) {
    const o = opts || {};
    const ctx = { app: o.app || '', origin: location.origin, base: location.href, removed: 0 };
    const t = parse(html, false);
    clean(t.content, ctx);
    if (o.report) o.report.removed = ctx.removed;
    return document.importNode(t.content, true);
  }

  /** Troca o conteúdo de `el` pelo HTML limpo (substitui `el.innerHTML = …`). */
  function setHTML(el, html, opts) {
    el.replaceChildren(sanitize(html, opts));
    return el;
  }

  /** Marcação SVG (filhos de um `<svg>`/`<g>`) → string limpa (gráficos do hub). */
  function cleanSvg(markup) {
    const t = parse(markup, true);
    const svg = t.content.firstElementChild;
    if (!svg) return '';
    clean(svg, { app: '', origin: location.origin, base: location.href, removed: 0 });
    return svg.innerHTML;
  }

  function cleanString(html) {
    const t = parse(html, false);
    clean(t.content, { app: '', origin: location.origin, base: location.href, removed: 0 });
    return t.innerHTML;
  }

  // Scripts: só os do hub e os aprovados (lista fixada uma vez pelo hub.js).
  let approved = null;
  function scriptOk(u) {
    let url;
    try { url = new URL(String(u), location.href); } catch (_) { return false; }
    if (url.origin !== location.origin) return false;
    if (url.pathname.startsWith('/assets/') && !url.pathname.includes('..')) return true;
    // Onda 13: o service worker do hub (registrado pelo hub-ux.js; a CSP só o aceita como worker).
    if (url.pathname === '/hub-sw.js' && !url.search) return true;
    return Boolean(approved && approved.has(url.pathname));
  }
  const scriptPolicy = mk('hub-script', { createScriptURL: (u) => { if (!scriptOk(u)) throw new TypeError(`script recusado: ${u}`); return u; } });
  const svgPolicy = mk('hub-svg', { createHTML: (s) => cleanSvg(s) });
  mk('default', {
    createHTML: (s) => cleanString(s),
    createScript: () => null,
    createScriptURL: (u) => (scriptOk(u) ? u : null),
  });


  /** CSS de app → folha construída confinada à área do app (onda 12, M1 da re-revisão):
   *  tudo dentro de `@scope (<escopo>)` — regras que miram o header/rail/`body` do hub não
   *  casam nada; `@import`, `@font-face` e `@namespace` saem; `@keyframes` ficam fora do
   *  escopo com o nome prefixado por `app-` (não substituem animações do hub). */
  function scopedSheet(text, scope) {
    const src = new CSSStyleSheet();
    src.replaceSync(String(text || '').replace(/@import[^;]*;/gi, ''));
    const frames = [];
    const walk = (rules) => [...rules].map(rule).filter(Boolean).join('\n');
    function body(r) { const c = r.cssText; return c.slice(c.indexOf('{')); }
    function rule(r) {
      if (typeof CSSStyleRule !== 'undefined' && r instanceof CSSStyleRule) {
        const sels = splitSelectors(r.selectorText).map(scopeSelector).filter(Boolean);
        return sels.length ? `${sels.join(', ')} ${body(r)}` : '';
      }
      if (typeof CSSMediaRule !== 'undefined' && r instanceof CSSMediaRule) return `@media ${r.conditionText} {\n${walk(r.cssRules)}\n}`;
      if (typeof CSSSupportsRule !== 'undefined' && r instanceof CSSSupportsRule) return `@supports ${r.conditionText} {\n${walk(r.cssRules)}\n}`;
      if (typeof CSSKeyframesRule !== 'undefined' && r instanceof CSSKeyframesRule) {
        frames.push(`@keyframes app-${r.name} ${body(r)}`);
        return '';
      }
      return ''; // @import, @font-face, @namespace, @page, @layer… saem
    }
    let inner = walk(src.cssRules);
    // Animações renomeadas: referências `animation(-name)` apontam para `app-<nome>`.
    for (const f of frames) {
      const name = /@keyframes app-([\w-]+)/.exec(f)[1];
      inner = inner.replace(new RegExp(`(animation(?:-name)?\\s*:[^;{}]*?)\\b${name}\\b`, 'g'), `$1app-${name}`);
    }
    const out = new CSSStyleSheet();
    out.replaceSync(`${frames.join('\n')}\n@scope (${scope}) {\n${inner}\n}`);
    return out;
  }

  window.HubSafe = Object.freeze({
    /** Folha de CSS de app confinada a `scope` (padrão: área do app + camadas do hub). */
    scopedSheet: (text, scope) => scopedSheet(text, scope || '#app-root, [data-hub-layer]'),
    sanitize,
    setHTML,
    /** HTML de SVG para `el.innerHTML` (gráficos): limpo e aceito pelo Trusted Types. */
    svgHTML: (s) => (svgPolicy ? svgPolicy.createHTML(s) : cleanSvg(s)),
    /** URL de script aceita pelo Trusted Types (só /assets/ e aprovados). */
    scriptURL: (u) => {
      if (!scriptOk(u)) throw new TypeError(`script recusado: ${u}`);
      return scriptPolicy ? scriptPolicy.createScriptURL(u) : u;
    },
    /** Fixa (uma única vez) os caminhos de script de app aprovados nesta página. */
    approve: (paths) => { if (approved === null) approved = new Set((paths || []).map(String)); },
    rules: Rules,
  });
})();
