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
