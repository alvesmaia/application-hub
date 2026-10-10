// Onda 12 (2.1) — anti-phishing na mesma origem: regras puras do sanitizador (hub-safe.js)
// e dos comportamentos declarativos (hub-kit.js) em node; o CORPUS DE XSS roda num
// Chromium real (Playwright de tests-e2e/) com a CSP de página de app (Trusted Types
// obrigatório) — pulado se o Playwright/navegador não estiver instalado.
//   node --test platform/js-tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const platform = path.resolve(here, '..');
const assets = path.join(platform, 'assets/shell');
const require = createRequire(import.meta.url);
const Safe = require(path.join(assets, 'hub-safe.js'));
const Kit = require(path.join(assets, 'hub-kit.js'));

test('URLs: só relativas, http(s), mailto/tel; data:image só em imagem', () => {
  for (const ok of ['/ativos/x', 'x?y=1', '#a', 'https://ex.com/', 'http://a', 'mailto:a@b', 'tel:+55', '']) assert.ok(Safe.safeUrl(ok, false), ok);
  for (const bad of ['javascript:alert(1)', ' JaVaScRiPt:alert(1)', 'java\tscript:alert(1)', 'java\nscript:x', '\u0001javascript:x', 'vbscript:x', 'data:text/html,<script>', 'data:image/svg+xml,<svg onload=x>', 'blob:x', 'file:///c:/', 'jar:x']) assert.ok(!Safe.safeUrl(bad, false), bad);
  assert.ok(Safe.safeUrl('data:image/png;base64,AAAA', true));
  assert.ok(!Safe.safeUrl('data:image/png;base64,AAAA', false));
  assert.ok(!Safe.safeUrl('data:image/svg+xml;base64,AAAA', true));
});

test('formulários: só para o próprio app; /login, /account, /oauth e outros apps barrados', () => {
  const o = 'http://hub.test';
  const base = `${o}/ativos/dashboard`;
  for (const ok of ['', '/ativos', '/ativos/itens', 'itens', '?q=1', `${o}/ativos/x`]) assert.ok(Safe.formTargetOk(ok, 'ativos', o, base), ok);
  for (const bad of ['/login', '/LOGIN', '/logout', '/account/password', '/account/agent-approval/1', '/oauth/authorize', '/api/admin/users', '/admin', '/_frag/servicos/x', '/servicos/x', '/ativosx', 'https://evil.example/ativos/x', '//evil.example/ativos', 'javascript:alert(1)', '/ativos/../login']) {
    assert.ok(!Safe.formTargetOk(bad, 'ativos', o, base), bad);
  }
  assert.ok(!Safe.formTargetOk('/ativos/x', '', o, base), 'sem app, nada');
  // Onda 13: páginas do app em /apps/{id}/… (o antigo /{id}/… segue aceito).
  for (const ok of ['/apps/ativos', '/apps/ativos/itens', `${o}/apps/ativos/x?y=1`]) assert.ok(Safe.formTargetOk(ok, 'ativos', o, base), ok);
  for (const bad of ['/apps/servicos/x', '/apps/ativosx', '/apps', '/apps/', '/apps/ativos/../../login']) assert.ok(!Safe.formTargetOk(bad, 'ativos', o, base), bad);
  assert.ok(Safe.reservedPath('/account/x') && Safe.reservedPath('/oauth') && !Safe.reservedPath('/accounting'));
});

test('elementos e atributos: lista de permissão', () => {
  for (const t of ['script', 'iframe', 'object', 'embed', 'base', 'meta', 'link', 'style', 'noscript', 'foreignObject', 'animate', 'set', 'frame', 'math']) assert.equal(Safe.tagRule(t), 'drop', t);
  for (const t of ['div', 'form', 'table', 'svg', 'path', 'template', 'input', 'select', 'linearGradient']) assert.equal(Safe.tagRule(t), 'keep', t);
  assert.equal(Safe.tagRule('x-widget'), 'unwrap');
  assert.ok(!Safe.attrAllowed('img', 'onerror', 'x'));
  assert.ok(!Safe.attrAllowed('div', 'onclick', 'x'));
  assert.ok(!Safe.attrAllowed('iframe', 'srcdoc', 'x'));
  assert.ok(!Safe.attrAllowed('a', 'href', 'javascript:x'));
  assert.ok(!Safe.attrAllowed('use', 'href', 'https://evil/x.svg#a'));
  assert.ok(Safe.attrAllowed('use', 'href', '#icone'));
  assert.ok(!Safe.attrAllowed('div', 'style', 'background:url(javascript:x)'));
  assert.ok(Safe.attrAllowed('div', 'style', 'color:red;width:10px'));
  assert.ok(Safe.attrAllowed('div', 'data-hub-get', '/ativos/x'));
  assert.ok(!Safe.attrAllowed('img', 'srcset', 'a.png 1x, javascript:x 2x'));
  assert.ok(Safe.clobbers('app-root', []) && Safe.clobbers('hub-x', []) && Safe.clobbers('cookie', [{ cookie: 1 }]) && !Safe.clobbers('contract-docs', [{}]));
});

test('hub-kit: gatilhos, filtro sem acento, ordenação e páginas', () => {
  assert.equal(Kit.everyMs('every 5s'), 5000);
  assert.equal(Kit.everyMs('load, every 200ms'), 1000, 'mínimo de 1 s');
  assert.equal(Kit.everyMs('click'), null);
  assert.equal(Kit.defaultTrigger('form', ''), 'submit');
  assert.equal(Kit.defaultTrigger('select', ''), 'change');
  assert.equal(Kit.defaultTrigger('input', 'checkbox'), 'change');
  assert.equal(Kit.defaultTrigger('input', 'text'), 'input');
  assert.equal(Kit.defaultTrigger('button', ''), 'click');
  assert.equal(Kit.fold('Gestão ÁGIL'), 'gestao agil');
  assert.ok(Kit.compare('R$ 1.200,50', 'R$ 900,00', 'num') > 0);
  assert.ok(Kit.compare('01/02/2026', '31/01/2026', 'date') > 0);
  assert.ok(Kit.compare('item 2', 'item 10', 'text') < 0);
  assert.equal(Kit.pages(0, 10), 1);
  assert.equal(Kit.pages(21, 10), 3);
});

// ---- corpus de XSS num navegador real ----------------------------------------------
const CORPUS = [
  '<script>window.__pwned=1</script>',
  '<img src=x onerror="window.__pwned=1">',
  '<img src=x OnErRoR=window.__pwned=1>',
  '<svg onload="window.__pwned=1"></svg>',
  '<svg><script>window.__pwned=1</script></svg>',
  '<svg><animate onbegin="window.__pwned=1" attributeName="x" dur="1s"/></svg>',
  '<svg><set attributeName="onmouseover" to="window.__pwned=1"/></svg>',
  '<svg><a href="javascript:window.__pwned=1"><text>x</text></a></svg>',
  '<svg><use href="data:image/svg+xml,&lt;svg id=x onload=window.__pwned=1&gt;#x"/></svg>',
  '<a href="javascript:window.__pwned=1" id="clk">x</a>',
  '<a href=" &#14;javascript:window.__pwned=1" id="clk">x</a>',
  '<a href="jav&#x09;ascript:window.__pwned=1" id="clk">x</a>',
  '<a href="JAVASCRIPT&colon;window.__pwned=1" id="clk">x</a>',
  '<iframe src="javascript:window.parent.__pwned=1"></iframe>',
  '<iframe srcdoc="<script>parent.__pwned=1</script>"></iframe>',
  '<object data="javascript:window.__pwned=1"></object>',
  '<embed src="javascript:window.__pwned=1">',
  '<math><mtext><table><mglyph><style><img src=x onerror=window.__pwned=1></style></mglyph></table></mtext></math>',
  '<noscript><p title="</noscript><img src=x onerror=window.__pwned=1>"></noscript>',
  '<form><math><mtext></form><form><mglyph><style></math><img src onerror=window.__pwned=1>',
  '<template><img src=x onerror=window.__pwned=1></template>',
  '<div><template><script>window.__pwned=1</script></template></div>',
  '<body onload="window.__pwned=1">',
  '<details open ontoggle="window.__pwned=1">',
  '<input autofocus onfocus="window.__pwned=1">',
  '<video><source onerror="window.__pwned=1"></video>',
  '<meta http-equiv="refresh" content="0;url=javascript:window.__pwned=1">',
  '<base href="javascript:window.__pwned=1//">',
  '<link rel="import" href="data:text/html,<script>window.__pwned=1</script>">',
  '<style>@import "javascript:window.__pwned=1";</style>',
  '<div style="background:url(javascript:window.__pwned=1)">x</div>',
  '<form action="/login" method="post" id="f"><input name="password"><button id="b">x</button></form>',
  '<form action="javascript:window.__pwned=1" id="f"><button id="b">x</button></form>',
  '<form id="f"><button id="b" formaction="/oauth/authorize">x</button></form>',
  '<form action="/account/password" target="_blank" id="f"><button id="b">x</button></form>',
  '<img name="cookie"><img id="currentScript" name="currentScript">',
  '<div id="app-root">x</div>',
  '<isindex action="javascript:window.__pwned=1" type=image>',
  '<button is="x-evil" onclick="window.__pwned=1" id="clk">x</button>',
  '<!--><img src=x onerror=window.__pwned=1>-->',
  '<![CDATA[<img src=x onerror=window.__pwned=1>]]>',
  '<a href="https://ex.com" target="_blank" ping="https://evil/p">ok</a>',
];

async function browser() {
  let pw;
  try {
    const req = createRequire(path.join(platform, 'tests-e2e', 'package.json'));
    pw = req('playwright');
  } catch (_) { return null; }
  try { return await pw.chromium.launch(); } catch (_) { return null; }
}

test('corpus de XSS: nada executa, nada perigoso sobra (Chromium + CSP de app com Trusted Types)', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const page = await b.newPage();
    const csp = "default-src 'self'; script-src http://hub.test/assets/; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src http://hub.test/_frag/ativos http://hub.test/_frag/ativos/; form-action http://hub.test/ativos http://hub.test/ativos/; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; require-trusted-types-for 'script'; trusted-types hub-parse hub-svg hub-script default";
    const violations = [];
    page.on('console', (m) => { if (/Trusted Type|Content Security Policy/i.test(m.text())) violations.push(m.text()); });
    await page.route('http://hub.test/**', (route) => {
      const u = new URL(route.request().url());
      if (u.pathname === '/assets/hub-safe.js') return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.join(assets, 'hub-safe.js')) });
      if (u.pathname === '/ativos/dashboard') return route.fulfill({ contentType: 'text/html', headers: { 'content-security-policy': csp }, body: '<!doctype html><html><body><main id="app-root"></main><script src="/assets/hub-safe.js"></script></body></html>' });
      return route.fulfill({ status: 404, body: '' });
    });
    await page.goto('http://hub.test/ativos/dashboard');
    assert.equal(await page.evaluate(() => typeof window.HubSafe), 'object');
    for (const vec of CORPUS) {
      for (const how of ['setHTML', 'innerHTML']) {
        const r = await page.evaluate(async ({ vec, how }) => {
          window.__pwned = undefined;
          const root = document.getElementById('app-root');
          let threw = null;
          try {
            if (how === 'setHTML') window.HubSafe.setHTML(root, vec, { app: 'ativos' });
            else root.innerHTML = vec; // passa pela política `default` do Trusted Types
          } catch (e) { threw = String(e); }
          const clk = root.querySelector('#clk');
          try { clk && clk.click(); } catch (_) { /* nada */ }
          await new Promise((res) => setTimeout(res, 120));
          const bad = root.querySelector('script, iframe, object, embed, base, meta, link, style, noscript, animate, set, math');
          const attrs = [...root.querySelectorAll('*')].flatMap((el) => [...el.attributes].map((a) => [a.name, a.value]));
          const onAttr = attrs.find(([n]) => /^on/i.test(n));
          const jsUrl = attrs.find(([n, v]) => /href|src|action|data/i.test(n) && /^\s*javascript:/i.test(v));
          const form = root.querySelector('form');
          return {
            pwned: window.__pwned, threw, bad: bad && bad.outerHTML, onAttr, jsUrl,
            formAction: form ? form.getAttribute('action') : null,
            blocked: form ? form.hasAttribute('data-hub-blocked') || !form.getAttribute('action') : null,
            appRoot: document.querySelectorAll('#app-root').length,
            cookieIntact: typeof document.cookie === 'string',
            currentScriptOk: document.currentScript === null,
          };
        }, { vec, how });
        const ctx = `${how}: ${vec}`;
        assert.equal(r.pwned, undefined, ctx);
        assert.equal(r.bad, null, ctx);
        assert.equal(r.onAttr, undefined, ctx);
        assert.equal(r.jsUrl, undefined, ctx);
        assert.equal(r.appRoot, 1, ctx);
        assert.ok(r.cookieIntact && r.currentScriptOk, ctx);
        if (r.formAction) assert.ok(!/login|account|oauth|javascript/i.test(r.formAction), `${ctx} → ${r.formAction}`);
      }
    }
    // Conteúdo legítimo sobrevive (classes, data-hub-*, SVG, tabela, formulário do app).
    const ok = await page.evaluate(() => {
      const root = document.getElementById('app-root');
      window.HubSafe.setHTML(root, '<section class="x" data-hub-get="/ativos/a" data-hub-target="#t"><table class="data-table"><tbody><tr><td>1</td></tr></tbody></table><svg viewBox="0 0 10 10"><path d="M0 0L10 10"/><use href="#i"/></svg><form method="post" action="/ativos/itens" enctype="multipart/form-data"><input name="title" required><select name="status"></select><button>ok</button></form><a href="/ativos/x" target="_blank">x</a><template id="modal-a"><p>modal</p></template></section>', { app: 'ativos' });
      const a = root.querySelector('a');
      return {
        get: root.querySelector('[data-hub-get]')?.getAttribute('data-hub-get'),
        svg: root.querySelector('svg path') instanceof SVGElement,
        form: root.querySelector('form').getAttribute('action'),
        rel: a.getAttribute('rel'),
        tpl: root.querySelector('template').content.textContent,
        td: root.querySelector('td').textContent,
        names: [...root.querySelectorAll('input, select')].map((x) => x.getAttribute('name')).join(),
      };
    });
    assert.deepEqual(ok, { get: '/ativos/a', svg: true, form: '/ativos/itens', rel: 'noopener noreferrer', tpl: 'modal', td: '1', names: 'title,status' });
    // Trusted Types: script inline e URL de script desconhecida são recusados.
    const tt = await page.evaluate(() => {
      const out = {};
      try { const s = document.createElement('script'); s.src = 'http://hub.test/_frag/ativos/assets/x.js'; out.url = s.src ? 'aceito' : 'vazio'; } catch (e) { out.url = 'recusado'; }
      try { const s = document.createElement('script'); s.textContent = 'window.__pwned=1'; document.body.append(s); out.inline = window.__pwned === 1 ? 'executou' : 'bloqueado'; } catch (e) { out.inline = 'recusado'; }
      try { window.HubSafe.scriptURL('/_frag/ativos/assets/x.js'); out.api = 'aceito'; } catch (e) { out.api = 'recusado'; }
      return out;
    });
    assert.notEqual(tt.url, 'aceito', JSON.stringify(tt));
    assert.notEqual(tt.inline, 'executou', JSON.stringify(tt));
    assert.equal(tt.api, 'recusado');
  } finally {
    await b.close();
  }
});

// Revisão de segurança da 2.1: referências a elementos só por id (H1), nomes de campo que
// sobrescrevem o <form> (L4) e links externos marcados (L2).
test('H1: data-hub-* só aceita um id puro (nada de seletor montado com texto do app)', () => {
  for (const ok of ['lista', '#lista', 'contract-docs', '#Kb_page-2']) assert.ok(Kit.idToken(ok), ok);
  for (const bad of [', .platform-header', ', body', '#x, header', '#a #b', '.classe', '#', '', '1abc', '#a[b]', 'a:hover', '#app root', '*']) {
    assert.equal(Kit.idToken(bad), null, bad);
  }
});

test('L4/L2: campos que sobrescrevem o <form> e links externos', () => {
  for (const n of ['action', 'method', 'submit', 'target', 'elements', 'requestsubmit']) assert.ok(Safe.FIELD_NAMES.has(n), n);
  for (const n of ['name', 'title', 'status', 'q']) assert.ok(!Safe.FIELD_NAMES.has(n), n);
  const o = 'http://hub.test';
  assert.equal(Safe.externalHost('https://evil.example/x', o, `${o}/ativos`), 'evil.example');
  assert.equal(Safe.externalHost('//evil.example/x', o, `${o}/ativos`), 'evil.example');
  assert.equal(Safe.externalHost('/ativos/x', o, `${o}/ativos`), null);
  assert.equal(Safe.externalHost('mailto:a@b', o, `${o}/ativos`), null);
});

// Re-revisão (M1): CSS de app confinado com @scope.
test('CSS de app: seletores de raiz viram `<raiz> :scope`, pseudo-elementos da raiz saem', () => {
  assert.deepEqual(Safe.splitSelectors(':root:not([data-theme="light"]) :is(.a, .b), .c , [x="1,2"]'), [':root:not([data-theme="light"]) :is(.a, .b)', '.c', '[x="1,2"]']);
  assert.equal(Safe.scopeSelector(':root'), ':root :where(:scope)');
  assert.equal(Safe.scopeSelector(':root[data-theme="dark"]'), ':root[data-theme="dark"] :where(:scope)');
  assert.equal(Safe.scopeSelector('body'), 'body :where(:scope)');
  assert.equal(Safe.scopeSelector('body::before'), null);
  assert.equal(Safe.scopeSelector('html::after'), null);
  assert.equal(Safe.scopeSelector('.platform-header'), ':where(:scope) .platform-header');
  assert.equal(Safe.scopeSelector(':root .x'), ':root :where(:scope) .x');
  assert.equal(Safe.scopeSelector(':root[data-theme="dark"] :is(.a, .b)'), ':root[data-theme="dark"] :where(:scope) :is(.a, .b)');
  assert.equal(Safe.scopeSelector(':where(#app-root) a'), ':where(:scope) a');
  assert.equal(Safe.scopeSelector('body.in-app > #app-root .x'), 'body.in-app :where(:scope) .x');
  assert.equal(Safe.scopeSelector('#app-root'), ':where(:scope)');
  assert.equal(Safe.scopeSelector('body::before .x'), null);
  assert.equal(Safe.scopeSelector('bodyx'), ':where(:scope) bodyx');
});

test('CSS de app hostil não alcança o header (Chromium, @scope)', async (t) => {
  const b = await browser();
  if (!b) { t.skip('Playwright/Chromium indisponível'); return; }
  try {
    const page = await b.newPage();
    await page.setContent(`<!doctype html><html><head><style>:root{--c:blue}.platform-header{height:60px;background:#eee}</style></head><body><header class="platform-header"><span class="brand">Hub</span></header><main id="app-root"><div class="app-x">x</div></main></body></html>`);
    await page.addScriptTag({ content: fs.readFileSync(path.join(assets, 'hub-safe.js'), 'utf8') });
    const r = await page.evaluate(() => {
      const h = document.querySelector('.platform-header');
      const before = h.getBoundingClientRect().toJSON();
      const css = `.platform-header{transform:scale(0)!important;height:0!important;clip-path:inset(50%)} .platform-header *{visibility:hidden} body::before{content:"Login"} html{filter:blur(9px)} :root{--app-cor:red} .app-x{color:var(--app-cor)} @import url("https://evil.example/x.css"); @font-face{font-family:Hub;src:url(x.woff)} @keyframes spin{to{opacity:0}} .app-x{animation:spin 1s} #app-root ~ * {display:none} :root .platform-header{display:none}`;
      document.adoptedStyleSheets = [window.HubSafe.scopedSheet(css)];
      const cs = getComputedStyle(h);
      return {
        same: JSON.stringify(h.getBoundingClientRect().toJSON()) === JSON.stringify(before),
        transform: cs.transform, display: cs.display, clip: cs.clipPath,
        brandVisible: getComputedStyle(h.querySelector('.brand')).visibility,
        bodyBefore: getComputedStyle(document.body, '::before').content,
        filter: getComputedStyle(document.documentElement).filter,
        appColor: getComputedStyle(document.querySelector('.app-x')).color,
        anim: getComputedStyle(document.querySelector('.app-x')).animationName,
      };
    });
    assert.deepEqual(r, { same: true, transform: 'none', display: 'block', clip: 'none', brandVisible: 'visible', bodyBefore: 'none', filter: 'none', appColor: 'rgb(255, 0, 0)', anim: 'app-spin' });
  } finally { await b.close(); }
});
