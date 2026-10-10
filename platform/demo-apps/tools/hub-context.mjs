// Normaliza os fragmentos gerados do mockup para o contexto do hub (aplicado pelo
// generate-fragments.mjs na geração; rode `node hub-context.mjs` para reaplicar nos
// fragmentos já commitados):
//   - usuário, iniciais, data, saudação e nome do app viram marcadores
//     ({{HUB_USER}}, {{HUB_INITIALS}}, {{HUB_TODAY}}, {{HUB_GREETING}}, {{HUB_FIRST}},
//     {{HUB_APP_NAME}}) trocados pelo hub-demo-apps na resposta (src/i18n.rs);
//   - a sidebar dos três apps segue o mesmo molde (cabeçalho com ícone + nome do app,
//     navegação, rodapé padrão "Conectado como" + ajuda + "Voltar à plataforma"), sem
//     marcas próprias ("Service Desk", "Supply Desk") nem cartões decorativos.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const BACK_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6M9 12h11"></path></svg>';
const BOTTOM = `<div class="sidebar-bottom"><div class="hub-user-note">Conectado como <strong>{{HUB_USER}}</strong></div><div class="help-card"><strong>Precisa de ajuda?</strong><p>Consulte a documentação ou fale com o time de suporte.</p><button data-hub-toast="O suporte entrará em contato em breve.">Acessar central de ajuda →</button></div><a class="back-link" href="/applications">${BACK_SVG}<span>Voltar à plataforma</span></a></div>`;

function brandRow(svg, subtitle) {
  return `<div class="sidebar-app"><div class="sidebar-app-row"><span class="app-icon" style="background:var(--app-color,#eae6ff);color:var(--app-ink,#644ce1)">${svg}</span><span><strong>{{HUB_APP_NAME}}</strong><small>${subtitle}</small></span></div></div>`;
}

/** Transforma o HTML de um fragmento do app `id`. Idempotente. */
export function hubContext(id, html) {
  let s = html;
  // Rodapé padrão em todos os apps (o de ativos já é o molde).
  s = s.replace(/<div class="sidebar-bottom">[\s\S]*?<\/a><\/div>(\s*<\/aside>)/, `${BOTTOM}$1`);
  if (id === 'ativos') {
    s = s.replace(/<strong>Gestão de Ativos<\/strong><small>Aplicação interna<\/small>/g, '<strong>{{HUB_APP_NAME}}</strong><small>Patrimônio e equipamentos</small>');
    s = s.replace(/<div class="eyebrow" style="color:var\(--app-ink,#644ce1\)">GESTÃO DE ATIVOS<\/div>/g, '<div class="eyebrow" style="color:var(--app-ink,#644ce1)">{{HUB_APP_NAME}}</div>');
  }
  if (id === 'fornecedores') {
    s = s.replace(/<div class="app2-brand"><span class="app2-brand-mark">(<svg[\s\S]*?<\/svg>)<\/span><span><strong>SUPPLY DESK<\/strong><small>Gestão de parceiros<\/small><\/span><\/div>/, (_, svg) => brandRow(svg, 'Gestão de parceiros'));
    s = s.replace(/\s*<div class="app2-sidebar-note">[\s\S]*?<\/small><\/span><\/div>/, '');
    s = s.replace(/CONFIGURAÇÃO DO SUPPLY DESK/g, 'CONFIGURAÇÕES · {{HUB_APP_NAME}}');
    s = s.replace(/Supply Desk/g, '{{HUB_APP_NAME}}');
    s = s.replace(/<span class="app2-kicker">[^<]*· \d+ [A-Z]{3} \d{4}<\/span>/, '<span class="app2-kicker">{{HUB_TODAY}}</span>');
    s = s.replace(/<h1>Bom dia, John <span>—<\/span><\/h1>/, '<h1>{{HUB_GREETING}}, {{HUB_FIRST}}</h1>');
    s = s.replace(/<div class="app2-welcome-stamp">[\s\S]*?<\/div>/, '');
    s = s.replace(/SUPPLY DESK · /g, '{{HUB_APP_NAME}} · ');
    s = s.replace(/>SUPPLY DESK</g, '>{{HUB_APP_NAME}}<');
    s = s.replace(/<option>John Doe<\/option>/g, '<option>{{HUB_USER}}</option>');
  }
  if (id === 'servicos') {
    s = s.replace(/<div class="app3-brand"><span class="app3-brand-mark">(<svg[\s\S]*?<\/svg>)<\/span><span><strong>SERVICE DESK<\/strong><small>Operações internas<\/small><\/span><\/div>/, (_, svg) => brandRow(svg, 'Atendimento interno'));
    s = s.replace(/\s*<div class="app3-agent">[\s\S]*?<i><\/i><\/div>/, '');
    s = s.replace(/\s*<div class="app3-queue-card">[\s\S]*?<\/small><\/div>/, '');
    // Rodapé próprio do app3 → padrão.
    s = s.replace(/<div class="app3-sidebar-footer">[\s\S]*?<\/a><\/div>(\s*<\/aside>)/, `${BOTTOM}$1`);
    s = s.replace(/<span class="app3-overline">[^<]*<\/span>/, '<span class="app3-overline">{{HUB_TODAY}}</span>');
    s = s.replace(/<h1>Olá, John <i>—<\/i><\/h1>/, '<h1>{{HUB_GREETING}}, {{HUB_FIRST}}</h1>');
    s = s.replace(/<div class="app3-welcome-mark">[\s\S]*?<\/div>/, '');
    s = s.replace(/Menu do Service Desk/g, 'Menu');
    s = s.replace(/Service Desk — Nível 1/g, 'Atendimento — Nível 1');
    s = s.replace(/procedimentos do Service Desk/g, 'procedimentos desta aplicação');
    s = s.replace(/<span class="app3-agent-avatar">JD<\/span>/g, '<span class="app3-agent-avatar">{{HUB_INITIALS}}</span>');
  }
  return s;
}

// `node hub-context.mjs`: reaplica nos fragmentos commitados.
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../fragments');
  for (const id of fs.readdirSync(root)) {
    const dir = path.join(root, id);
    for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.html'))) {
      const p = path.join(dir, f);
      const before = fs.readFileSync(p, 'utf8');
      const after = hubContext(id, before);
      if (after !== before) { fs.writeFileSync(p, after); console.log('atualizado', `${id}/${f}`); }
    }
  }
}
