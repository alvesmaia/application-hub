// Gera os fragmentos HTML dos três apps de demonstração a partir do mockup
// estático da raiz do repositório (index.html + js/*.js), usando as próprias
// funções de renderização do mockup dentro de um DOM jsdom.
//
// Uso (a partir de platform/demo-apps/tools):
//   npm install
//   npm run generate
//
// Saída (commitada, para que `cargo build/test` não dependa de Node):
//   ../fragments/<id>/<section>.html   workspace (sidebar + conteúdo) da seção
//   ../fragments/<id>/_skeleton.html   miolo do skeleton exibido entre seções
//   ../assets/{app2.css,app3.css}  cópias dos assets do mockup (onda 12: o gráfico
//   TimeSeriesChart agora é do hub — assets/shell/hub-chart.js)
//
// Transformações aplicadas sobre o HTML do mockup:
//   - chaves do mockup (app1/app2/app3) viram os ids do registro (ativos/...);
//   - elementos com data-route viram <a href> reais (navegação sem JS funciona);
//   - configs dos gráficos TimeSeriesChart vão no atributo data-hub-charts e as
//     interações do mockup viram atributos data-hub-* (onda 12: apps sem JavaScript;
//     ver migrate-data-hub.mjs e o hub-kit.js do shell);
//   - um marcador {{HUB_USER}} na sidebar é trocado pelo nome do usuário
//     (vindo de x-hub-user) pelo serviço hub-demo-apps em tempo de resposta.
import { JSDOM } from 'jsdom';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { migrate } from './migrate-data-hub.mjs';
import { hubContext } from './hub-context.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../..');
const outFragments = path.resolve(here, '../fragments');
const outAssets = path.resolve(here, '../assets');

const APPS = { app1: 'ativos', app2: 'fornecedores', app3: 'servicos' };

const html = fs.readFileSync(path.join(repo, 'index.html'), 'utf8')
  .replace(/<script[\s\S]*?<\/script>/g, '');
const dom = new JSDOM(html, {
  url: 'http://localhost/',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
});
const { window } = dom;
const ctx = dom.getInternalVMContext();
// Scripts clássicos compartilham o escopo léxico global (const/let entre arquivos),
// como no navegador; eval indireto não, por isso vm.Script.
const run = (code) => new vm.Script(code).runInContext(ctx);
window.scrollTo = () => {};
window.matchMedia = window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));

for (const file of ['chart.js', 'app.js', 'app2.js', 'app3.js', 'ops.js']) {
  run(fs.readFileSync(path.join(repo, 'js', file), 'utf8'));
  if (file === 'chart.js') {
    // Registra as configs de cada gráfico renderizado para serializá-las no fragmento.
    run(`(function(){const r=TimeSeriesChart.render;window.__tscConfigs=[];
      TimeSeriesChart.render=function(c){window.__tscConfigs.push(c);return r(c)};
      // Montagem (eventos, animações) é feita no navegador pelo script do app.
      TimeSeriesChart.mountAll=function(){};})()`);
  }
}

function rewriteRoute(route) {
  return route.replace(/^\/(app[123])(?=\/|$)/, (_, k) => '/' + APPS[k]);
}

function transform(markup, configs) {
  const doc = window.document;
  const host = doc.createElement('div');
  host.innerHTML = markup;
  // Onda 9 (acessibilidade): campo sem rótulo associado ganha id + for.
  let fid = 0;
  for (const f of [...host.querySelectorAll('.form-field')]) {
    const label = f.querySelector('label:not([for])');
    const ctl = f.querySelector('input:not([id]), select:not([id]), textarea:not([id])');
    if (label && ctl) {
      fid += 1;
      ctl.id = `mf-${fid}`;
      label.setAttribute('for', ctl.id);
    }
  }
  for (const el of [...host.querySelectorAll('[data-route]')]) {
    const a = doc.createElement('a');
    for (const attr of el.attributes) if (attr.name !== 'type') a.setAttribute(attr.name, attr.value);
    const href = rewriteRoute(el.getAttribute('data-route'));
    a.removeAttribute('data-route');
    a.setAttribute('href', href);
    a.innerHTML = el.innerHTML;
    el.replaceWith(a);
  }
  const bottom = host.querySelector('.app-sidebar .sidebar-bottom');
  const agent = host.querySelector('.app3-agent strong');
  if (agent) {
    agent.textContent = '{{HUB_USER}}';
  } else if (bottom) {
    const note = doc.createElement('div');
    note.className = 'hub-user-note';
    note.innerHTML = 'Conectado como <strong>{{HUB_USER}}</strong>';
    bottom.prepend(note);
  }
  let out = host.innerHTML;
  if (configs.length) {
    const json = JSON.stringify(configs).replace(/</g, '\\u003c');
    out += `<script type="application/json" data-hub-charts>${json}</script>`;
  }
  return migrate(out + '\n');
}

function renderRoute(route) {
  run(`applicationLoading=false;componentSkeletonLoading=false;
    window.__tscConfigs.length=0;history.replaceState({},'',${JSON.stringify(route)});render();`);
  return window.document.getElementById('app-root').innerHTML;
}

fs.rmSync(outFragments, { recursive: true, force: true });
let count = 0;
for (const [key, id] of Object.entries(APPS)) {
  const dir = path.join(outFragments, id);
  fs.mkdirSync(dir, { recursive: true });
  const sections = run(`applications.${key}.sections.map(s=>s[0])`);
  for (const section of sections) {
    const markup = renderRoute(`/${key}/${section}`);
    if (!markup.includes('id="workspace"')) throw new Error(`render falhou para ${key}/${section}`);
    fs.writeFileSync(path.join(dir, `${section}.html`), hubContext(id, transform(markup, [...window.__tscConfigs])));
    count++;
  }
  // Skeleton: só o miolo de .workspace-content (a sidebar já está na tela).
  const skel = window.document.createElement('div');
  skel.innerHTML = run(`componentSkeletonView('${key}','dashboard')`);
  const content = skel.querySelector('.workspace-content');
  content.querySelector('[data-mobile-menu]')?.remove();
  fs.writeFileSync(path.join(dir, '_skeleton.html'), hubContext(id, content.innerHTML + '\n'));
  fs.writeFileSync(path.join(dir, 'sections.txt'), sections.join('\n') + '\n');
}

fs.mkdirSync(outAssets, { recursive: true });
fs.copyFileSync(path.join(repo, 'css', 'app2.css'), path.join(outAssets, 'app2.css'));
fs.copyFileSync(path.join(repo, 'css', 'app3.css'), path.join(outAssets, 'app3.css'));

// Ícones usados pelo hub (loader e cards) — impresso para conferência manual.
if (process.argv.includes('--icons')) {
  for (const k of Object.keys(APPS)) console.log(k, run(`svg(applications.${k}.icon)`));
}
console.log(`${count} fragmentos gerados em ${path.relative(process.cwd(), outFragments)}`);
