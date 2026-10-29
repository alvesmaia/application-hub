# Postgres único por padrão

## Banco
- ✅ 46 · Um Postgres por padrão, dois como opção (compose: hub no servidor dos apps, reserva de conexões do hub, banco `hub` protegido em backups/restores)
- ✅ 47 · Limites por app no Postgres (statement_timeout, idle_in_transaction_session_timeout, temp_file_limit no provisionamento)
- ✅ 48 · Apps de exemplo com role própria, sem superusuário (senha por app, dono das tabelas também em volume existente, variáveis do cofre)

## Instalação
- ✅ 49 · --db-mode single|split e pergunta interativa (padrão single), resumo e consentimento, segredos novos completados num .prod.env existente

## Docs
- ✅ 50 · Guias, prod.env.example e CHANGELOG (inclui trocar de modo depois: dump e restore)

## Fechamento
- ▶️ 51 · hub-check --all, instalação isolada nos dois modos, limpeza
