/* App 3 — corporate service operations workspace. */
(function () {
  const fallbackAppView = window.appView;
  const fallbackAppContent = window.appContent;
  const fallbackAppDashboard = window.appDashboard;
  const fallbackComponentSkeletonView = window.componentSkeletonView;
  const fallbackAppLoadingView = window.appLoadingView;

  Object.assign(applications.app3, {
    name: 'Central de Serviços', short: 'Serviços',
    description: 'Atendimento interno, solicitações e aprovações em um só lugar.',
    color: '#e0eaf5', ink: '#326a98', icon: 'clipboard',
    sections: [
      ['dashboard', 'Visão geral', 'dashboard'],
      ['requests', 'Solicitações', 'clipboard'],
      ['approvals', 'Aprovações', 'check'],
      ['knowledge', 'Base de conhecimento', 'book'],
      ['settings', 'Configurações', 'settings']
    ],
    accent: 'Solicitações abertas', metric: '38'
  });

  const serviceRequests = [
    ['Acesso ao ambiente de produção', 'SD-2026-1842', 'Mariana Costa', 'Tecnologia', 'Alta', 'Em atendimento', '00:42'],
    ['Instalação do pacote de design', 'SD-2026-1839', 'Rafael Mendes', 'Software', 'Normal', 'Aguardando', '03:18'],
    ['Ajuste de centro de custo', 'SD-2026-1836', 'Camila Rocha', 'Financeiro', 'Normal', 'Em atendimento', '01:05'],
    ['Falha na VPN corporativa', 'SD-2026-1831', 'Lucas Ferreira', 'Infraestrutura', 'Urgente', 'Em atendimento', '00:16'],
    ['Solicitação de novo equipamento', 'SD-2026-1828', 'Ana Beatriz', 'Equipamentos', 'Baixa', 'Aguardando', '06:24'],
    ['Atualização de dados cadastrais', 'SD-2026-1822', 'Pedro Alves', 'Pessoas', 'Normal', 'Resolvido', '—']
  ];

  const approvals = [
    ['Notebook para novo colaborador', 'APR-2026-0418', 'Mariana Costa', 'R$ 6.450,00', 'Gestor da área', 'Hoje'],
    ['Licença de software anual', 'APR-2026-0415', 'Rafael Mendes', 'R$ 2.880,00', 'Finanças', 'Hoje'],
    ['Acesso privilegiado temporário', 'APR-2026-0411', 'Camila Rocha', 'Sem custo', 'Segurança', 'Amanhã'],
    ['Reembolso de deslocamento', 'APR-2026-0406', 'Lucas Ferreira', 'R$ 460,00', 'Gestor da área', 'Amanhã']
  ];

  function serviceSidebar(section) {
    return `<aside class="app-sidebar app3-sidebar">
      <div class="app3-brand"><span class="app3-brand-mark">${svg('clipboard')}</span><span><strong>SERVICE DESK</strong><small>Operações internas</small></span></div>
      <div class="app3-agent"><span class="app3-agent-avatar">JD</span><span><strong>John Doe</strong><small>Agente de atendimento</small></span><i></i></div>
      <div class="sidebar-caption">ATENDIMENTO</div>
      <nav class="sidebar-nav">${applications.app3.sections.map(([id, label, icon]) => `<button class="nav-item ${section === id ? 'active' : ''}" data-route="/app3/${id}">${svg(icon)}<span>${label}</span>${id === 'approvals' ? '<i class="app3-nav-count">4</i>' : ''}</button>`).join('')}</nav>
      <div class="app3-queue-card"><span>FILA DA EQUIPE</span><strong>12 <small>chamados</small></strong><div><i></i></div><small>4 próximos do vencimento do SLA</small></div>
      <div class="app3-sidebar-footer"><div class="app3-help-card"><span>?</span><strong>Precisa de suporte?</strong><small>Acesse os procedimentos do Service Desk.</small><button data-route="/app3/knowledge">Abrir central de ajuda →</button></div><button class="app3-platform-back" data-route="/applications">${svg('back')}<span>Voltar à plataforma</span></button></div>
    </aside>`;
  }

  function appView(key, section) {
    if (key !== 'app3') return fallbackAppView(key, section);
    const selected = sectionFor(applications.app3, section);
    return `<div class="workspace app3-workspace" id="workspace">${serviceSidebar(selected[0])}<div class="mobile-backdrop" data-mobile-close></div><section class="workspace-content app3-content"><button class="mobile-menu-button" data-mobile-menu>${svg('menu')} Menu do Service Desk</button>${appContent('app3', selected[0], applications.app3)}</section></div>`;
  }

  function appContent(key, section, app) {
    if (key !== 'app3') return fallbackAppContent(key, section, app);
    if (section === 'dashboard') return serviceDashboard();
    if (section === 'requests') return serviceRequestsPage();
    if (section === 'approvals') return serviceApprovalsPage();
    if (section === 'knowledge') return serviceKnowledgePage();
    return serviceSettingsPage();
  }

  function appDashboard(key, app) {
    return key === 'app3' ? serviceDashboard() : fallbackAppDashboard(key, app);
  }

  function componentSkeletonView(key, section) {
    if (key !== 'app3') return fallbackComponentSkeletonView(key, section);
    const selected = sectionFor(applications.app3, section);
    return `<div class="workspace app3-workspace" id="workspace">${serviceSidebar(selected[0])}<div class="mobile-backdrop" data-mobile-close></div><section class="workspace-content app3-content component-loading"><button class="mobile-menu-button" data-mobile-menu>${svg('menu')} Menu do Service Desk</button><div class="app3-skeleton-head"><i></i><b></b><span></span></div><div class="app3-skeleton-stats"><i></i><i></i><i></i><i></i></div><div class="app3-skeleton-main"><i></i><i></i></div><span class="component-loading-caption">Carregando Central de Serviços</span></section></div>`;
  }

  function appLoadingView(key, section) {
    if (key !== 'app3') return fallbackAppLoadingView(key, section);
    const app = applications.app3;
    return `<div class="workspace workspace-no-sidebar" id="workspace"><section class="workspace-content"><div class="app-loading" role="status" aria-label="Carregando aplicação" data-loader-name="${app.name}"><div class="loader-heading"><span class="loader-mark">${svg(app.icon)}</span><div><span class="loader-app-name">${app.name}</span><span class="loader-message">Preparando sua aplicação</span></div><strong class="loader-percentage">0%</strong></div><div class="loader-track"><span class="loader-fill"></span></div><div class="loader-footer"><span>Carregando workspace</span><span>Isso levará apenas alguns segundos</span></div></div></section></div>`;
  }

  function pageHeading(label, title, copy, action = '') {
    return `<header class="app3-page-heading"><div><span>${label}</span><h1>${title}</h1><p>${copy}</p></div>${action}</header>`;
  }

  function serviceDashboard() {
    const rows = serviceRequests.slice(0, 4);
    return `<div class="app3-page app3-dashboard">
      <div class="app3-welcome"><div><span class="app3-overline">OPERAÇÕES · QUARTA-FEIRA, 25 SET 2026</span><h1>Olá, John <i>—</i></h1><p>Veja o que precisa de atenção na operação de atendimento.</p></div><button class="app3-primary" data-toast="Formulário de nova solicitação aberto.">${svg('plus')} Nova solicitação</button><div class="app3-welcome-mark">SD<span>●</span></div></div>
      <div class="app3-kpi-row"><article><span>Chamados abertos</span><strong>38</strong><small><b>−12%</b> vs. semana passada</small></article><article><span>Na fila da equipe</span><strong>12</strong><small>4 com SLA próximo</small></article><article><span>Aguardando aprovação</span><strong>04</strong><small>2 há mais de 24 horas</small></article><article><span>Tempo médio de resolução</span><strong>3,6<em>h</em></strong><small><b>−18 min</b> neste mês</small></article></div>
      <div class="app3-dashboard-grid"><section class="app3-panel app3-volume-panel"><div class="app3-panel-head"><div><span class="app3-overline">DEMANDA DE ATENDIMENTO</span><h2>Chamados recebidos</h2></div><button data-toast="Período definido para os últimos 7 dias.">Últimos 7 dias⌄</button></div><div class="app3-volume-chart">${[['SEG',44,18],['TER',67,26],['QUA',51,20],['QUI',82,31],['SEX',59,24],['SÁB',34,12],['DOM',25,9]].map(([day,h,open], i) => `<div><span>${open}</span><i class="${i===3?'highlight':''}" style="height:${h}%"></i><small>${day}</small></div>`).join('')}</div><div class="app3-chart-legend"><span><i></i>Novos chamados</span><b>312 <small>nesta semana</small></b></div></section>
        <section class="app3-panel app3-sla-panel"><div class="app3-panel-head"><div><span class="app3-overline">QUALIDADE DO SERVIÇO</span><h2>Indicadores de SLA</h2></div><button data-toast="Detalhamento de SLA aberto.">Detalhes →</button></div><div class="app3-sla-score"><div><strong>94<small>%</small></strong><span>Dentro do prazo</span></div><div class="app3-sla-ring"><i></i></div></div><div class="app3-sla-line"><span>Primeira resposta</span><b>96%</b><i><em style="width:96%"></em></i></div><div class="app3-sla-line"><span>Resolução</span><b>91%</b><i><em style="width:91%"></em></i></div><div class="app3-sla-line"><span>Aprovações</span><b>88%</b><i><em style="width:88%"></em></i></div></section></div>
      <section class="app3-panel app3-recent-panel"><div class="app3-panel-head"><div><span class="app3-overline">ACOMPANHAMENTO AO VIVO</span><h2>Solicitações recentes</h2></div><button class="app3-link" data-route="/app3/requests">Ver todas as solicitações <b>→</b></button></div>${requestTable(rows, false)}</section>
    </div>`;
  }

  function requestTable(rows, includePriority = true) {
    return `<div class="app3-table-scroll"><table class="app3-table"><thead><tr><th>SOLICITAÇÃO</th><th>PROTOCOLO</th><th>SOLICITANTE</th><th>CATEGORIA</th>${includePriority?'<th>PRIORIDADE</th>':''}<th>STATUS</th><th>SLA</th><th></th></tr></thead><tbody>${rows.map((r) => `<tr><td><strong>${r[0]}</strong></td><td><code>${r[1]}</code></td><td>${r[2]}</td><td>${r[3]}</td>${includePriority?`<td><span class="app3-priority ${r[4].toLowerCase()}">${r[4]}</span></td>`:''}<td><span class="app3-request-status ${r[5]==='Resolvido'?'resolved':r[5]==='Aguardando'?'waiting':''}">${r[5]}</span></td><td class="app3-sla-time ${r[6]==='00:16'?'urgent':''}">${r[6]}</td><td><button class="app3-row-menu" aria-label="Ver solicitação" data-toast="Chamado ${r[1]} aberto.">···</button></td></tr>`).join('')}</tbody></table></div>`;
  }

  function serviceRequestsPage() {
    const action = `<button class="app3-primary" data-toast="Formulário de nova solicitação aberto.">${svg('plus')} Nova solicitação</button>`;
    return `<div class="app3-page app3-requests-page">${pageHeading('ATENDIMENTO INTERNO','Solicitações','Acompanhe os chamados, prioridades e prazos da central.', action)}
      <div class="app3-request-stats"><div><strong>38</strong><span>Abertos</span></div><div><strong>12</strong><span>Em atendimento</span></div><div><strong>19</strong><span>Aguardando</span></div><div><strong>07</strong><span>Resolvidos hoje</span></div></div>
      <div class="app3-request-toolbar"><div class="app3-request-tabs"><button class="selected" data-toast="Exibindo todas as solicitações.">Todas <i>38</i></button><button data-toast="Exibindo os chamados atribuídos a você.">Atribuídas a mim <i>6</i></button><button data-toast="Exibindo chamados sem responsável.">Não atribuídas <i>4</i></button></div><div class="app3-request-filters"><label>${svg('search')}<input data-table-search placeholder="Buscar por protocolo ou assunto..." aria-label="Buscar solicitações"></label><button data-toast="Filtros avançados abertos.">${svg('filter')} Filtrar</button></div></div>
      <section class="app3-panel app3-request-list">${requestTable(serviceRequests, true)}<footer>Mostrando 6 de 38 solicitações <span>‹　<b>1</b>　2　3　…　7　›</span></footer></section></div>`;
  }

  function serviceApprovalsPage() {
    return `<div class="app3-page app3-approvals-page">${pageHeading('FLUXOS DE DECISÃO','Aprovações','Revise solicitações que aguardam sua decisão.', `<button class="app3-outline" data-toast="Relatório de aprovações exportado.">${svg('file')} Exportar</button>`)}
      <div class="app3-approval-banner"><span>${svg('clock')}</span><div><strong>4 itens aguardam aprovação</strong><small>2 solicitações estão pendentes há mais de 24 horas.</small></div><button data-toast="Lista ordenada por antiguidade.">Mais antigas primeiro⌄</button></div>
      <div class="app3-approval-layout"><div class="app3-approval-list">${approvals.map(([title, code, requester, amount, approver, date], i) => `<article class="app3-approval-card"><div class="app3-approval-card-top"><span class="app3-approval-type">${i===2?'ACESSO E SEGURANÇA':'COMPRA E DESPESA'}</span><span class="app3-pending-label"><i></i> Pendente</span></div><h2>${title}</h2><div class="app3-approval-meta"><span><small>PROTOCOLO</small><b>${code}</b></span><span><small>SOLICITANTE</small><b>${requester}</b></span><span><small>VALOR</small><b>${amount}</b></span></div><div class="app3-approval-card-foot"><span>Próximo aprovador: <b>${approver}</b> · ${date}</span><button data-toast="Detalhes da aprovação ${code} abertos.">Analisar solicitação <strong>→</strong></button></div></article>`).join('')}</div>
        <aside class="app3-approval-aside"><span class="app3-overline">SEU RESUMO</span><h2>Este mês</h2><div class="app3-approval-total"><strong>27</strong><span>solicitações<br>analisadas</span></div><div class="app3-aside-stat"><span>Aprovadas</span><b>23 <i>85%</i></b></div><div class="app3-aside-stat"><span>Rejeitadas</span><b>04 <i>15%</i></b></div><div class="app3-aside-note">Seu prazo médio de análise está em <strong>4,2 horas</strong>.</div></aside></div></div>`;
  }

  function serviceKnowledgePage() {
    const articles = [['Acessos e permissões','TECNOLOGIA','Como solicitar acesso a sistemas e ambientes corporativos.','6 artigos'],['Equipamentos e trabalho remoto','HARDWARE','Solicitação, troca e configuração de equipamentos.','8 artigos'],['Despesas e reembolsos','FINANCEIRO','Prazos, comprovantes e acompanhamento de reembolsos.','4 artigos'],['Pessoas e benefícios','PESSOAS','Dúvidas sobre cadastro, benefícios e movimentações.','9 artigos'],['Aplicações corporativas','SOFTWARE','Guias de uso e resolução de problemas comuns.','12 artigos'],['Segurança da informação','SEGURANÇA','Boas práticas e canais para reportar incidentes.','5 artigos']];
    return `<div class="app3-page app3-knowledge-page">${pageHeading('AUTOATENDIMENTO','Base de conhecimento','Orientações rápidas para resolver dúvidas e encontrar serviços.', `<button class="app3-outline" data-toast="Todos os serviços disponíveis abertos.">Ver catálogo de serviços <span>↗</span></button>`)}
      <div class="app3-knowledge-hero"><div><span class="app3-overline">COMO PODEMOS AJUDAR?</span><h2>Encontre sua resposta.<br><em>Sem esperar na fila.</em></h2><p>Pesquise artigos, instruções e serviços da Application Hub.</p><label>${svg('search')}<input data-kb-search placeholder="Ex.: acesso ao sistema, VPN, reembolso..." aria-label="Buscar na base de conhecimento"></label></div><div class="app3-knowledge-shape"><span>?</span><i></i><b></b></div></div>
      <div class="app3-knowledge-title"><div><span class="app3-overline">CENTRAL DE AJUDA</span><h2>Explore por assunto</h2></div><button class="app3-link" data-toast="Todos os assuntos exibidos.">Ver todos →</button></div><div class="app3-category-grid">${articles.map(([title, category, desc, count], i) => `<button class="app3-category-card" data-kb-item data-kb-searchable="${`${title} ${category} ${desc}`.toLowerCase()}" data-toast="Conteúdos de ${title} abertos."><span class="app3-category-icon cat-${i}">${svg(['users','box','file','people','apps','adminShield'][i])}</span><span class="app3-category-copy"><small>${category}</small><strong>${title}</strong><em>${desc}</em><b>${count} <i>→</i></b></span></button>`).join('')}</div><p class="app3-kb-empty" hidden>Nenhum assunto encontrado. Experimente outra busca.</p></div>`;
  }

  function serviceSettingsPage() {
    return `<div class="app3-page app3-settings-page">${pageHeading('PREFERÊNCIAS DO ATENDIMENTO','Configurações','Gerencie as preferências pessoais da sua central de serviços.')}
      <div class="app3-settings-layout"><nav class="app3-settings-nav"><button class="selected">Meu atendimento <span>→</span></button><button data-toast="Preferências de notificações selecionadas.">Notificações</button><button data-toast="Canais de atendimento selecionados.">Canais de atendimento</button></nav><section class="app3-settings-panel"><div class="app3-settings-panel-head"><div><span class="app3-overline">PERFIL DE ATENDIMENTO</span><h2>Como prefere trabalhar?</h2><p>Essas definições ajustam sua fila e as atualizações que recebe.</p></div><span>${svg('settings')}</span></div><div class="app3-settings-fields"><label>Grupo de atendimento<select><option>Service Desk — Nível 1</option><option>Infraestrutura — Nível 2</option><option>Aplicações corporativas</option></select></label><label>Fuso horário<select><option>Brasília (GMT−3)</option><option>UTC</option></select></label><label class="app3-setting-check"><input type="checkbox" checked><span><b>Atribuir chamados automaticamente</b><small>Distribuir novos chamados de acordo com a disponibilidade da equipe.</small></span></label><label class="app3-setting-check"><input type="checkbox" checked><span><b>Alertar antes do vencimento do SLA</b><small>Receber um lembrete 15 minutos antes do prazo.</small></span></label></div><footer><button class="app3-outline" data-toast="Alterações descartadas.">Cancelar</button><button class="app3-primary" data-toast="Preferências salvas com sucesso.">Salvar preferências</button></footer></section></div></div>`;
  }

  function filterKnowledge() {
    const page = document.querySelector('.app3-knowledge-page');
    if (!page) return;
    const query = (page.querySelector('[data-kb-search]')?.value || '').trim().toLowerCase();
    let visible = 0;
    page.querySelectorAll('[data-kb-item]').forEach((item) => {
      const show = !query || item.dataset.kbSearchable.includes(query);
      item.hidden = !show;
      if (show) visible++;
    });
    const empty = page.querySelector('.app3-kb-empty');
    if (empty) empty.hidden = visible > 0;
  }

  window.appView = appView;
  window.appContent = appContent;
  window.appDashboard = appDashboard;
  window.componentSkeletonView = componentSkeletonView;
  window.appLoadingView = appLoadingView;

  if (typeof searchable !== 'undefined') {
    for (let i = searchable.length - 1; i >= 0; i--) {
      if (searchable[i][1].startsWith('/app3')) searchable.splice(i, 1);
    }
    searchable.unshift(['Central de Serviços', '/app3', applications.app3.description], ...applications.app3.sections.map(([id, label]) => [`${label} · Central de Serviços`, `/app3/${id}`, `Central de Serviços · ${label}`]));
  }

  document.addEventListener('input', (event) => {
    if (event.target.matches('[data-kb-search]')) filterKnowledge();
  });
  render();
})();
