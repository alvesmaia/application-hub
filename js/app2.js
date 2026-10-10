/* App 2 — a supplier operations workspace with its own visual language. */
(function () {
const baseAppView = window.appView;
const baseAppContent = window.appContent;
const baseAppDashboard = window.appDashboard;
const baseComponentSkeletonView = window.componentSkeletonView;
const baseAppLoadingView = window.appLoadingView;
Object.assign(applications.app2, {
  name: 'Rede de Fornecedores',
  short: 'Fornecedores',
  description: 'Gerencie parceiros, avaliações e contratos em um só lugar.',
  color: '#f6e4dc',
  ink: '#a94f36',
  icon: 'groups',
  sections: [
    ['dashboard', 'Painel', 'dashboard'],
    ['indicators', 'Fornecedores', 'groups'],
    ['analysis', 'Avaliações', 'check'],
    ['reports', 'Contratos', 'file'],
    ['settings', 'Preferências', 'settings']
  ],
  accent: 'Fornecedores ativos',
  metric: '86'
});

const supplierRows = [
  ['Norte Tecnologia', 'FOR-0284', 'Tecnologia', 'São Paulo, SP', 'Aprovado'],
  ['Logística Horizonte', 'FOR-0279', 'Logística', 'Campinas, SP', 'Em avaliação'],
  ['Casa & Forma', 'FOR-0271', 'Facilities', 'Curitiba, PR', 'Aprovado'],
  ['VerdeVivo Refeições', 'FOR-0268', 'Alimentação', 'São Paulo, SP', 'Documentação'],
  ['Atlas Segurança', 'FOR-0252', 'Serviços', 'Rio de Janeiro, RJ', 'Aprovado'],
  ['Papelaria Central', 'FOR-0246', 'Suprimentos', 'Belo Horizonte, MG', 'Em avaliação']
];

function app2Sidebar(section) {
  const app = applications.app2;
  return `<aside class="app-sidebar app2-sidebar">
    <div class="app2-brand"><span class="app2-brand-mark">${svg('groups')}</span><span><strong>SUPPLY DESK</strong><small>Gestão de parceiros</small></span></div>
    <div class="sidebar-caption">OPERAÇÃO</div>
    <nav class="sidebar-nav">${app.sections.map(([id, label, ico]) => `<button class="nav-item ${section === id ? 'active' : ''}" data-route="/app2/${id}">${svg(ico)}<span>${label}</span>${id === 'analysis' ? '<i class="app2-nav-count">4</i>' : ''}</button>`).join('')}</nav>
    <div class="app2-sidebar-note"><span class="app2-note-dot"></span><span><strong>Rede conectada</strong><small>86 parceiros ativos</small></span></div>
    <div class="sidebar-bottom"><div class="help-card"><strong>Central de suporte</strong><p>Dúvidas sobre cadastro ou homologação?</p><button data-toast="A central de suporte foi aberta.">Falar com a equipe →</button></div><button class="back-link app2-back-link" data-route="/applications">${svg('back')}<span>Voltar à plataforma</span></button></div>
  </aside>`;
}

function appView(key, section) {
  if (key !== 'app2') return baseAppView(key, section);
  const selected = sectionFor(applications.app2, section);
  return `<div class="workspace app2-workspace" id="workspace">${app2Sidebar(selected[0])}<div class="mobile-backdrop" data-mobile-close></div><section class="workspace-content app2-content"><button class="mobile-menu-button" data-mobile-menu>${svg('menu')} Navegação</button>${appContent('app2', selected[0], applications.app2)}</section></div>`;
}

function appContent(key, section, app) {
  if (key !== 'app2') return baseAppContent(key, section, app);
  if (section === 'dashboard') return app2Dashboard();
  if (section === 'indicators') return app2Suppliers();
  if (section === 'analysis') return app2Reviews();
  if (section === 'reports') return app2Contracts();
  return app2Preferences();
}

function appDashboard(key, app) {
  if (key !== 'app2') return baseAppDashboard(key, app);
  return app2Dashboard();
}

function componentSkeletonView(key, section) {
  if (key !== 'app2') return baseComponentSkeletonView(key, section);
  const selected = sectionFor(applications.app2, section);
  return `<div class="workspace app2-workspace" id="workspace">${app2Sidebar(selected[0])}<div class="mobile-backdrop" data-mobile-close></div><section class="workspace-content app2-content component-loading" aria-label="Carregando componentes do Supply Desk"><button class="mobile-menu-button" data-mobile-menu>${svg('menu')} Navegação</button><div class="skeleton-page-heading"><div><i class="skeleton-block eyebrow-skeleton"></i><i class="skeleton-block title-skeleton"></i><i class="skeleton-block subtitle-skeleton"></i></div><i class="skeleton-block action-skeleton"></i></div><div class="skeleton-metric-row"><i class="skeleton-block metric-skeleton"></i><i class="skeleton-block metric-skeleton"></i><i class="skeleton-block metric-skeleton"></i></div><div class="skeleton-panel-row"><section class="skeleton-panel chart-skeleton"><i class="skeleton-block panel-title-skeleton"></i><div class="skeleton-chart-bars"><i></i><i></i><i></i><i></i><i></i><i></i></div></section><section class="skeleton-panel list-skeleton"><i class="skeleton-block panel-title-skeleton"></i><i class="skeleton-block list-line"></i><i class="skeleton-block list-line"></i><i class="skeleton-block list-line"></i><i class="skeleton-block list-line short"></i></section></div><section class="skeleton-panel table-skeleton"><i class="skeleton-block panel-title-skeleton"></i><i class="skeleton-block table-line"></i><i class="skeleton-block table-line"></i><i class="skeleton-block table-line"></i></section><span class="component-loading-caption">Carregando Supply Desk</span></section></div>`;
}

function appLoadingView(key, section) {
  if (key !== 'app2') return baseAppLoadingView(key, section);
  return `<div class="workspace workspace-no-sidebar app2-workspace" id="workspace"><section class="workspace-content app2-content"><div class="app-loading app2-loader" role="status" aria-label="Carregando Supply Desk"><div class="loader-heading"><span class="loader-mark">${svg('groups')}</span><div><span class="loader-app-name">Supply Desk</span><span class="loader-message">Preparando sua rede de fornecedores</span></div><strong class="loader-percentage">0%</strong></div><div class="loader-track"><span class="loader-fill"></span></div><div class="loader-footer"><span>Conectando diretório de parceiros</span><span>Quase pronto</span></div></div></section></div>`;
}

function app2Dashboard() {
  return `<div class="app2-dashboard">
    <div class="app2-welcome"><div><span class="app2-kicker">QUARTA-FEIRA · 25 SET 2026</span><h1>Bom dia, John <span>—</span></h1><p>Acompanhe a saúde da sua cadeia de fornecedores.</p></div><button class="app2-primary" data-toast="O fluxo de cadastro de fornecedor foi iniciado.">${svg('plus')} Cadastrar fornecedor</button><div class="app2-welcome-stamp">SUPPLY<br><b>DESK</b><i>●</i></div></div>
    <div class="app2-metrics"><article><span>Parceiros ativos</span><strong>86</strong><small><b>+6</b> neste trimestre</small></article><article><span>Em homologação</span><strong>12</strong><small>5 aguardam documentos</small></article><article><span>Contratos a vencer</span><strong>04</strong><small class="app2-warning">Nos próximos 30 dias</small></article><article><span>Nota média da rede</span><strong>92<em>/100</em></strong><small><b>+2,4 pts</b> neste semestre</small></article></div>
    <div class="app2-dashboard-grid"><section class="app2-panel app2-volume"><div class="app2-panel-head"><div><span class="app2-kicker">VISÃO DA REDE</span><h2>Novos parceiros</h2></div><button data-toast="Período: últimos 6 meses.">Últimos 6 meses⌄</button></div><div class="app2-bars" aria-label="Novos parceiros por mês">${[['Abr',38],['Mai',58],['Jun',45],['Jul',72],['Ago',60],['Set',88]].map(([m,h],i)=>`<div class="app2-bar-col"><span>${[4,7,5,9,7,11][i]}</span><i style="height:${h}%"></i><small>${m}</small></div>`).join('')}</div><div class="app2-chart-foot"><span><i></i> Parceiros homologados</span><strong>+18% <small>vs. semestre anterior</small></strong></div></section>
      <section class="app2-panel app2-attention"><div class="app2-panel-head"><div><span class="app2-kicker">AÇÃO NECESSÁRIA</span><h2>Na sua fila</h2></div><span class="app2-badge">4 itens</span></div><button class="app2-task" data-route="/app2/analysis"><span class="app2-task-icon">${svg('check')}</span><span><strong>Avaliações pendentes</strong><small>3 parceiros aguardam revisão</small></span><b>03</b></button><button class="app2-task" data-route="/app2/reports"><span class="app2-task-icon warm">${svg('clock')}</span><span><strong>Renovações próximas</strong><small>1 contrato vence em 12 dias</small></span><b>01</b></button><button class="app2-inline-link" data-route="/app2/analysis">Abrir minha fila <span>→</span></button></section></div>
    <section class="app2-panel app2-recent"><div class="app2-panel-head"><div><span class="app2-kicker">ATIVIDADE RECENTE</span><h2>Parceiros atualizados</h2></div><button class="app2-inline-link" data-route="/app2/indicators">Ver diretório →</button></div><div class="app2-supplier-list">${supplierRows.slice(0,4).map((r,i)=>`<div class="app2-supplier-row"><span class="app2-monogram">${r[0].split(' ').map(x=>x[0]).slice(0,2).join('')}</span><span class="app2-supplier-name"><strong>${r[0]}</strong><small>${r[2]} · ${r[1]}</small></span><span class="app2-supplier-date">${['Hoje, 10:42','Hoje, 09:15','Ontem, 16:20','Ontem, 14:06'][i]}</span><span class="app2-status ${i===1?'review':i===3?'docs':''}">${i===1?'Em avaliação':i===3?'Documentação':'Aprovado'}</span></div>`).join('')}</div></section>
  </div>`;
}

function app2PageHeading(kicker, title, subtitle, action = '') {
  return `<div class="app2-page-heading"><div><span class="app2-kicker">${kicker}</span><h1>${title}</h1><p>${subtitle}</p></div>${action}</div>`;
}

function app2Suppliers() {
  const action = `<button class="app2-primary" data-toast="O fluxo de cadastro de fornecedor foi iniciado.">${svg('plus')} Novo fornecedor</button>`;
  return `<div class="app2-page">${app2PageHeading('DIRETÓRIO DE PARCEIROS','Fornecedores','Todos os parceiros da rede, seus dados e situação atual.',action)}
    <div class="app2-directory-summary"><div><strong>86</strong><span>Total de parceiros</span></div><div><strong>72</strong><span>Homologados</span></div><div><strong>12</strong><span>Em avaliação</span></div><div><strong>02</strong><span>Com pendências</span></div></div>
    <div class="app2-filter-row"><div class="app2-search">${svg('search')}<input data-table-search placeholder="Buscar empresa, código ou categoria..." aria-label="Buscar fornecedores"></div><button data-toast="Filtros de fornecedores abertos.">${svg('filter')} Filtros <span class="app2-filter-count">2</span></button><button data-toast="Exibindo todos os fornecedores.">Todas as situações⌄</button></div>
    <div class="app2-table-wrap"><table class="app2-table"><thead><tr><th>EMPRESA</th><th>CÓDIGO</th><th>CATEGORIA</th><th>LOCALIZAÇÃO</th><th>SITUAÇÃO</th><th></th></tr></thead><tbody>${supplierRows.map((r,i)=>`<tr><td><span class="app2-company"><i class="app2-monogram">${r[0].split(' ').map(x=>x[0]).slice(0,2).join('')}</i><b>${r[0]}</b></span></td><td>${r[1]}</td><td>${r[2]}</td><td>${r[3]}</td><td><span class="app2-status ${i===1||i===5?'review':i===3?'docs':''}">${r[4]}</span></td><td><button class="app2-more" aria-label="Mais ações" data-toast="Detalhes de ${r[0]} selecionados.">···</button></td></tr>`).join('')}</tbody></table><div class="app2-table-footer">Mostrando 6 de 86 fornecedores <span>‹　<b>1</b>　2　3　…　15　›</span></div></div></div>`;
}

function app2Reviews() {
  const rows = [['Norte Tecnologia','FOR-0284','Segurança e compliance',96,'Em revisão'],['Logística Horizonte','FOR-0279','Capacidade operacional',82,'Em revisão'],['VerdeVivo Refeições','FOR-0268','Documentação fiscal',64,'Aguardando dados'],['Atlas Segurança','FOR-0252','Qualidade de serviço',91,'Em revisão']];
  return `<div class="app2-page">${app2PageHeading('QUALIFICAÇÃO CONTÍNUA','Avaliações','Revise critérios e mantenha a qualidade da rede de parceiros.',`<button class="app2-outline" data-toast="Relatório de avaliações exportado.">${svg('file')} Exportar análise</button>`)}<div class="app2-review-banner"><span class="app2-review-symbol">${svg('check')}</span><div><strong>4 avaliações precisam da sua atenção</strong><small>Priorize as avaliações com prazo mais próximo para manter a homologação em dia.</small></div><button data-toast="Fila ordenada pela data limite.">Ver prazos <span>→</span></button></div><div class="app2-review-grid">${rows.map(([name,code,topic,score,status],i)=>`<article class="app2-review-card"><div class="app2-review-top"><span class="app2-monogram">${name.split(' ').map(x=>x[0]).slice(0,2).join('')}</span><span class="app2-status ${i===2?'docs':'review'}">${status}</span></div><h3>${name}</h3><p>${code} · ${topic}</p><div class="app2-score"><span>Índice de conformidade</span><b>${score}<small>/100</small></b></div><div class="app2-score-track"><i style="width:${score}%"></i></div><div class="app2-review-card-foot"><small>Prazo ${['hoje','amanhã','em 3 dias','em 5 dias'][i]}</small><button data-toast="Avaliação de ${name} aberta.">Revisar →</button></div></article>`).join('')}</div></div>`;
}

function app2Contracts() {
  const contracts = [['Serviços de cloud e infraestrutura','Norte Tecnologia','CTR-2026-041','18 out 2026','R$ 248.000','Vigente'],['Distribuição regional','Logística Horizonte','CTR-2025-118','07 nov 2026','R$ 96.500','Renovação próxima'],['Alimentação corporativa','VerdeVivo Refeições','CTR-2026-036','14 jan 2027','R$ 72.000','Vigente'],['Monitoramento patrimonial','Atlas Segurança','CTR-2025-092','22 fev 2027','R$ 184.000','Vigente']];
  return `<div class="app2-page">${app2PageHeading('ACORDOS COMERCIAIS','Contratos','Prazos, valores e documentos dos acordos com seus parceiros.',`<button class="app2-primary" data-toast="Novo contrato preparado para cadastro.">${svg('plus')} Registrar contrato</button>`)}<div class="app2-contract-totals"><div><span>Contratos vigentes</span><strong>38</strong><small>em 31 fornecedores</small></div><div><span>Valor anual contratado</span><strong>R$ 4,82 mi</strong><small>+8,6% vs. ano anterior</small></div><div class="urgent"><span>Renovação em 30 dias</span><strong>04</strong><small>1 requer ação nesta semana</small></div></div><div class="app2-contract-toolbar"><h2>Todos os contratos <span>38</span></h2><div><button data-toast="Filtros de contratos abertos.">${svg('filter')} Filtrar</button><button data-toast="Ordenação por vencimento aplicada.">Vencimento mais próximo⌄</button></div></div><div class="app2-contract-list">${contracts.map(([title,vendor,code,due,value,status],i)=>`<article class="app2-contract-row"><span class="app2-contract-icon">${svg('file')}</span><div class="app2-contract-main"><strong>${title}</strong><small>${vendor} <i>·</i> ${code}</small></div><div class="app2-contract-due"><small>VENCIMENTO</small><strong>${due}</strong></div><div class="app2-contract-value"><small>VALOR ANUAL</small><strong>${value}</strong></div><span class="app2-status ${i===1?'review':''}">${status}</span><button class="app2-more" aria-label="Abrir contrato" data-toast="Contrato ${code} aberto.">↗</button></article>`).join('')}</div></div>`;
}

function app2Preferences() {
  return `<div class="app2-page">${app2PageHeading('CONFIGURAÇÃO DO SUPPLY DESK','Preferências','Ajuste os padrões de homologação e notificações da equipe.')}
    <div class="app2-settings-layout"><nav class="app2-settings-nav"><button class="selected">Geral <span>→</span></button><button data-toast="Seção de homologação selecionada.">Homologação</button><button data-toast="Seção de notificações selecionada.">Notificações</button><button data-toast="Seção de equipe selecionada.">Equipe e acessos</button></nav><section class="app2-settings-card"><div class="app2-settings-title"><div><h2>Configurações gerais</h2><p>Defina os dados e padrões da sua operação.</p></div><span class="app2-settings-mark">${svg('settings')}</span></div><div class="app2-form-grid"><label>Nome da área<input value="Gestão de Fornecedores"></label><label>Responsável pela operação<select><option>John Doe</option><option>Mariana Costa</option><option>Rafael Mendes</option></select></label><label>Moeda padrão<select><option>Real brasileiro (BRL)</option><option>Dólar americano (USD)</option></select></label><label>Prazo padrão de avaliação<select><option>10 dias úteis</option><option>5 dias úteis</option><option>15 dias úteis</option></select></label></div><div class="app2-settings-divider"></div><div class="app2-toggle-row"><span><strong>Lembretes de vencimento</strong><small>Notificar a equipe 30 e 7 dias antes do vencimento.</small></span><button class="app2-toggle on" aria-label="Lembretes ativados" data-toast="Lembretes de vencimento ativados."><i></i></button></div><div class="app2-settings-actions"><button class="app2-outline" data-toast="Alterações descartadas.">Descartar</button><button class="app2-primary" data-toast="Preferências salvas com sucesso.">Salvar alterações</button></div></section></div></div>`;
}

// Replace only the app-specific render entry points; the platform shell and
// the other applications keep using their original renderers.
window.appView = appView;
window.appContent = appContent;
window.appDashboard = appDashboard;
window.componentSkeletonView = componentSkeletonView;
window.appLoadingView = appLoadingView;
if (typeof searchable !== 'undefined') {
  for (let i = searchable.length - 1; i >= 0; i--) {
    if (searchable[i][1].startsWith('/app2')) searchable.splice(i, 1);
  }
  searchable.unshift(
    ['Rede de Fornecedores', '/app2', applications.app2.description],
    ...applications.app2.sections.map(([id, label]) => [`${label} · Rede de Fornecedores`, `/app2/${id}`, `Supply Desk · ${label}`])
  );
}
render();
})();
