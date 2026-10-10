# Roadmap — Application Hub (stack Rust + Topcoat)

> Nota (08/10/2026): a stack oficial passou a ser Python (`platform-py/`); o código Rust citado abaixo foi removido da `main` e está arquivado na branch `stack/rust-topcoat`.

Visão consolidada do que foi feito, do que está no radar e do que foi abandonado. Detalhe técnico de cada limitação: `docs/PENDENCIAS.md`. Decisões de arquitetura: `docs/FAQ-ARQUITETURA.md`.

Legenda: ✅ concluído · 🎯 no radar · ⏸️ registrado para depois · ❌ abandonado/substituído

---

## ✅ Concluído

### Mockup e definição (branch `main`)
1. Mockup interativo do hub (shell, rail, catálogo, 3 apps, Administração, Operações) — white label, arquivos organizados.
2. FAQ de arquitetura (itens 1–14): stacks, validação no deploy, pacote `.hubapp`, containers isolados, `hub dev`, limites no Windows sem admin, backup, deploy UI/CI, múltiplas abas, conclusão garantida de ações, login/SSO.
3. Convenção de branches por stack (`stack/<nome>`); escolha da stack Rust + Topcoat.

### Fase 1 — Fundação
4. Spike do Topcoat 0.9 (API real, custo de saída) e workspace Cargo + CI.
5. `hub-manifest`, `hub-registry`, `hub-gateway` (identidade, anti-traversal, streaming), `hub-runtime` (Docker/Podman API, limites e hardening), `hub pack`, `hub dev`.
6. Shell SSR em Topcoat com o visual do mockup.

### Plataforma apresentável
7. Os 3 apps do mockup por composição de fragmentos (loading, skeleton, single viewport) — sem iframe.
8. Administração completa: configurações, variáveis, funções e permissões, logs em tempo real, auditoria, saúde.
9. Operações completas: implantações com pipeline de 7 etapas, containers, backups, SDK e pacotes, tarefas.
10. Containers: compose com hardening e isolamento de rede, deploy ligado ao runtime, plano de controle restrito, allowlist de registries.
11. Testes de carga no perfil da VM de destino (4 vCPU, 8 GB RAM, 80 GB disco) e limites/prioridade do hub.

### Ondas 1–8
12. **Onda 1** — apps com rotinas reais: CRUD com concorrência otimista, busca/filtro, importação Excel grande, exportação CSV/XLSX, anexos S3, PDF, API externa com circuit breaker, relatório pesado; gateway em streaming e limite de upload por app.
13. **Onda 2** — `hub-sdk` (tarefas duráveis com checkpoint, idempotência, outbox, `/_hub/*` com token), aba Tarefas, teste de caos, variáveis e connection strings vinculadas por app × ambiente e injetadas no deploy, provisionamento de banco por app.
14. **Onda 3** — login local, sessões, RBAC real, usuários, tokens de API para CI, SSO/OIDC pronto e desligado.
15. **Onda 4** — staging e produção isolados, blue/green sem indisponibilidade, deploy assíncrono, cosign/scanner opcionais, reconciliação após queda do hub.
16. **Onda 5** — agendador de backups + DR (banco, bucket, arquivos), supervisão com backoff e alerta de OOM, sino de alertas, multi-abas (loader e SSE compartilhado), staging em origem separada.
17. **Onda 6** — validação: Playwright (roteiro de apresentação), multi-abas em navegador real, zero downtime sob carga, CI real no GitHub Actions (substituído em 28/09/2026 pelo CI local `platform/scripts/ci-local.sh`), DR, paridade `hub dev` × produção, ZAP/`cargo audit`/`cargo deny`, carga realista.
18. **Onda 7** — segunda stack oficial de apps: **Python/FastAPI** (`hub_sdk` Python, app Gestão de Contratos, python-calamine para leitura de Excel), contrato formal (`platform/docs/CONTRATO-APP.md`) e suíte de conformidade (4/4 apps).
19. **Onda 8** — Fase 1 pendente + Fase 2: design tokens formais com white label (cor da marca) e regressão visual; observabilidade com OpenTelemetry (traces hub → app → banco, métricas RED/saturação, página Observabilidade, OTLP + perfil Collector/Jaeger opcionais); cofre com criptografia envelope (segredos do `admin.json`, backups cifrados, rotação, leitura de volta da cópia externa); SLOs + status page com incidentes automáticos; auditoria imutável (cadeia de hashes com checkpoints assinados, verificação e exportação); feature flags nos SDKs Rust e Python.
20. **Onda 9** — Fase 3 (experiência): Início personalizável, Inbox unificada, busca federada ⌘K, onboarding, analytics com privacidade, WCAG 2.2 AA, i18n e tema escuro. **Onda 10** — Conexões (SharePoint pelo Graph e S3) com broker de armazenamento na rede interna dos apps.
21. **Onda 11** — API e MCP: arquivo de configuração do hub (`HUB_CONFIG_FILE`, TOML; env sobrescreve; inválido = não sobe); **API pública versionada** `/api/v1` (fachada das rotas internas, mesmo RBAC da UI, RFC 9457, paginação, limite de taxa, auditoria das mutações, OpenAPI 3.1 gerado do código + `/api/docs` sem script inline); **servidor MCP** do hub (Streamable HTTP em `/mcp`, ligado por padrão, `hub mcp` stdio na CLI; ferramentas filtradas por usuário, confirmação de destrutivas, auditoria, limites) que **agrega as ferramentas dos apps** (`<app>.<ferramenta>`, chamadas pelo hub em nome do usuário pela rede interna); tokens com dono e escopos `api:read`/`api:write`/`mcp`/`app-api`/`secrets:reveal` (+ tokens pessoais); **API obrigatória nos apps** (OpenAPI no pacote validado no passo Contrato; exceção só por configuração) e **MCP interno** gerado do OpenAPI nos SDKs Rust/Python, conferido no candidato antes da troca; API v1 nos 4 apps de demonstração; conformidade, testes e E2E.
22d. **Onda 12 — itens 2.4, 2.8 e 2.9 (30/09/2026)**: OpenAPI 3.1 tipado com `utoipa` (tabela única `OPS`, teste de contrato com respostas reais, lint Redocly, `inputSchema` do MCP dos mesmos tipos, SDKs com tipos); i18n pt-BR/en/es de Administração, Operações, Conexões, Inbox, OAuth, credenciais e mensagens do servidor (catálogo com moldes, varredura de textos fixos); sagas com compensação e envios em duas fases nos SDKs Rust e Python (exemplo no `ativos`). **Onda 12 concluída.**
22c. **Onda 12 — itens 2.5, 2.6 e 2.7 (30/09/2026)**: prontidão obrigatória `GET /{app}/_hub/ready` (SDKs, blue/green, recusa de candidato sem a rota, conformidade); backups com privilégio mínimo (papel só leitura por banco, chave só leitura por bucket, no cofre, rotação); "Provisionar armazenamento" (bucket + usuário MinIO restrito + conexão S3 do broker, idempotente, com desfazer e desprovisionamento seguro, API/MCP).
22b. **Onda 12 — item 2.1 (29/09/2026)**: anti-phishing com os apps na mesma origem — apps sem JavaScript (CSP + Trusted Types + sanitizador), comportamentos declarativos `data-hub-*` (atualização parcial, modais, tabelas, gráficos, abas, validação…), JavaScript próprio só por exceção revisada (sha384, REVISE no pipeline, aprovação separada e auditada, SRI) e frase de segurança nas telas de credencial.
22a. **Onda 12 — item 2.2 (29/09/2026)**: MCP conforme a especificação atual (revisão 2026-07-28 + legado) com **OAuth 2.1 no próprio hub** (descoberta, registro dinâmico e documentos de metadados de cliente, PKCE, consentimento, refresh com rotação/reuso, token amarrado ao `/mcp`) — clientes remotos como o Langdock conectam só com a URL.
22. **Onda 12 — item 2.3 (29/09/2026)**: estado de execução compartilhado no PostgreSQL da plataforma (crate `hub-state`, `HUB_STATE_DB_URL`) — sessões, limitador de login, limites de taxa, sessões MCP, `confirm_token`s (pendentes sobrevivem a reinício), step-up, staging, jobs, travas de implantação; liderança por lease com fencing (outbox, agendador, supervisão, reconciliação, varreduras); arquivos do volume compartilhado coerentes entre réplicas (trava + geração); **hub-web com várias réplicas** atrás de balanceador sem sessão grudada (`docker-compose.replicas.yml`, prova `loadtest/e2e/w12-replicas.mjs`).
23. Cinco rodadas de revisão de segurança com correções (críticas e importantes resolvidas) e documentação viva (HANDOFF, PENDENCIAS, CLAUDE.md, README).

---

## 🎯 No radar

**Decisões pendentes**
1. ~~**Tela "Conexões"** com **SharePoint** como primeiro tipo~~ ✅ onda 10 (28/09/2026): cadastro único no cofre, "Testar conexão", vínculo app × ambiente, broker do hub (o app nunca recebe a credencial), SDK Rust/Python (enviar, baixar, listar, apagar, mover, gerar link) e S3 na mesma interface. Validação com o tenant real: responsabilidade da implantação (`docs/IMPLANTACAO.md` §6).
2. ~~**Armazenamento de objetos em produção**~~ ✅ decidido (28/09/2026): **MinIO** (compilado do código-fonte) — sem serviço de armazenamento gerenciado externo. Atenção: atualizações de segurança exigem recompilar; numa máquina só, a proteção dos arquivos vem dos backups criptografados.
3. ~~**Topcoat**~~ ✅ decidido (28/09/2026): **mantido** — é a camada de páginas do hub (`hub-web`).

**Responsabilidade da implantação (ver `docs/IMPLANTACAO.md`)** — o repositório entrega a aplicação pronta; estes itens dependem do ambiente real e não são pendências do repositório:
4. Rodar a validação numa **VM real de destino** (4 vCPU, 8 GB RAM, 80 GB disco) — carga, DR, zero downtime, caos — com os critérios de aprovação do §9 (números atuais são indicativos, Docker Desktop).
5. App Python: staging e troca sem indisponibilidade sob carga na VM (§10).
6. cosign e trivy/grype com as ferramentas reais (§8); SSO com o Entra ID (§7); SharePoint com o tenant real (§6); chave mestra fora do volume e âncora externa da auditoria (§4–§5, já prontos em `docker-compose.prod.yml` e nos scripts).

**CI/CD** ✅ (28/09/2026): GitHub Actions desativado; CI local (`platform/scripts/ci-local.sh --quick|--full`), release local com `gh release` (`platform/scripts/release.sh`) e compose de produção (`platform/docker-compose.prod.yml`).

**Segurança e dados**
7. ~~Cofre/criptografia em repouso para segredos e pacotes de backup; verificação de leitura da cópia externa~~ ✅ onda 8; chave mestra fora do volume no compose de produção (secret + `scripts/gen-master-key.sh`) — falta só adaptador de KMS externo (ver PENDENCIAS).
8. Papel de backup somente leitura por banco e chave de object store por app.
9. MFA, logout no IdP, cookies `__Host-`.

**Plataforma e operação**
10. ~~Provisionamento de bucket/chave por app pela UI (hoje só banco).~~ ✅ onda 12 (2.7): Conexões → "Provisionar armazenamento".
11. Capacidade: ajustar `shared_buffers`/Postgres e critério para banco dedicado; ~~jobs do hub persistentes e com ids não sequenciais~~ ✅ onda 12 (progresso no banco de estado, ids da sequência; retomar um job depois da queda da réplica ainda não).
11a. Várias réplicas em **VMs diferentes** (runtime de containers remoto, sistema de arquivos compartilhado) e observabilidade agregada entre réplicas (hoje por réplica) — ver PENDENCIAS "Onda 12 — lacunas".
12. "Reinício pendente" por ambiente; aplicar variáveis sem ação manual (opcional).
13. SDKs: saga com compensação e arquivos em duas fases; streaming de respostas idempotentes grandes.
14. Demais itens de `docs/PENDENCIAS.md` → "Limitações conhecidas".

---

## ⏸️ Registrado para depois

- Podman como runtime (`podman compose`) — documentado, não testado.
- Limites do `hub dev` em Linux e macOS (hoje só compilados).
- Pacotes de exemplo presos ao digest de imagem local.
- Header do mockup: busca global e menu de perfil completo (o sino de alertas já existe).

---

## ❌ Abandonado ou substituído

| Item | Decisão | Motivo |
|---|---|---|
| **GitHub Actions** (CI/CD hospedado) | Desativado (28/09/2026) | Decisão do usuário: build/CI/CD locais (`ci-local.sh`, `release.sh`); workflows mantidos só como referência, gatilho manual; federação OIDC do GitHub deixa de ser objetivo |
| **Fase 4** (IA, scorecards, catálogo self-service, FinOps, workflows) | Descartada | Decisão do usuário (27/09/2026) |
| **Armazenamento de objetos gerenciado** (serviço externo) em produção | Não adotado | Mantido MinIO na própria VM |
| Nomes alternativos (Taba, Cais, Trama…) | Não adotados | Mantido **Application Hub** |
| Composição de apps por **iframe** | Abandonado | Pedido explícito; substituído por fragmentos HTML e Web Components |
| **Convites e tela de Pessoas** | Abandonado | Fora do escopo definido (foco em Administração e Operações) |
| **Dockerfile como artefato de importação** | Rejeitado | Exigiria build no hub (lento e inseguro); importa-se imagem pronta + `.hubapp` |
| Gateway como **binário separado** | Descartado | O spike provou o gateway no mesmo processo (TowerRoute) |
| Fragmentos **estáticos** gerados do mockup para todas as telas | Substituído | Telas dinâmicas renderizadas pelos apps (onda 1) |
| Auditoria via cabeçalho **`x-hub-audit`** | Substituído | Outbox transacional (entrega garantida mesmo com queda) |
| Progresso por **SSE através do gateway** | Substituído | Polling (o gateway não repassa SSE de fragmentos) |
| **Bloqueio rígido** de conta após erros de senha | Substituído | Atraso progressivo (evita negação de serviço contra contas) |
| **Staging na mesma origem** do hub (modo por cookie) | Substituído | Host próprio (`staging.localhost`) com sessão isolada |
| Jobs de implantação **só em memória** | Substituído | Persistidos com reconciliação na partida |
| Imagens públicas do **MinIO** | Substituído | Deixaram de ser publicadas; compilado do código-fonte |
| **MinIO gateway / rclone** para expor SharePoint como S3 | Não adotado | Gateway removido do MinIO; rclone experimental — proposta é a tela Conexões |
| **openpyxl** para leitura de Excel no Python | Substituído | python-calamine (decisão do usuário); openpyxl só para escrita |
| Tarefas no **mesmo processo web** (Python) | Substituído | Processo separado (capacidade 30 → ~100 usuários) |
| Suporte oficial a **outras linguagens** (.NET, Node, Go) | Não adotado | Só Rust e Python oficiais; outras possíveis via contrato + conformidade |

---

## Plano atual (decisão de 27/09/2026)

- **Fase 4 (IA, scorecards, catálogo self-service, FinOps, workflows) — descartada** ❌ pelo usuário.
- **Onda 8 — Fase 1 pendente + Fase 2 — ✅ concluída (28/09/2026):** design tokens formais; observabilidade com OpenTelemetry (métricas e traces); cofre/criptografia em repouso de segredos; SLOs e status page; auditoria imutável (cadeia de hashes); feature flags (por app, ambiente, função e percentual) nos SDKs Rust e Python. Revisão de segurança corrigida: chave de verificação derivada da semente, manifesto de arquivamento assinado, âncora anti-rollback no keyring + publicação externa da cabeça e os menores (ver PENDENCIAS).
- **Onda 12 — itens 2.4 (OpenAPI tipado), 2.8 (i18n) e 2.9 (sagas e envios em duas fases) — ✅ concluídos (30/09/2026); com eles a onda 12 está completa.** Lacunas em PENDENCIAS.
- **Onda 12 — itens 2.5 (prontidão), 2.6 (backups com privilégio mínimo) e 2.7 (provisionar armazenamento) — ✅ concluídos (30/09/2026).** Lacunas em PENDENCIAS.
- **Onda 12 — item 2.1 (anti-phishing, apps na mesma origem) — ✅ concluído (29/09/2026):** domínio por app descartado pelo usuário; apps sem JavaScript (CSP só com scripts do hub, Trusted Types, `form-action` do próprio app, `/_frag` em sandbox), sanitizador DOMParser com lista de permissão, `data-hub-*` com os 4 apps migrados, `manifest.scripts` com revisão de segurança separada + SRI, frase de segurança por usuário (cifrada). Riscos residuais em PENDENCIAS.
- **Onda 12 — item 2.2 (MCP conforme a especificação / Langdock) — ✅ concluído (29/09/2026):** `/mcp` nas duas eras do transporte Streamable HTTP; hub como servidor de autorização OAuth 2.1 + recurso (RFC 9728/8414/7591/8707/9207/7009, Client ID Metadata Documents), consentimento, Apps autorizados, Clientes OAuth na administração, estado no banco de estado (várias réplicas); validado por cliente independente (SDK oficial TypeScript) e pelo E2E de réplicas. Lacunas em PENDENCIAS.
- **Onda 12 — item 2.3 (estado compartilhado / várias réplicas) — ✅ concluído (29/09/2026):** estado de execução em memória movido para o PostgreSQL (banco `hub` no hub-db, migrações na partida; opcional — sem ele, réplica única com aviso); leases com fencing para o que só pode rodar uma vez; arquivos de dados coerentes entre réplicas; prova com duas réplicas atrás de nginx round-robin (login, token, taxa do cluster, MCP/aprovação, staging, tomada de liderança, reinício com pedido pendente). Lacunas em PENDENCIAS.
- **Onda 11 — API e MCP — ✅ concluída (28/09/2026):** decisões do usuário — hub: API REST sempre disponível (a pública `/api/v1` desligável por arquivo de configuração), MCP suportado e **ligado por padrão**, desligável pelo arquivo de configuração; apps: API **obrigatória por padrão**, MCP suportado e **ligado por padrão**, desligável pelo manifesto. Detalhes e lacunas em PENDENCIAS; guia de implantação (clientes de IA) em IMPLANTACAO §14; FAQ item 22.
- **Onda 9 — Fase 3 — ✅ concluída (28/09/2026):** onboarding guiado (tour, primeiros passos, pedido de acesso); inbox unificada (alertas, notificações e aprovações dos apps com decisão pelo canal interno); busca global federada (⌘K) com contrato nos 4 apps; personalização da página inicial (widgets, favoritos, padrão por função); analytics de uso com privacidade; acessibilidade WCAG 2.2 AA (axe em 2 temas × 2 idiomas), i18n (pt-BR/en/es) e tema escuro. Lacunas em PENDENCIAS. Revisão de segurança da onda 9 corrigida (decisão atômica da Inbox, limites por app contra enxurrada, persistência incremental; menores em PENDENCIAS) — **Fase 3 concluída**. Correções adicionais (mesma data): linha rompida do `inbox.jsonl` tratada na abertura e no `append` (nunca corrompe o lote seguinte), poda do mapa de taxa por app/destinatário, evicções da Inbox publicando no feed do SSE, chave do analytics sem cofre isolada num arquivo `0600` próprio, `admin_roles()` por permissão nomeada (não por posição), amostras `.hubapp` regeneradas com `notify_roles`.
