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
- ⬜ 61 · Nome real da pessoa vindo do diretório (sugestão do agente implantador; analisar antes de implementar)
  - ⬜ login Entra: nome pelo claim `name` (fallback `preferred_username`, e-mail), sem sufixos "(Empresa)"/", external"
  - ⬜ conciliação no login: busca por `sub`, senão adota a conta pelo e-mail (sem duplicar); nome atualizado, contas locais mantêm o seu
  - ⬜ opcional: job LDAP diário (lote 50, reconsulta anual, status por usuário), inativo sem LDAP

## Implantação real: relatório de testes no browser (2026-11-18)
- ✅ 62 · Runtime em host sem delegação de cgroup: métricas "indisponível" (não o agregado do usuário), aviso de overcommit/CPU não aplicada, socket real no rótulo, imagens locais sem docker.io/
- ⬜ 63 · Apps de demonstração: botões estáticos com aviso visível, lista recarrega após cadastrar, dados coerentes (totais e status por data), horário no fuso configurado
- ⬜ 64 · Consistência do hub: idioma padrão respeitado e traduções completas, disponibilidade "desde a instalação", Coletor na Saúde, instalação sem "restart pendente" nem variáveis de exemplo soltas, nome de exibição do app em todas as telas
- ⬜ 65 · Acessibilidade de menus ⋯ e modais; fetch a /apps/* com erro HTTP explícito (ou documentado)
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
