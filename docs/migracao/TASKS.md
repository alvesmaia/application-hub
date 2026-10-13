# Application Hub — migração para Python

`✅ done` · `▶️ running` · `⬜ todo` · `⛔ blocked` · `❓ needs your call`

**4 of 38 tasks · 47 testes · 7 commits**

## F0 — Fundação

- ✅ 1 · Projeto Python (uv, ruff, mypy, pytest) e casca com o visual atual
- ✅ 2 · hub-dev.example.com (DNS, túnel, Access, porta 8090)

## F1 — Núcleo

- ✅ 3 · Gateway: apps, fragmentos, API, WebSocket, redirecionamentos, CSP
- ✅ 4 · Cofre e estado compartilhado (compatíveis com o Rust)
- ✅ 5 · Login, sessões, permissões, tokens, usuários demo
- ▶️ 6 · Auditoria em cadeia, store da Administração, logs e outbox — *Codex*
- ⬜ 7 · Reiniciar pilha nova e conferir login no endereço público

## F2 — Páginas do usuário

- ⬜ 8 · Início e onboarding
- ⬜ 9 · Aplicações (catálogo, stack, favoritos)
- ⬜ 10 · Status e SLOs
- ⬜ 11 · Busca ⌘K e busca federada
- ⬜ 12 · Inbox, aprovações e notificações (SSE)
- ⬜ 13 · Configurações, tokens pessoais, apps autorizados
- ⬜ 14 · Telas de credencial (login, troca de senha, consentimento, aprovação)
- ⬜ 15 · i18n pt-BR/en/es do shell e páginas

## F3 — Administração e Operações

- ⬜ 16 · Usuários e funções
- ⬜ 17 · Variáveis de ambiente e conexões (SharePoint/S3, provisionar armazenamento)
- ⬜ 18 · Implantações e pipeline (scripts aprovados, SRI)
- ⬜ 19 · Containers (runtime Docker)
- ⬜ 20 · Tarefas (filtros por coluna)
- ⬜ 21 · Backups e restauração
- ⬜ 22 · Logs ao vivo e auditoria
- ⬜ 23 · Flags, analytics, observabilidade, saúde

## F4 — API e MCP

- ⬜ 24 · API /api/v1 e OpenAPI equivalente
- ⬜ 25 · MCP (Streamable HTTP, duas eras) e ferramentas dos apps
- ⬜ 26 · OAuth 2.1 (DCR, PKCE, refresh, CORS)

## F5 — Operações pesadas

- ⬜ 27 · Blue/green e staging (Docker)
- ⬜ 28 · Tarefas duráveis e sagas
- ⬜ 29 · Várias réplicas e liderança
- ⬜ 30 · Telemetria (OTel) e modo dev
- ⬜ 31 · DR, zero downtime e caos

## F6 — Apps em Python

- ⬜ 32 · Ativos em Python
- ⬜ 33 · Fornecedores em Python
- ⬜ 34 · Serviços em Python
- ⬜ 35 · Fim do SDK Rust e conformidade dos 5 apps

## F7 — Virada

- ⬜ 36 · CI local só Python e validação pesada
- ⬜ 37 · Documentação final
- ⬜ 38 · Virar hub.example.com para o hub Python

## ❓ Needs your call

- Validar hub-dev.example.com ao fim de cada fase (sem pendência agora)

## ⛔ Blocked

- Nada bloqueado

## ⬜ Debts

- SSO/OIDC (F1c deixou para depois)
- Reload do keyring com lock distribuído (multi-réplica)
- MCP pelo Langdock do cliente depende da TI liberar o domínio no proxy
