# Postgres único por padrão

## Banco
- ✅ 46 · Um Postgres por padrão, dois como opção (compose: hub no servidor dos apps, reserva de conexões do hub, banco `hub` protegido em backups/restores)
- ✅ 47 · Limites por app no Postgres (statement_timeout, idle_in_transaction_session_timeout, temp_file_limit no provisionamento)
- ✅ 48 · Apps de exemplo com role própria, sem superusuário (senha por app, dono das tabelas também em volume existente, variáveis do cofre)

## Instalação
- ✅ 49 · --db-mode single|split e pergunta interativa (padrão single), resumo e consentimento, segredos novos completados num .prod.env existente (split removido na 53)

## Docs
- ✅ 50 · Guias, prod.env.example e CHANGELOG (inclui trocar de modo depois: dump e restore)

## Fechamento
- ✅ 51 · hub-check --all, instalação isolada nos dois modos, limpeza

## MVP: só um Postgres
- ✅ 52 · scripts/db-merge.sh: move o banco `hub` do servidor antigo para o único (dump/restore, conferência, contêiner antigo parado e renomeado para rollback)
- ✅ 53 · Compose só com um servidor; sem --db-mode, pergunta e variáveis do split; trava (install, hub-start, dev.sh, hub_update) quando há `<projeto>-db` antigo
- ✅ 54 · Docs, prod.env.example e CHANGELOG (atualização de quem tinha dois servidores)
- ✅ 55 · hub-check --all, fusão testada em instância isolada no formato antigo, limpeza

## Laboratório: sessão do usuário sem bus
- ✅ 56 · Reproduzir em laboratório RHEL o servidor sem bus do usuário e validar a correção da TI (restart de user@)
  - ✅ imagem Rocky 9 + usuário rootless com linger, "app vizinho" net=host iniciado fora da sessão
  - ✅ antes: bus removido, rede nomeada falha como no servidor, install.sh para com a orientação
  - ✅ "TI": systemctl restart user@<uid>.service; bus volta; vizinho sobrevive?
  - ✅ depois: install.sh padrão, /readyz, publicação de app novo sem comando no servidor, reboot
  - ✅ limpeza e registro em docs/task-details.md
- ✅ 57 · Laboratório: alternativas se a TI negar caminhos
  - ✅ S2: bus restaurado, units do usuário proibidas (só cron): --no-systemd --autostart cron, app novo, reboot (corrigido: hub-start.sh sem ~/.local/bin no PATH do cron)
  - ✅ S3: restart negado (sem bus, só cron): corrigido (systemd-run próprio do socket do hub, CONTAINER_HOST)
  - ✅ registro em docs, limpeza
- ✅ 58 · Laboratório: user@.service desativado pela TI (ExecStart=/bin/true, sem systemd --user)
  - ✅ servidor simulado com o mesmo user@.service do servidor real, vizinho pelo cron
  - ✅ install.sh padrão para com a orientação; --no-systemd --autostart cron verde (corrigido: install detecta o user@ desativado e segue sem systemd)
  - ✅ app novo publicado, rede nomeada, reboot
  - ✅ registro em docs, limpeza

## Implantação real: melhorias apontadas pelo agente implantador
- ✅ 59 · Correções da instalação em rede corporativa
  - ✅ bucket de exemplo: contêiner do mc sem o proxy herdado (post.sh, iso-instance.sh)
  - ✅ doctor: build de prova com cgroupfs no modo sem systemd
  - ✅ --ca-file grande (bundle do sistema) recusado com mensagem clara
  - ✅ reexecução: aviso quando opção diverge do .prod.env
  - ✅ doctor: espelho interno falhando pelo proxy sugere --no-proxy <host>
- ✅ 60 · Flags não interativas: login Microsoft (Entra) e proxy reverso na frente
  - ✅ --entra-tenant/--entra-client-id/--entra-client-secret-file/--entra-domains/--entra-role/--local-login
  - ✅ --trusted-proxy <CIDR>; doc do Nginx externo com TLS
  - ✅ docs, CHANGELOG, hub-check

## Implantação real: relatório de testes no browser (2026-11-18)
- ✅ 62 · Runtime em host sem delegação de cgroup: métricas "indisponível" (não o agregado do usuário), aviso de overcommit/CPU não aplicada, socket real no rótulo, imagens locais sem docker.io/
- ✅ 63 · Apps de demonstração: botões estáticos com aviso visível, lista recarrega após cadastrar, dados coerentes (totais e status por data), horário no fuso configurado
  - ✅ correções nos apps e no SDK (`hub_sdk.locale.zone()`) · subagente: Corrigir apps demo (tarefa 63)
  - ✅ cartão de ajuda, perfil de conformance do inventory (303) e E2E ajustados
- ✅ 64 · Consistência do hub: idioma padrão respeitado e traduções completas, disponibilidade "desde a instalação", Coletor na Saúde, instalação sem "restart pendente" nem variáveis de exemplo soltas, nome de exibição do app em todas as telas
- ✅ 66 · Deploy no Podman rootless: alias DNS do nome final no candidato, staging sem os aliases de produção, implantação só conclui se o hub alcança o app pelo nome (senão falha com a causa)
- ✅ 67 · Botão "i" (stack) dos cards de Aplicações só para admin
- ✅ 68 · App novo só em staging marcado "Unstable": sonda de produção só depois da primeira implantação em produção

## Refinamentos pedidos pelo dono (2026-11-18)
- ✅ 69 · Usuário SSO novo entra sem papel de admin (admin só por atribuição explícita)
- ✅ 70 · App oculto no catálogo: visível só para quem tem acesso ao app (config por app)
- ✅ 71 · Home sem a opção Inbox
- ✅ 72 · Home sem o contêiner de atalhos (shortcuts)
- ✅ 73 · Status: mensagem geral coerente com app instável
- ✅ 74 · Status: histórico de disponibilidade de um ano
- ✅ 75 · URL própria de staging: /staging/apps/<id>/… (x-hub-base com o prefixo; produção sem mudança)
- ✅ 76 · Selo "STAGING" no header global quando o ambiente for staging
- ✅ 77 · admin/runtime: cores distintas Plataforma/Aplicações, sem notas do engine e sem botões Docker/Podman
- ✅ 78 · Revisão de idioma: telas em inglês sem trechos em português (Copy prompt, JS e Python)
  - ✅ varredura das telas em inglês · subagente: Varredura de idioma misto
  - ✅ strings fixas em JS/Python: 51 pares novos no catálogo, 403 e paginador traduzidos no servidor
- ✅ 79 · Menu "⋯" das tabelas não fica cortado na última linha (abre para cima dentro do painel)
- ✅ 80 · Descrição da página num "i" que abre painel lateral (no lugar do subtítulo e das notas fixas)
- ✅ 81 · Versão do hub no header (só para quem está logado)
- ✅ 82 · Teste de adequação: app existente (FastAPI + Jinja) adequado ao hub sem perder layout, design e funções
  - ✅ app "legado" independente, com várias páginas, sidebar, formulários e JS próprio · subagente: Constrói app legado de frota
  - ✅ capturas de referência e roteiro funcional do app original (36/36)
  - ✅ outro subagente, sem contexto, adequa o app só com o prompt do Copy prompt · subagente: Adequa app Frota ao hub (rodada 1): 7/12 funções
  - ✅ avaliação conjunta: antes × depois (design, UX, funções)
  - ✅ melhorias: links profundos, PRG atualiza a URL, autosubmit, ícones e SVG próprio, help_card opcional, eixo do gráfico, roteiro de adequação, prompt, hub check (links e tema escuro), hub-check ressincroniza a CLI
  - ✅ rodada 2 com agente novo · subagente: Adequa app Frota ao hub (rodada 2): 33/36; achados: redirect de fragmento, confirmação com rótulo próprio, modal com formulário, nome na sidebar, menu do celular sem nome acessível
  - ✅ rodada 3 com agente novo · subagente: Adequa app Frota ao hub (rodada 3): 36/36, design e tema escuro coerentes
- ✅ 83 · Botão de SSO na tela de login com o rótulo "Microsoft SSO"

## Sessão, SSO e funções (2026-11-23)

- ✅ 84 · Sessão: 24 h sem uso, renovada enquanto o usuário navega (no máximo a cada 60 min), limite de 3 dias desde o login
- ✅ 85 · Contas do Entra ID sem "Redefinir senha" nem "Alterar senha"
- ✅ 86 · Funções padrão em 3 níveis: Administrador, Power User e User (antigo Colaborador)
- ✅ 87 · Inbox: seta do dropdown aparece como 2 triângulos; trocar por uma seta só
- ✅ 88 · /admin/users: faixa clara sob a coluna Ações no hover da linha (célula flex mais baixa que a linha)
- ✅ 89 · App de exemplo "Automações" (orquestrador): pacotes versionados (UI, API/CLI e MCP), processos com versão escolhida, gatilhos agendados e execuções; admin configura, usuário aciona e vê resultados; 3 pacotes de exemplo (CEP, feriados, CPF/CNPJ)
  - ✅ desenho a partir do módulo RPA de referência (modelo, parâmetros, agendamento, telas) · subagente: Extrair conceitos do módulo RPA
  - ✅ app em platform/demo-apps-py/automacoes (orquestrador: pacotes, processos, gatilhos, execuções em subprocesso sem segredos) · subagente: Construir app de exemplo Automações
  - ✅ integração na plataforma (compose, banco, registro, segredos, conformance, imagens, instalação)
  - ✅ correções vistas no app rodando: amostras fora do wheel, formulário do modal (405), polling após PRG do kit, checkbox esticado em .form-field
  - ✅ testes (app 75 + hub), conformance 25/25 (perfil com "roles"), E2E 35/35 e ui-sweep (2 achados de select vazio, corrigidos)
- ✅ 90 · Login depois de sair: "?next=/logout" levava a uma página "Method Not Allowed"
- ✅ 91 · Apps de exemplo com menos recursos por padrão (menos de 1 CPU)
- ✅ 92 · admin/runtime: remover a nota "App limits above the host capacity..."
- ✅ 93 · admin/runtime: "Capacidade do host" à esquerda e "Runtime" à direita
- ✅ 94 · Validar implantação: instalação nova e atualização de versão existente, medindo a indisponibilidade do hub e dos apps
- ✅ 95 · Riscos remanescentes da 0.3.0: atualização em servidor RHEL com Podman sem root, a partir da 0.2.0 com dois servidores de banco (db-merge + hub_update)
  - ✅ achado no laboratório Podman: hub_update.py, install.sh e db-merge.sh usavam `docker inspect` sem `--type container`; no Podman o nome `app-hub-db` casava com o volume `app-hub-db-data` e a atualização sem parada era recusada
- ✅ 96 · Rótulos de status no padrão das páginas de status (Statuspage): Operacional, Degradado (antes "Instável") e Indisponível / Outage
- ✅ 97 · admin/runtime: "Capacidade do host" mostra o uso real medido (/proc do host + uso de cada contêiner), atualiza a cada 5 s; "Aplicações" em magenta; hover com valor / total; runtime no "i" ao lado do título
- ✅ 98 · admin/runtime: "Atualizado às HH:MM:SS" no lugar de "Uso atual · atualiza sozinho"
- ✅ 99 · Ícones do menu: Status com o medidor (antes do grupo Operação); Operação com uma chave inglesa

## Apps de exemplo em inglês (0.4.0)

- ✅ 100 · ids e rotas dos apps de exemplo em inglês (inventory, suppliers, servicedesk, collector, automations), com migração automática das instalações existentes; telas seguem o idioma do usuário
  - ✅ inventário de tudo que guarda o id do app (hub, banco, segredos, contêineres, bucket, histórico) · subagente: Inventário de ids dos apps
  - ✅ desenho da migração (ordem, reversão, o que fica como histórico) — docs/task-details.md
  - ✅ renomeação dentro dos apps (pastas, pacotes, rotas, testes, conformance, E2E) · subagente: Renomear apps de exemplo para inglês (ativos virou inventory: `assets` colidia com /assets/ do hub)
  - ✅ renomeação na plataforma (hub, compose, registro, segredos, scripts, CI, docs, testes do hub)
  - ✅ migração automática (bancos, papéis, segredos, contêineres, bucket, dados do hub) — scripts/rename-sample-apps.sh + python -m hub.admin rename-apps; achados no teste real: valores do cofre presos ao nome (recifrar), redes antigas com a mesma faixa, valores da .prod.env, rotas no histórico
  - ✅ validação: atualização real 0.3.2 → 0.4.0 (dados preservados, ~1 min 40 s fora), conformance 5/5, E2E 52/52, ui-sweep 0 achados; hub-check --all

## Melhorias pós-0.4.0 (2026-11-24)

- ✅ 101 · admin/runtime: aviso "Runtime indisponível" com a causa provável em palavras simples (socket ausente, sem permissão, conexão recusada, tempo esgotado), não a exceção crua
- ✅ 102 · hub_update.py: depois da troca do hub, aplica o compose ao resto da pilha (apps, borda, bancos) sem recriar o hub; `--hub-only` mantém o comportamento antigo; builds sem atestação de proveniência (ids reproduzíveis: só o que mudou é recriado)
- ✅ 103 · Pacotes de exemplo (platform/samples/*.hubapp) regerados com os ids em inglês e 0,5 vCPU
- ✅ 104 · admin/runtime: x / y em CPU e memória (medição pelo /proc do contêiner quando o engine não mede), hover só com números, barras com escala e gráfico de área dos últimos 5 min em painéis separados
- ✅ 105 · MCP (OAuth) atrás de proxy corporativo: documento do cliente (Copilot desktop) falhava com "certificate verify failed" (IP fixado na URL do túnel); pelo proxy conecta pelo nome
- ✅ 106 · admin/deploys: sem a nota do manifest.json na importação; log de cada implantação guardado no registro (etapas, build, resultado; ações seguintes acrescentam) e exibido no painel lateral da versão no Histórico; aviso curto no lugar da mensagem longa
- ✅ 107 · Sem banners: admin/tasks não mostra mais "Sem resposta de" (motivo fica em /api/ops/tasks); runtime indisponível e avisos de Conexões viram linha discreta
- ✅ 108 · Cabeçalho do hub sem a caixa de pesquisa (confundida com a do app; Ctrl+K continua); guia dos agentes diz para onde vai cada item do cabeçalho próprio do app (pesquisa, notificações, menu do usuário, logo)
- ✅ 109 · Idioma padrão "Automático (idioma do navegador)": salvar outras configurações não fixa mais pt-BR

## Port de app existente: lacunas achadas com o Frota+ (2026-11-24)

- ✅ 110 · Hub: recolher a barra lateral do app no desktop; bloco da marca usa `AppInfo.color`
- ✅ 111 · SDK: ícone SVG sem `Markup` no código do app (sem S704); `sdk.search`/`sdk.outbox` no sdk.md
- ✅ 112 · CLI: `hub --version` com a versão do SDK; versão da CLI acompanha a do hub
- ✅ 113 · Guia e contrato: `@HUB@` sem hub, cores em tokens, outbox na mesma transação, destinatários de notificação e papéis no `hub dev`, caminhos do §29, POST → 303, nomes reservados, animações/SVG, flash
- ✅ 114 · Exemplo de referência: port do Frota+ (as is → to be) no guia

## Falhas E2E pré-existentes + 0.4.2 (2026-11-24)

- ✅ 115 · E2E (c)+(b) JS próprio: candidata do servicedesk presa em "migrações do banco em curso" — instância isolada não criava os papéis de banco dos apps (o hub implanta com o papel do app-env.json)
- ✅ 116 · E2E 07-staging: CSP da página de staging só liberava /_frag/<app>; agora /staging/_frag/<app>, ws e formulários de staging, scripts aprovados de staging
- ✅ 117 · E2E 06 backups: menu "⋯" de tabela com uma linha ficava cortado pelo painel (Restaurar escondido); sem espaço nos dois sentidos, abre como camada fixa. 0 B = suppliers sem banco e bucket vazio (esperado)
- ✅ 118 · E2E 15 storage: passa com `iso-instance.sh run` (variáveis da instância); sem defeito
- ✅ 119 · Release 0.4.2 (também: `hub` CLI sem o pacote da plataforma voltou a funcionar; rótulo "Connection string" com dica)

## Ajustes de UI + login único do CLI por OAuth (2026-11-25)

- ✅ 120 · admin/runtime: barras de CPU/memória deslizam até o valor novo (sem salto)
- ✅ 121 · Sem banners nas telas do hub (status, contêiner degradado, manutenção, aviso de versão, erro de login)
- ✅ 122 · admin/users: própria conta SSO mostra o ⋯ desabilitado com o motivo (menu vazio)
- ✅ 123 · Entra: papel padrão nunca é admin; conta do instalador assume o nome real da pessoa
- ✅ 124 · OAuth device flow (RFC 8628) + escopo `deploy`; sessão do CLI 30 dias sem uso / 90 dias total, refresh rotativo com detecção de reuso
- ✅ 125 · CLI: `hub login` pelo navegador (código de dispositivo), renovação automática, `hub mcp` e `hub deploy` usam o mesmo login; saída "não autenticado" orienta o agente
- ✅ 126 · Tokens só por usuário: remover admin/tokens e /api/admin/tokens; tokens de serviço migrados para quem os criou; "Revogar todos os acessos" em admin/users; usuário desativado perde todos os acessos
- ✅ 127 · Docs, instruções do MCP, CHANGELOG; E2E + ui-sweep; hub-check --all
- ✅ 128 · CLI atrás de proxy com inspeção TLS: confia no repositório do sistema e em SSL_CERT_FILE; certificado recusado explica o motivo (0.5.1)

## Acesso de leitura ao banco por host:porta (dashboards) (2026-11-25)

- ✅ 129 · Postgres na rede (opt-in): TLS obrigatório, faixas de IP só para contas de leitura, contas de serviço só pela rede interna; pg_hba e certificado geridos pelo hub
- ✅ 130 · Contas de leitura: admin cria com responsável, bancos escolhidos (produção/staging, nunca `hub`), validade no Postgres, só leitura e tempo máximo por consulta; nova senha, remover; param com a pessoa desativada ou com "Revogar todos os acessos"
- ✅ 131 · Tela "Acesso ao banco" (aba de Usuários) + API de administração
- ✅ 132 · Instalador interativo e flags (também numa instalação existente) + compose + docs
- ✅ 133 · Validação: conexão real com TLS de fora do contêiner, E2E, ui-sweep, hub-check --all

## Publicações mais rápidas (2026-11-25)

- ✅ 134 · Testes do hub em paralelo (pytest-xdist, até 4 processos)
- ✅ 135 · Check completo não roda de novo em código já verificado (assinatura do código; pre-push pula)

## Leitura do banco do hub (2026-11-26)

- ✅ 136 · Conta de leitura pode incluir o banco interno do hub (opção separada, explícita)
- ✅ 137 · Textos sem nome de ferramenta (clientes SQL, não DBeaver)
- ✅ 139 · Credenciais mostram os bancos liberados (nome para a conexão do cliente SQL)
- ✅ 138 · Validação: testes, ui-sweep, hub-check; mostrar o fluxo no browser

## Sem busca; tutorial (2026-11-26)

- ✅ 140 · Tutorial: tarefa "notificações" leva a /account#prefs (hoje cai na Inbox, sem formulário) e sai a tarefa "busca"
- ✅ 141 · Hub sem busca: /search, Ctrl+K, botão do cabeçalho, API/MCP search, estatísticas de busca, textos
- ✅ 142 · SDK e apps de exemplo sem `_hub/search`; contrato e docs
- ✅ 143 · Validação: hub-check --all, conformance, E2E, ui-sweep

## Textos enxutos (2026-11-26)

- ✅ 144 · "Acesso ao banco": sem a linha de status ligada, sem textos nos modais e sem a dica do banco do hub

## Bancos dos apps de exemplo com prefixo app_ (2026-11-26)

- ✅ 145 · Instalação nova: banco e papel `app_<id>` (job de papéis, init do banco, compose, variáveis semeadas, iso-instance)
- ✅ 146 · Instalação existente: renomeia `<id>` → `app_<id>` (banco, papel, grupo de leitura e contas de leitura) e reescreve as conexões guardadas
- ✅ 147 · Atualizador roda o job de papéis antes de recriar os apps
- ✅ 148 · Validação: migração real num Postgres com dados, hub-check --all, E2E, docs
- ✅ 149 · /admin/env: botão de filtro "Aplicação" e badges cortados na coluna Aplicação
- ✅ 150 · Catálogo (/applications) sem o estado "Operacional/Degradado" nos cards

## Variáveis e tarefas (2026-11-26)

- ✅ 151 · /admin/env: nome + cadeado ("Mantida pelo hub"); nome no app em cinza na linha de baixo; sem seta e sem "gerida"
- ✅ 152 · /admin/tasks (Tarefas): clicar numa tarefa abre o painel lateral com detalhes e log; coluna Tarefa com número sequencial no lugar do id
- ✅ 153 · Todas as tabelas paginadas com 10 itens por padrão; paginador com Anterior/Próxima no centro e "Itens por página" à direita

## UX: Governança, Backups e Observabilidade (2026-11-26)

- ✅ 156 · Governança (/admin): abas Geral · Aplicações · Segurança e chaves · Uso (proposta aprovada)
- ✅ 157 · Backups (/admin/backups): seções em abas, sem descrições desnecessárias
- ✅ 158 · Saúde › Observabilidade: filtros mais limpos

## Cabeçalho dentro do app e rodapé do menu (2026-11-26)

- ✅ 159 · Busca do app no cabeçalho (campo `search` do manifesto; o hub só abre a página do app)
- ✅ 160 · Sino dentro do app mostra só as notificações daquele app (+ "Ver todas")
- ✅ 161 · Ícone do GitHub e versão no rodapé do menu lateral
- ✅ 162 · Apps de exemplo, contrato e SDK; validação
- ✅ 163 · Relatório de integração: `--app-ink` deixa de ser sobrescrito pela cor do catálogo; opt-out do chevron do select; contrato explica papéis dos tokens e botões
  - ✅ relatório: `--app-ink`, opt-out do chevron, contrato
  - ✅ testes do Entra: `HUB_PUBLIC_URL` exportado vazava para o pytest
  - ✅ compatibilidade Tailwind/Bootstrap/HTML: rem 16px, select com especificidade zero, E2E 39
- ✅ 164 · Prova com app real de teste: subir original → validação do dono → adequação via "Copiar prompt" → comparação
  - ✅ réplica sintética descartada (substituída pelo app real de teste)
  - ✅ subir o app original e pausar para validação (validado pelo dono)
  - ✅ adequação ao hub pelo prompt da documentação (publicado no hub, porte parcial)
  - ✅ comparação lado a lado: porte reescreveu o CSS (363 KB → 12 KB), perdeu fontes/ícones e o layout
- ✅ 165 · Fidelidade visual na adequação de apps
  - ✅ 1 · fontes do próprio app (`@font-face` com URL do app) mantidas no CSS confinado
  - ✅ 2 · modo "marcação original": templates e CSS do app reaproveitados dentro do `workspace` (SDK + AGENTS.md)
  - ✅ 3 · `hub compare` (impressão de estilo: fontes, paleta, raios, contagens; porte ruim = 54–72%, limite 25%)
  - ✅ 4 · refazer o case de exemplo (subagente): visual fiel; só Tickets portado; compare 19,7–24,5%
  - ✅ 5 · ponte de tokens com especificidade zero (variáveis do app com nomes comuns)
