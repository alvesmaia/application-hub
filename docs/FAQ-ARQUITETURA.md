# FAQ — Arquitetura do Hub (Rust + Topcoat)

> Nota (08/10/2026): a stack oficial passou a ser Python (`platform-py/`); o código Rust citado abaixo foi removido da `main` e está arquivado na branch `stack/rust-topcoat`.

> Topcoat está em 0.9 (experimental, jul/2026). Respostas assumem essa maturidade.

## 1. Isso funciona completamente com Topcoat e Rust?

**Sim para a plataforma; não é necessário (nem desejável) para as aplicações.**

| Parte | Rust/Topcoat cobre? | Observação |
|---|---|---|
| Shell (header, rail, catálogo, admin) | Sim | SSR com `view!`, shards e WebSocket |
| Gateway/proxy `/appN/*` | Sim | Axum/Tower (mesmo runtime Tokio) |
| SSO, RBAC, auditoria, segredos, flags | Sim | Serviços Rust + Toasty/Postgres |
| Composição de fragmentos HTML | Sim | Hub busca e injeta no layout |
| SDK JS dos apps (Web Components) | Parcial | Precisa ser JS/TS — roda no browser do app |
| Gráficos/libs JS ricas | Parcial | Servidas como assets via `asset!` |

Riscos: breaking changes do Topcoat; reatividade client-side ainda inicial (usar htmx/Alpine como fallback).

## 2. Como ficam as stacks?

**Plataforma (hub)**
- Rust + Topcoat (shell, UI admin) + Axum (gateway/APIs)
- Toasty + PostgreSQL (registro de apps, permissões, auditoria)
- Vault/Key Vault (segredos), OpenTelemetry (observabilidade)
- OIDC (Entra ID/Keycloak) para SSO
- Tailwind/tokens CSS para tema e white label
- SDK JS/TS leve (`@hub/sdk`) + CLI de empacotamento (`hub` CLI, em Rust)

**Aplicações**
- **Oficialmente suportadas (onda 7): Rust (Axum + `hub-sdk`) e Python ≥ 3.11 (FastAPI +
  `hub-sdk-python`, pacote `hub_sdk`)** — as duas com SDK que já cumpre o contrato, mesmas
  tabelas (tarefas, idempotência, outbox) e o mesmo hash de idempotência; exemplos `ativos`,
  `fornecedores`, `servicos` (Rust) e `contratos` (Python). O manifesto declara
  `"runtime": {"stack": "rust" | "python"}` (informativo, validado; aparece no passo *Contrato*).
- **Qualquer outra stack com servidor HTTP** (.NET, Java, Node, Go…) é aceita implementando o
  contrato formal `platform/docs/CONTRATO-APP.md` e passando na **suíte de conformidade**
  `platform/conformance` (caixa-preta por HTTP: fragmentos, `x-hub-user`, `/_hub/*` com token,
  idempotência, auditoria, tarefas, 429 + `Retry-After`, SIGTERM, cookies/CSP) — sem SDK oficial.
- Front-end em um dos modos:
  - **Fragmento HTML** (SSR: Razor, Thymeleaf, Topcoat, Jinja2, Django…) — padrão.
  - **Web Component** (React, Vue, Svelte, Lit) — front-end rico.
- Obrigações: manifest, endpoint de health (`GET /<id>` → 200), aceitar a identidade do hub
  (`x-hub-user`, JSON base64url), CSS com escopo, sem script inline — detalhes no contrato.

**Diferenças práticas entre as stacks oficiais** (medidas na onda 7, ver
`platform/loadtest/RESULTADOS.md`): Rust usa todos os núcleos num processo; Python escala por
processos (`UVICORN_WORKERS`), com o semáforo de operações pesadas por processo e a aba do Excel
carregada em memória nativa pelo `python-calamine` (teto configurável).

## 3. É possível validar a aplicação no deploy?

**Sim.** Pipeline de admissão do hub (via CLI/API), bloqueante:

1. **Manifest**: schema válido, rotas sem conflito, permissões declaradas existentes.
2. **Assinatura**: pacote/imagem assinados (cosign/Sigstore) por publicador autorizado.
3. **Segurança**: scan de vulnerabilidades e SBOM (Trivy/Grype), sem segredos no artefato.
4. **Contrato**: health check, rota do fragmento/Web Component responde, SDK compatível (semver).
5. **Front-end**: CSS escopado, sem globais JS, CSP compatível com origens declaradas.
6. **Smoke test**: sobe em staging, hub renderiza a rota de entrada e verifica erro/latência.
7. **Aprovação**: gate manual opcional por ambiente (ex.: produção) e registro em auditoria.

Falhou em qualquer etapa → deploy rejeitado com relatório.

## 4. O SDK pode definir um formato de pacote importável (manual ou via API)?

**Sim.** Proposta: `.hubapp` (zip assinado, estilo `.nupkg`/`.msapp`):

```
minha-app.hubapp
├── manifest.json        # id, versão, rotas, menu, permissões, variáveis requeridas, modo (fragment|webcomponent)
├── ui/                  # bundle do Web Component ou assets estáticos
├── runtime.json         # referência da imagem OCI (registry@sha256:...)
├── sbom.spdx.json
├── icon.svg
└── signature            # assinatura do conteúdo
```

- Gerado por `hub pack`, publicado por `hub publish` ou `POST /api/apps/packages`.
- Import manual: upload na tela Aplicações → passa pela mesma validação do item 3.
- Versões imutáveis; rollback = reativar versão anterior.

**Implementado hoje** (`platform/crates/hub-manifest`, `hub-cli`, `hub-ops`; ver `platform/README.md`): `hub pack` gera o `.hubapp` (manifest, `runtime.json` com imagem por digest, `ui/`, `checksums.txt`) e `POST /api/ops/packages` (UI ou CI) roda o pipeline real. O manifest declara recursos (padrão da plataforma **1 vCPU / 1 GiB / 100 processos**; política até 4 vCPU / 8 GiB), `limits.max_upload_mb`, variáveis requeridas, permissões, `sdk` e o contrato das **tarefas duráveis** (`jobs: [{name: "<id>.<nome>", max_duration_s ≤ 24 h, max_attempts ≤ 20}]`), validado pelo `hub-manifest` e pelo passo *Contrato* (tarefas exigem `sdk` 1.x). Variáveis e connection strings **não** vão no pacote: ficam na Administração, vinculadas ao app e ao ambiente, e são injetadas no container em cada implantação. Ainda não há: `hub publish`/`hub promote` na CLI (a API existe), `icon.svg`/SBOM obrigatórios. A assinatura (cosign) e o scan de vulnerabilidades existem desde a onda 4, opcionais por configuração (ver item 5).

## 5. Pacote próprio ou Dockerfile?

**Os dois, com papéis diferentes. Recomendado: pacote `.hubapp` + imagem OCI.**

| Critério | Só pacote (.hubapp com código) | Só Dockerfile/imagem | `.hubapp` + imagem OCI |
|---|---|---|---|
| Qualquer stack | Não (hub teria que saber executar cada uma) | Sim | Sim |
| Metadados de plataforma (menu, permissões, variáveis) | Sim | Não (labels são pobres) | Sim |
| Import manual/API | Sim | Difícil (imagem grande) | Sim (pacote leve referencia a imagem) |
| Validação/assinatura | Sim | Sim | Sim |
| Runtime padrão (K8s, Container Apps) | Não | Sim | Sim |

- **Dockerfile** fica no repositório do app e gera a imagem (execução).
- **`.hubapp`** descreve a integração com o hub e aponta para a imagem por digest (governança).
- Apps só de front-end (Web Component estático) podem ir apenas no `.hubapp`, sem imagem.

Evitar Dockerfile como artefato de import: exige build no hub, lento e inseguro. Importar sempre imagem já construída e assinada.

**Implementado hoje (onda 4):** o `.hubapp` referencia a imagem **por digest** e o pipeline verifica a imagem, não só o pacote: o passo *Assinatura* roda `cosign verify` da imagem por digest quando `HUB_COSIGN_PUBKEY` (chave) ou `HUB_COSIGN_IDENTITY`/`HUB_COSIGN_ISSUER` (keyless) estão definidos — configurado e sem assinatura válida (ou sem o binário) → rejeita; o passo *Segurança* mantém a busca de segredos e o SBOM e, com `HUB_SCANNER=trivy|grype` (binário, ou container `HUB_SCANNER_IMAGE` fixado por digest), faz o scan de vulnerabilidades da imagem com política `HUB_SCAN_FAIL_ON=LOW|MEDIUM|HIGH|CRITICAL` (valor inválido impede a partida) e `HUB_SCAN_MODE=block|warn`, mostrando a contagem por severidade e os principais CVEs no passo. Sem configuração, os dois mostram "não configurado". A verificação é feita invocando os binários oficiais (sem crate sigstore em Rust: menos superfície própria de criptografia). Ver `platform/README.md`, "Assinatura e scan de vulnerabilidades". Ainda não há: política de publicadores por app (quem pode assinar o quê), verificação de atestados SLSA/SBOM assinado, cache/banco offline do scanner.

## 6. Cada app pode rodar em container exclusivo, com recursos definidos, sem impactar os demais?

**Sim.** Compatível com **Docker e Podman** (não exclusivo de nenhum).

**Compatibilidade de runtime**
- O hub fala com a **Docker Engine API** — Docker nativamente e Podman via `podman.socket` (API compatível).
- Cliente Rust: `bollard` (funciona com os dois apontando para o socket).
- Configuração: `HUB_CONTAINER_SOCKET` (`/var/run/docker.sock` ou `/run/podman/podman.sock`; rootless: `$XDG_RUNTIME_DIR/podman/podman.sock`).
- Usar apenas recursos comuns aos dois (imagens OCI, limites cgroups, redes bridge, volumes, restart policy). Nada de Swarm nem Quadlet como requisito.
- Escala multi-host: mesmo manifest traduzido para Kubernetes/Nomad (adaptador separado).

**Provisionamento automático**
1. `.hubapp` aprovado → hub faz pull da imagem por digest.
2. Cria container exclusivo `hub-app-<id>` com limites do manifest (clampados pela política da plataforma).
3. Rede própria por app; acesso só via gateway do hub.
4. Health check + `restart: unless-stopped`; hub monitora e reinicia/alerta.

**Limites por container (cgroups v2, ambos os runtimes)**

| Recurso | Opção |
|---|---|
| CPU | `--cpus`, `--cpu-shares` |
| Memória | `--memory`, `--memory-swap` (OOM afeta só o container) |
| Processos | `--pids-limit` |
| Disco (I/O) | `--device-read-bps`, `--device-write-bps` |
| Disco (espaço) | volume por app com cota |
| Rede | rede dedicada; banda via `tc`/CNI |

**Proteção da plataforma**
- Hub e seus serviços com recursos reservados (cgroup/slice próprio).
- Reserva para o sistema operacional.
- Admission control: não sobe app se a soma dos limites excede a capacidade do host.

**Segurança**
- Rootless (Podman) ou userns-remap (Docker); `--cap-drop=ALL`, `--security-opt no-new-privileges`, seccomp padrão, `--read-only`.
- Apps não confiáveis: runtime gVisor (`runsc`) ou Kata — ambos plugáveis em Docker e Podman.

**Limites do isolamento**
- Kernel compartilhado (mitigar com gVisor/Kata).
- Contenção residual de cache de CPU, I/O e banda — mitigar com limites de I/O e nós dedicados para apps pesados.

**Onda 5 — quedas em laço e OOM:** o hub supervisiona cada container de app (eventos `die`/`oom` do engine + consulta de `OOMKilled`, `RestartCount`, código de saída e health check). Uma queda isolada o engine religa na hora (`unless-stopped`); OOM ou 2 quedas em 10 min deixam o app **Degradado** (Saúde e Containers mostram o motivo e a ação sugerida, ex.: aumentar a memória), a restart policy vira `no` e o hub religa com espera crescente (5 s → 10 s → 20 s … teto 5 min); estável por 60 s volta a `Disponível`. Alerta no sino dos administradores, auditoria e log. Para o usuário, o app mostra "reiniciando" dentro do shell e recarrega sozinho quando volta. Correções da revisão: a supervisão reconhece o container pelas labels e pelo id — recriado por uma implantação ou "Aplicar" com o app ainda degradado, volta aos reinícios pelo hub e só é dado como recuperado (com alerta) depois de estável; nunca espera uma implantação longa de outro app nem age durante uma restauração. Prova: `platform/loadtest/results/2026-09-27/oom.md`. (256 MiB aplicados pela tela, redeploy com o app degradado até o alerta de recuperação).

## 7. O dev consegue testar localmente (sem Docker e sem plataforma) de forma similar à produção?

**Sim, via `hub dev`** (binário Rust do SDK, mesmo código do shell em modo dev).

1. App roda nativo com a própria stack (`dotnet run`, `npm run dev`, `cargo run`, `python -m contratos`/`uvicorn`…).
2. `hub dev` na pasta do app:
   - lê o `manifest.json` (menu, rotas, permissões);
   - faz proxy de `/<app-id>/*` para o processo local;
   - compõe fragmento/Web Component dentro do shell real (header, rail, tema);
   - injeta identidade simulada (JWT de dev, `--role admin`);
   - fornece variáveis de `.hub/dev.env`, respeitando pública/secreta;
   - simula serviços da plataforma (notificações, auditoria, flags, busca);
   - valida em tempo real as regras de deploy (manifest, CSS escopado, CSP, health).
3. Acesso em `localhost:4173/<app-id>` com hot reload da stack do app.

| Igual à produção | Diferente (como cobrir) |
|---|---|
| Manifest, roteamento, shell, identidade/papéis, variáveis, CSP, validações | Limites de container → `hub dev --limits` ou `--container` |
| | SO/dependências nativas → `--container` ou CI |
| | SSO/Vault reais → staging |

**Implementado hoje** (`platform/crates/hub-cli`, ver `platform/README.md`): `hub dev` serve o mesmo shell do hub-web (crate `hub-shell`: header, carregamento, composição de fragmentos via `/_frag`), faz proxy só para o app do manifest, injeta a identidade de dev no header `x-hub-user` (não um JWT) com `--role`, repassa só as variáveis de `.hub/dev.env` declaradas no manifest e aplica `--limits`. Ainda não há: distinção pública/secreta das variáveis, simulação de serviços da plataforma nem validação em tempo real de CSS/CSP/health; o `--container` também não existe.

## 8. É possível limitar recursos do app em desenvolvimento para simular produção?

**Sim.** Rust não tem isso na linguagem, mas o `hub dev --limits` inicia o app como processo filho com limites do SO, lidos do manifest (`resources: { cpu: 0.5, memory: "512Mi", pids: 100 }`).

| SO | Mecanismo | Crate | Limita |
|---|---|---|---|
| Linux | cgroups v2 (mesmo do Docker/Podman) | `cgroups-rs` / `systemd-run --user --scope` | CPU, memória, PIDs, I/O |
| Linux/macOS | `setrlimit` | `rlimit`, `nix` | memória virtual, processos, arquivos |
| Windows | Job Objects | `windows` | memória, CPU (hard cap), processos |
| macOS | `setrlimit`/`taskpolicy` | `rlimit` | memória, prioridade (CPU exata não confiável) |

- Monitora consumo e alerta perto do limite.
- Sem container ficam de fora: isolamento de rede, banda e filesystem read-only.

## 9. Funciona em Windows sem perfil de administrador?

**Sim, via Job Objects** — não exigem admin para processos do próprio usuário; jobs aninhados funcionam (Windows 8+); processos filhos herdam o job.

Fluxo do `hub dev --limits`:
1. `CreateProcessW` com `CREATE_SUSPENDED`.
2. Job com `JOB_OBJECT_LIMIT_JOB_MEMORY`/`PROCESS_MEMORY`, `JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP`, `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
3. `AssignProcessToJobObject` → `ResumeThread`.
4. `QueryInformationJobObject` para monitorar.

Limitações:
- I/O de disco e banda de rede podem exigir elevação — tratados como não suportados.
- Estouro de memória falha a alocação (produção faz OOM kill); o `hub dev` pode encerrar o job para simular.
- AppLocker/WDAC podem bloquear o binário: distribuir `hub.exe` assinado (Winget/Intune).
- WSL2/Docker Desktop costumam exigir admin para instalar; fidelidade total fica no staging.

**Implementado hoje** (`platform/crates/hub-cli`, ver `platform/README.md`; vale também para o app Python — `hub dev --limits -- .venv/Scripts/python -m contratos`, o launcher do venv e o intérprete ficam no mesmo job): criação suspensa + `AssignProcessToJobObject` + retomada das threads; `JOB_OBJECT_LIMIT_JOB_MEMORY` (memória comprometida do job), CPU hard cap, processos ativos e `KILL_ON_JOB_CLOSE`. Ao atingir o limite de memória o `hub dev` avisa ("limite de memória atingido"), mas não encerra o job.

## 10. O hub consegue gerenciar backup/snapshots das aplicações?

**Sim.** Containers são stateless; o backup cobre o estado:

- **Versão**: `.hubapp` + digest da imagem.
- **Dados**: volumes do app.
- **Bancos**: dump consistente via hooks.
- **Configuração**: variáveis, permissões, metadados (segredos só como referência ao Vault).

Manifest:
```json
"backup": {
  "volumes": ["data"],
  "schedule": "0 2 * * *",
  "retention": { "daily": 7, "weekly": 4, "monthly": 6 },
  "hooks": { "pre": "/app/backup-pre.sh", "post": "/app/backup-post.sh" }
}
```

Execução (Docker e Podman):
- Container auxiliar efêmero monta o volume (API comum aos dois).
- restic/Kopia: incremental, deduplicado, criptografado.
- Destino S3 compatível (MinIO, Azure Blob, S3), fora do host.
- Snapshots de storage opcionais (ZFS/Btrfs/LVM, disco de nuvem).
- Job de backup com CPU/memória/I/O limitados — não impacta outros apps.

No hub: agendamento, retenção, backup sob demanda (UI, `hub backup`, API); restore completo ou só dados; clonar para staging; teste periódico de restore com alerta; auditoria.

Limites:
- Sem hooks, o backup é apenas crash-consistent.
- Bancos externos só se registrados no manifest com credencial de backup.
- Dev local: `hub dev --seed` para dados de exemplo.

**Onda 5 — implementado:** agendador (cron de 5 campos no fuso `HUB_BACKUP_TZ`, padrão America/Sao_Paulo; por app × ambiente; jitter; um job por app; execução perdida `executar`/`pular`); cada backup é um pacote com o banco do app (`pg_dump -Fc`), o bucket (`mc mirror`), os arquivos e um manifesto com sha256 de tudo, feito por containers de tarefa descartáveis na rede `hub-data` (credenciais de administração nunca na rede dos apps); hooks `backup.pre/post` do manifesto; restauração com o app parado e religado com health check; cópia externa S3 opcional; falhas viram alerta. Teste de DR: `platform/loadtest/results/2026-09-27/dr.md` (1,79 M linhas + 15 anexos restaurados byte a byte, RTO ≈ 51 s, RPO = intervalo da agenda). **Correções da revisão:** a restauração é atômica — segura a trava do app, mostra "Em manutenção (restauração)" (bloqueia implantação, aplicar, iniciar, backup e a supervisão), tira um snapshot de segurança antes, põe o banco atual de lado e só o apaga no fim; qualquer falha desfaz tudo e, se nem isso der, o app fica parado em manutenção com alerta (nunca volta com dados pela metade); app parado pelo operador continua parado; credenciais das tarefas só em arquivos 0600. DR reexecutado com as correções: RTO ≈ 57 s.

**Onda 12 (2.6) — backups com privilégio mínimo:** o `pg_dump` de cada banco roda com um papel **só leitura** próprio (`hub_bkp_<banco>`: `CONNECT` só naquele banco, `SELECT` em tabelas/sequências, privilégios padrão para as futuras, sessão somente leitura) e o `mc mirror` com uma chave **só leitura daquele bucket** (`bkp-<bucket>`); senhas/chaves no cofre, com rotação pela tela de Backups. Só a **restauração** usa a credencial de administração, num caminho separado e explícito. Buckets novos de apps vêm de Conexões → "Provisionar armazenamento" (onda 12, 2.7): bucket + usuário do MinIO restrito ao bucket + conexão do broker — o app nunca recebe a chave.

## 11. Deploys são suportados tanto por import no hub quanto por CI/CD?

**Sim.** Ambos usam a mesma API e o mesmo pipeline de validação (item 3).

**Import no hub (UI)**
- Aplicações → Importar: upload do `.hubapp` ou URL do pacote/imagem.
- Relatório de validação, escolha de ambiente, aprovação manual se configurada.

**CI/CD**
```yaml
- uses: actions/checkout@v4
- run: docker build -t ghcr.io/org/minha-app:${{ github.sha }} . && docker push ...
- uses: hub/setup-cli@v1
- run: hub pack --image ghcr.io/org/minha-app@${{ steps.push.outputs.digest }}
- run: hub publish minha-app.hubapp --env staging --wait
```
- Autenticação via OIDC do GitHub (sem segredo fixo), confiança por repositório/branch.
- `--wait` falha o job se o deploy for rejeitado.
- `hub promote minha-app@1.4.0 --to prod` promove a mesma versão validada, sem rebuild.
- Também aplicável a Azure DevOps e GitLab.

**Regras comuns**
- Pacotes assinados; versões imutáveis.
- RBAC de deploy por app e ambiente; aprovação obrigatória para produção (opcional).
- Rollback via UI ou `hub rollback`.
- Auditoria: quem, origem (UI/CI), commit.

**Recomendação:** restringir import manual a staging ou a apps sem repositório, com aprovação obrigatória — assim o CI permanece a fonte da verdade em produção.

**Atualização (28/09/2026) — CI local, sem GitHub Actions:** o GitHub Actions deixou de ser usado neste projeto; build, testes e publicação rodam localmente (`platform/scripts/ci-local.sh`, `platform/scripts/release.sh`). O **endpoint de CI continua o mesmo e vale para qualquer CI** — Azure DevOps, GitLab, Jenkins, um runner self-hosted ou o próprio script local (`platform/scripts/ci-publish.sh`: token de API, `hub pack`, `POST /api/ops/packages?origin=ci&commit=…&wait=true` com Bearer, aprovação). A única exigência de rede é o chamador estar dentro de `HUB_ADMIN_ALLOWED_CIDRS` (rede corporativa/VPN). O YAML acima é ilustrativo; a federação OIDC do GitHub deixou de ser um objetivo.

**Implementado hoje (onda 3):** o CI autentica com **token de API** (Administração › Tokens de API: escopo por apps, ambientes e ações `upload`/`status`, validade, revogação; guardado só como hash) em `Authorization: Bearer` no `POST /api/ops/packages`; fora do escopo → 403, revogado/vencido → 401, e a auditoria registra `token:<nome>`. A federação OIDC do GitHub (sem segredo fixo) segue pendente.

**Implementado hoje (onda 4 + correções da revisão):** staging e produção são containers separados (`hub-app-<id>__staging` / `hub-app-<id>`; o separador `__` não existe em ids, e a posse é conferida pelas labels antes de mexer em qualquer container), cada um com as suas variáveis (vínculo app × ambiente), volume, rede de dependências (`hub-deps-<id>__staging` / `hub-deps-<id>`), upstream no gateway e banco próprio (`app_<id>_staging`) — staging **nunca** recebe a credencial do banco de produção: sem banco de staging, a implantação em staging falha com a instrução de provisioná-lo; o gateway serve produção e só mostra staging a quem tem "Editar configurações" e ligou o modo staging **na sessão** (POST same-origin com gesto do usuário, no servidor, 30 min; faixa STAGING na tela e ações de administração bloqueadas enquanto isso). Implantações ficam "Implantando" até o runtime confirmar, são gravadas passo a passo e reconciliadas se o hub reiniciar no meio; falha ao renomear containers desfaz a troca. Aprovar/promover/rollback respondem **202 + job** (o CI acompanha em `GET /api/ops/jobs/{id}`, ou usa `?wait=true`). Toda ativação é **blue/green sem indisponibilidade**: o novo container sobe ao lado do atual, passa no health check, recebe o tráfego numa troca atômica do upstream, o anterior é drenado (requisições em curso, inclusive downloads/uploads em streaming, terminam nele) e fica como `__prev` para **rollback instantâneo** (só se nasceu com as variáveis atuais; com segredo rotacionado ele é removido e o rollback reimplanta). Prova sob carga: `platform/loadtest/results/2026-09-27/zero-downtime.md`. Assinatura cosign e scan de vulnerabilidades: opcionais por configuração (item 5). **Onda 5 (e correções):** staging agora só num host próprio (`HUB_STAGING_HOST`) com sessão própria amarrada à sessão principal (sair do hub encerra o staging) e ticket com prova de posse (o link de staging não funciona repassado a outra pessoa). Atenção: o `/api/*` continua atrás de `HUB_ADMIN_ALLOWED_CIDRS` — runners hospedados do GitHub precisam de runner self-hosted na rede permitida, VPN/túnel ou liberação das faixas de `api.github.com/meta`.

## 12. O usuário pode abrir várias aplicações, uma em cada aba? E a mesma aplicação em várias abas (uma página por aba)?

**Sim para os dois casos.**

- Cada página de app tem URL real sob `/apps/` (`/apps/ativos`, `/apps/ativos/assets`, `/apps/servicos/requests`…; desde a onda 13 — os caminhos antigos `/ativos/…` respondem 301/308 para o novo, com a query). O servidor entrega o shell para essa URL e o `hub.js` busca o fragmento da seção pelo gateway (`/_frag/...`). Por isso funcionam: abrir em nova aba, Ctrl/⌘+clique ou clique do meio no sidebar (o `hub.js` não intercepta cliques com modificadores), favoritos, recarregar e compartilhar link.
- Cada aba é independente: navegação, filtros e estado de tela ficam na aba. O estado de negócio (Administração, Operações, dados dos apps) fica no servidor e é o mesmo para todas as abas.
- Várias aplicações ao mesmo tempo: cada aba carrega só os assets do seu app, e os apps não compartilham JS global (isolamento do SDK).

**Cuidados**

| Ponto | Situação | Encaminhamento |
|---|---|---|
| Tela de carregamento (~4 s) repete em cada aba | **Onda 5:** só na primeira abertura do app sem assets desta versão em cache; deep link, recarregar/voltar, app já aberto na sessão ou assets em cache vão direto ao conteúdo | Implementado |
| Edição do mesmo registro em duas abas | Última gravação vence | Controle otimista (versão/ETag) nas APIs de escrita |
| Logs em tempo real (SSE) abertos em muitas abas | **Onda 5:** uma conexão para todas as abas (Web Locks elege a líder, BroadcastChannel repassa; failover quando a líder fecha) | Implementado |
| Sessão | Cookie `hub_session` compartilhado entre abas: login, logout e expiração (30 min ocioso, 12 h no total) valem para todas; aba com sessão vencida volta ao login e depois à mesma página (`next`) | Implementado na onda 3 |

## 13. Existe uma arquitetura que garanta a conclusão das ações/transações mesmo se o app cair (ex.: estouro de memória)?

**Sim — a queda não é evitável, mas o resultado é garantido se nenhum trabalho importante viver só na memória do processo.** Padrões, oferecidos prontos pelo **SDK da plataforma** (`hub-sdk`):

| Padrão | Garante | Uso típico |
|---|---|---|
| Transação no banco | Tudo-ou-nada; queda no meio não deixa estado parcial | CRUD, abertura de chamado |
| Chave de idempotência | Repetir o pedido após queda devolve o mesmo resultado, sem duplicar | Toda ação que grava; o gateway gera e repassa `Idempotency-Key` |
| Tarefas duráveis (fila no Postgres com *lease*, retentativa com backoff e *checkpoint*) | Operação longa retoma de onde parou em outro worker | Importar Excel, relatórios, exportações grandes |
| Outbox transacional | Efeito colateral (auditoria, notificação, integração) é entregue depois, exatamente uma vez em efeito | Auditoria para a plataforma, e-mails, webhooks |
| Saga com compensação | Ação em vários sistemas termina ou é desfeita de forma controlada | Cadastro + API externa |
| Arquivos em duas fases | Upload só "existe" quando concluído; órfãos são limpos | Anexos |
| Proteção preventiva | Queda vira exceção: limite de operações pesadas simultâneas, streaming, desligamento gracioso | Todos os apps |

**Na plataforma**
- Operações ganha a aba **Tarefas** (estado, progresso, tentativas, reprocessar/cancelar).
- O manifest declara as tarefas do app (nome, tempo máximo, tentativas); o pipeline de implantação valida o contrato.
- Auditoria migra do cabeçalho de resposta para o **outbox**: o hub busca os eventos pendentes do app pela rede interna (hub → app; o app continua sem acesso ao plano de controle) e confirma o recebimento.

**Validação:** teste de caos — derrubar o container no meio de uma importação grande e de uma rajada de gravações e verificar: importação conclui a partir do checkpoint, zero duplicados/parciais, zero eventos de auditoria perdidos.

**Implementado hoje** (`platform/crates/hub-sdk`, usado pelos três apps de demonstração; ver `platform/README.md`): tarefas duráveis no Postgres do app (lease + `SKIP LOCKED`, heartbeat, backoff exponencial com jitter, tempo máximo por tentativa, checkpoint na transação do lote, cancelamento, dead-letter, evento final no outbox), middleware `Idempotency-Key` (repetição devolve a mesma resposta; efetivação marcada na transação de negócio; 409 em paralelo, 422 com pedido diferente) — o `hub.js` envia uma chave por submissão e repete com a mesma em falha transitória —, outbox transacional coletado pelo hub pela rede interna com token por app (o gateway recusa `/_frag/<app>/_hub/*`; o antigo cabeçalho `x-hub-audit` saiu), desligamento gracioso e o semáforo de operações pesadas. Importação e exportação Excel do `ativos` são tarefas; **Operações › Tarefas** lista as de todos os apps com reprocessar/cancelar auditados. O **teste de caos** (`platform/loadtest/chaos/chaos.mjs`: 250 mil linhas + 420 cadastros com novas tentativas, `docker kill` 2× com requisições em voo) passou em todas as verificações — ver `platform/loadtest/results/2026-09-27/chaos.md`. **Onda 7:** SDK Python (`platform/sdk-python`, FastAPI) com a mesma semântica e as mesmas tabelas, usado pelo app `contratos`; o teste de caos do app Python (`loadtest/chaos/chaos-contratos.mjs`) passou em todas as verificações (efeito exatamente uma vez, nenhuma auditoria perdida). Ainda não há: saga com compensação, arquivos em duas fases com limpeza de órfãos, SDK oficial para outras linguagens (elas seguem `platform/docs/CONTRATO-APP.md` + suíte de conformidade), limpeza automática de uploads de tarefas que falharam.

## 14. Como funciona o login e o SSO?

**Login local, com SSO (OIDC) pronto e desligado por configuração** (`HUB_SSO_ENABLED=false`).

- **Login local:** e-mail e senha (Argon2id). Erro sempre "E-mail ou senha incorretos." e mesmo custo e mesmas respostas para conta real, inexistente ou desativada, de qualquer IP (não revela se a conta existe). Sem bloqueio rígido da conta: limite por IP+conta (5 em 15 min) e por IP (30), mais um atraso curto na conta (2 s dobrando, teto 60 s) que não afeta os IPs de onde o dono já entrou — um atacante não tranca o usuário legítimo. "Desbloquear" limpa tudo. A troca de senha também tem limite. O primeiro administrador vem de `HUB_BOOTSTRAP_ADMIN_EMAIL/PASSWORD` ou de uma senha aleatória gravada uma vez em `bootstrap-admin.txt`.
- **Sessão:** cookie opaco `hub_session` (HttpOnly, SameSite=Lax, Secure em HTTPS), guardado no servidor (sobrevive a reinícios); expira em 30 min sem uso ou 12 h; troca a cada login; cai no logout, na troca de senha e ao desativar o usuário.
- **Permissões:** as da tela *Funções e permissões* valem de verdade em cada rota (páginas, `/api/*`, `/_frag/*`), com 403 amigável e menu filtrado; cada função pode limitar as aplicações que abre. Mutações exigem mesma origem (CSRF). O **Administrador é superusuário**: só ele altera Funções e permissões, atribui funções de administrador e mexe em contas de administradores; ninguém altera as próprias funções.
- **Credenciais não vazam para os apps:** o gateway remove o cookie de sessão do hub (e qualquer `hub_*`) e o `Authorization` antes de repassar a requisição, e não deixa o app gravar cookies do hub; a identidade chega só pelo `x-hub-user`.
- **Usuários:** Administração › Usuários (criar com senha provisória, editar funções, desativar, redefinir senha, desbloquear) — tudo auditado com o usuário real. Sem convite por e-mail.
- **Apps:** recebem a identidade real no `x-hub-user` (login, nome, funções, permissões), no mesmo formato de antes; o `hub dev` continua sem login, com identidade de desenvolvimento marcada no header.
- **SSO:** Authorization Code + PKCE com descoberta, `state`/`nonce`, ID token validado pelo JWKS (RS256/ES256). Vincula por emissor + `sub`; ao usuário local pelo e-mail só com e-mail verificado pelo IdP (`email_verified`), nunca a um Administrador e nunca sobrescrevendo um vínculo; grupos do IdP podem definir as funções; criação automática opcional. Para ligar: `HUB_SSO_ENABLED=true` + emissor, cliente e URL de retorno (ver `platform/README.md`). Testado contra um provedor falso nos testes, sem IdP externo.

## 15. Como sei se as aplicações estão saudáveis e rápidas? (observabilidade)

**Em linguagem simples:** o hub é a porta de entrada de todos os apps, então ele mede cada pedido que passa por ele — quantos chegaram, quantos deram erro e quanto demoraram — e mostra isso em gráficos na tela **Operações › Observabilidade**. Quando alguém reclama "ficou lento", dá para abrir o pedido lento e ver, numa linha do tempo, quanto tempo foi gasto no hub, no app e em cada consulta ao banco.

**Como funciona (onda 8):**
- **Métricas RED por app × ambiente** (taxa de pedidos, erros 5xx, latência p50/p95/p99), pedidos em curso, CPU e memória de cada container (lidos do Docker), vagas de operações pesadas e fila de tarefas (informadas pelo app). Guardadas em memória no próprio hub (última hora em baldes de 10 s; 30 dias em baldes de 5 min, gravados em disco) — sem servidor extra.
- **Traces (padrão W3C/OpenTelemetry):** o gateway abre um "span" por pedido e manda o `traceparent` ao app; o SDK (Rust ou Python) continua o trace e cria um span para cada consulta ao banco, colocando o `traceparent` no comentário do SQL (aparece no Postgres). O app **não** manda nada para fora (continua sem acesso ao plano de controle): o hub busca os spans pela rede interna, como já faz com a auditoria. Ações de Administração/Operações e jobs (implantação, backup) também viram spans.
- **Exportar para outras ferramentas (opcional):** com `OTEL_EXPORTER_OTLP_ENDPOINT` o hub envia traces e métricas por OTLP. O compose tem o perfil `observability` (OpenTelemetry Collector + Jaeger, ~450 MiB de teto — cabe na VM de destino como opcional): `OTEL_EXPORTER_OTLP_ENDPOINT=http://hub-otel-collector:4318 docker compose --profile observability up -d` e a UI do Jaeger em `http://127.0.0.1:16686`. Grafana LGTM foi descartado por pesar ~1,5 GB.
- **SLOs e status page:** em **Saúde da plataforma** cada app × ambiente pode ter metas (ex.: 99,5% sem erro e 95% dos pedidos em até 500 ms, janela de 7 ou 30 dias); o hub calcula o "orçamento de erro" que resta e avisa no sino (e abre um incidente) quando ele está queimando rápido demais. **/status** mostra para qualquer usuário logado (ou para todos, se ligado em Configurações) o estado atual, barras de disponibilidade dos últimos 30 dias e os incidentes — automáticos (queda em laço, falta de memória, implantação que falhou, SLO queimando) e notas da equipe.

**Custo:** medido sob a carga realista em `platform/loadtest/RESULTADOS.md` (Onda 8) — o hub continua dentro de 1 vCPU / 1 GB com folga.

## 16. Onde ficam as senhas e segredos? E se alguém copiar o disco?

**Em linguagem simples:** segredos (senhas de banco, connection strings, tokens internos) ficam **cifrados** no disco do hub, e os backups também. Quem copiar o volume de dados sem a **chave mestra** vê só texto embaralhado.

**Como funciona (onda 8):**
- **Criptografia envelope:** uma **chave mestra** (arquivo `HUB_MASTER_KEY_FILE`, 256 bits — recomendada fora do volume de dados, com permissão restrita/ACL) cifra as **chaves de dados**; as chaves de dados cifram cada valor com AES-256-GCM, "amarrado" ao nome da variável (um valor copiado para outra variável não abre). Sem `HUB_MASTER_KEY_FILE`, o hub gera `master.key` na primeira partida e avisa para guardar uma cópia fora do servidor.
- **Migração automática:** na primeira partida da onda 8, segredos em texto claro do `admin.json` são cifrados; o arquivo antigo fica guardado **cifrado** (`admin.json.pre-vault-*.enc`) e o novo é relido e conferido antes de seguir.
- **Rotação:** "Rotacionar chave de dados" (Administração › Configurações) cria uma chave nova e recifra os segredos (as antigas continuam abrindo backups antigos); a chave mestra troca com o hub parado: `hub-web --rotate-master-key [arquivo-novo]` (só recifra o chaveiro; queda no meio é concluída na próxima partida).
- **Backups cifrados:** cada parte do pacote (arquivos, dump do banco, objetos do bucket) é cifrada com uma chave própria logo depois de criada; restaurar decifra, confere o sha256 e apaga o texto claro no fim. Qualquer byte alterado impede a restauração **antes** de parar o app. A cópia externa (S3) é **lida de volta** e conferida arquivo a arquivo.
- **Senhas de usuários e tokens de API** continuam só como hash (Argon2id/SHA-256) no `auth.json` — não há o que decifrar.
- **KMS externo (HashiCorp Vault, KMS de nuvem):** o cofre embrulha as chaves de dados por uma interface (`KeyWrapper`); um adaptador para KMS externo encaixa aí (documentado, não implementado).
- **Auditoria imutável:** cada evento da trilha entra numa cadeia de hashes (cada um inclui o hash do anterior), com checkpoints assinados (Ed25519) a cada 100 eventos e a "cabeça" assinada a cada gravação. **Auditoria › Verificar integridade** detecta qualquer edição, remoção, reordenação ou corte do fim; **Exportar** gera um JSONL com as provas (chave pública, manifesto e a verificação).
- **Quem pode assinar:** só a chave derivada da semente guardada cifrada no chaveiro. Uma chave pública acrescentada à mão no `keyring.json` é ignorada e denunciada na verificação (correção da revisão da onda 8).
- **Começo e retenção:** a cadeia só é aceita se começar do gênese ou do "piso" registrado no **manifesto de arquivamento assinado** (`audit/archive/manifest.json`). Apagar as primeiras linhas até um checkpoint, ou um segmento arquivado, é acusado. Remover histórico antigo só pelo caminho assinado: `hub-web --audit-prune-before <seq>`.
- **Apagar ou voltar a cadeia no tempo:** o chaveiro guarda, cifrado e autenticado pela chave mestra, a **âncora** da cadeia: marcador "cadeia inicializada", seq/hash da última cabeça, piso e segmentos arquivados. Cadeia/cabeça ausentes ou mais velhas → a cadeia fica **bloqueada** (nada é reconstruído sozinho), a verificação falha e um alerta crítico aparece no sino. Recomeço só explícito, depois de confirmar a perda: `hub-web --audit-reinit-chain`.
- **A âncora é obrigatória:** o chaveiro tem um registro selado (release 2) que lembra quais valores protegidos existem e em que **geração** cada um foi gravado (a geração só sobe e entra na autenticação do valor). Por isso apagar a âncora do `keyring.json`, repor um valor antigo dela ou "rebaixar" o chaveiro para o formato antigo **bloqueia** a auditoria (com alerta) em vez de aceitar o estado atual. O estado existente só é adotado uma vez: no chaveiro novo, ou no primeiro uso de um chaveiro anterior a esta versão.
- **Janela local:** para não regravar o chaveiro a cada evento, a âncora é gravada nos checkpoints (a cada 100 eventos), no arquivamento, na partida/desligamento e no máximo a cada 30 s. Um corte dentro dessa janela só é pego pela cabeça assinada e pela âncora externa. A versão anterior do chaveiro fica em `keyring.json.prev`.
- **Limite do que é local:** tudo que fica só no diretório de dados pode ser voltado **inteiro** para uma cópia antiga (chaveiro junto), e aí tudo confere. A tela de Auditoria mostra **"Proteção contra rollback: LOCAL APENAS (configure publicação externa)"** enquanto a publicação externa estiver desligada.
- **Âncora externa (a proteção real):** publique fora do servidor, a cada `HUB_AUDIT_HEAD_EVERY_S` segundos (padrão 1 h), pelo `HUB_AUDIT_HEAD_URL` (webhook HTTP(S) com `POST`, `HUB_AUDIT_HEAD_TOKEN` opcional) e/ou pelo `HUB_AUDIT_HEAD_FILE` (uma linha JSONL num armazenamento de outra máquina). O documento publicado sai do estado em memória e é **assinado inteiro**: seq, hash, id do último evento, piso, segmentos arquivados, id do chaveiro, geração, host e horário. Com a cadeia bloqueada, o hub publica um estado `blocked` assinado, nunca uma cabeça "boa". Uma falha gera alerta no sino. Se passar mais de 2× o intervalo sem publicação bem-sucedida, há alerta de atraso e a tela mostra "ATRASADA". Esse estado vem da memória do hub, não de um arquivo.
- **Conferir a série fora do servidor** (em outra máquina, por exemplo num agendamento diário): `hub-web --audit-verify-published <arquivo-ou-url> [chave-pública|-] [intervalo-máx-s]`. A chave pública está em Exportar › prova e deve ser fixada na primeira vez. O comando confere:
  - a assinatura de cada documento;
  - que a chave e o chaveiro são sempre os mesmos;
  - que o horário só cresce;
  - que seq, id de evento, piso, arquivados e geração nunca descem;
  - que o mesmo seq tem sempre o mesmo hash;
  - buracos maiores que o intervalo informado;
  - publicações `blocked`.

  Sai com 0 quando está tudo certo e com 1 quando há problema. Um rollback do diretório inteiro aparece como regressão ou, se o atacante segurar as publicações, como buraco na série.
- **Arquivamento à prova de queda:** se o hub cair no meio de um arquivamento (segmento já movido, manifesto ainda sem ele), a próxima partida confere o segmento e o registra no manifesto assinado. A conferência verifica o encadeamento e o checkpoint final assinado.
- **Arquivos temporários e memória:** arquivos cifrados, decifrados e temporários nascem com permissão 0600 (Linux) e são removidos em erro; chaves ficam em memória zerada ao sair de uso. **No Windows**, proteja o diretório de dados (e o da chave mestra) com ACL, por exemplo: `icacls C:\hub\data /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F"`.
- **Cópias antigas em claro:** depois da migração, cópias do `admin.json` com segredo em claro e backups em claro anteriores ao cofre (quando já há backup cifrado mais novo) são sobrescritos e apagados, com aviso no sino. Mesmo assim, **rotacione os segredos** que existiam antes do cofre: podem sobrar em blocos do disco (SSD) ou em cópias fora do servidor. `HUB_KEEP_PLAINTEXT_COPIES=1` e `HUB_KEEP_PLAINTEXT_BACKUPS=1` mantêm esses arquivos.
- **Conexões (onda 10):** segredos do cliente, chaves S3 e certificados (chave privada) das conexões de armazenamento ficam cifrados no mesmo cofre (`connections.json`, amarrados ao nome da conexão e ao campo) e nunca voltam para a tela, API, logs ou auditoria; os apps usam as conexões pelo broker do hub e nunca recebem a credencial (ver item 21).

## 17. Dá para liberar uma função nova só para alguns usuários? (feature flags)

**Em linguagem simples:** sim. Em **Administração › Feature flags** você cria uma "chave" para um recurso de um app e decide, por ambiente (staging/produção), quem vê: todo mundo, ninguém (o botão **Desligar** funciona como interruptor de emergência), algumas funções (ex.: só Administradores), alguns usuários, ou uma porcentagem das pessoas (sempre as mesmas pessoas — quem entrou nos 10% continua nos 10%). Não precisa implantar versão nova do app para mudar.

**Como funciona (onda 8):**
- O **hub avalia** as flags para o usuário de cada pedido e manda o resultado ao app no cabeçalho `x-hub-flags` (ex.: `novo-resumo=on`). Como o gateway apaga qualquer `x-hub-*` vindo do navegador, o app pode confiar nele como confia na identidade (`x-hub-user`).
- Para tarefas em segundo plano (sem usuário no pedido), o hub **empurra** as definições para o app pela rede interna (`/_hub/flags`) e o SDK avalia localmente com o **mesmo algoritmo** (Rust e Python testados com os mesmos vetores).
- Escolhemos o hub avaliar (e não o app buscar as regras no hub) porque os apps **não alcançam** o plano de controle — isso continua verdade.
- Tudo é auditado (criar, alterar regras, ligar/desligar) no escopo do app. Exemplos: `novo-resumo` no Gestão de Ativos e `vencimentos-destaque` no Gestão de Contratos.

## 18. Como funcionam a Inbox e as aprovações? (notificações dos apps)

**Em linguagem simples:** o sino no topo virou uma **Inbox** para todo mundo. Ali chegam, num lugar só, os **alertas da plataforma** (para quem cuida dela), os **avisos das aplicações** ("sua importação terminou") e os **pedidos de aprovação** ("aprovar o chamado SD-2026-10000123"). Quem pode decidir aprova ou recusa **ali mesmo**, sem abrir o app, e o resultado volta para a aplicação na hora — o chamado muda de "Aguardando" para "Em atendimento", e quem pediu recebe o aviso. Dá para marcar como lido, arquivar, filtrar (não lidas, aprovações, alertas…) e escolher, por tipo, se quer ver no hub (e-mail e Teams estão previstos, ainda não enviados).

**Como funciona (onda 9):**
- A aplicação grava o aviso ou o pedido no **mesmo outbox** que já usava para a auditoria, **na mesma transação** da ação — se a ação não confirmar, o aviso não existe. O hub busca pela rede interna, entrega na Inbox de quem é destino (usuários e/ou funções) e **só então** confirma ao app (nada se perde nem duplica se o hub cair no meio).
- Ninguém recebe aviso de um app que não pode abrir (a lista de apps de cada função vale também aqui).
- **Aprovar é seguro**: só aparece o botão para quem é destino, tem a permissão "Aprovar solicitações", pode abrir o app e **não** foi quem pediu. O hub chama o app pela rede interna (`/_hub/approvals`) com o token interno daquele app e a identidade de quem decidiu; o app confere a permissão de novo, aplica **uma vez** (repetir responde "já decidido") e registra na auditoria. O navegador nunca fala direto com o app para isso.
- **Pedir acesso**: em **Aplicações**, quem não tem um app vê "Solicitar acesso". O pedido cai na Inbox de quem gerencia usuários; aprovado, o acesso é liberado na hora (e pode ser revogado ali).
- **Ao vivo**: o contador do sino atualiza sozinho por **uma única conexão** por navegador, compartilhada entre abas (o mesmo mecanismo do console de logs). Dentro de um app, por segurança (CSP), o sino mostra o estado da abertura da página e o link para a Inbox.
- **Uma decisão só** (revisão da onda 9): aprovar e recusar ao mesmo tempo não dá dois resultados — o hub reserva o pedido de forma atômica e o segundo clique recebe "já decidido". Num pedido de acesso, o status e a liberação do app são gravados juntos. Se o hub cair enquanto avisa o app, o pedido volta sozinho para "Falha ao avisar o app" com "Tentar de novo". Se o app registrar algo diferente do que foi decidido, o hub não finge que deu certo: marca como divergente e alerta a administração.
- **Um app não afoga os outros**: cada app tem limite de avisos por minuto e de itens guardados; pedidos que ainda esperam decisão (de qualquer app) e pedidos de acesso **nunca** são apagados para dar lugar a avisos. Um app só pode avisar funções administrativas se declarar isso no manifesto (`notify_roles`), e avisos endereçados a pessoas usam login/e-mail (nunca o nome, que pode se repetir).
- **Pedir acesso** tem limites: um pedido pendente por app, até 5 pendentes por pessoa e 24 h de espera depois de uma recusa. Quem aprova precisa, além de gerenciar usuários, alcançar o app; o acesso liberado aparece na tela de Usuários e só um acesso ativo pode ser revogado.
- Exemplos na demo: Gestão de Ativos e Gestão de Contratos avisam quando uma importação termina; a Central de Serviços pede aprovação quando o chamado é aberto com "Precisa de aprovação".

## 19. Como funciona a busca global (Ctrl+K)?

**Em linguagem simples:** aperte **Ctrl+K** (⌘K no Mac) ou clique na barra de busca do topo e digite: aparecem as aplicações e páginas da plataforma que você pode abrir e, **das próprias aplicações**, os registros que batem (um ativo pelo patrimônio, um fornecedor pelo nome, um chamado pelo protocolo, um contrato pelo número). Setas escolhem, Enter abre direto no lugar certo, Esc fecha; as últimas buscas ficam guardadas.

**Como funciona (onda 9):**
- O hub pergunta a **todas as aplicações que você pode abrir ao mesmo tempo** (endpoint `/_hub/search`, pela rede interna, com a sua identidade — cada app só devolve o que você pode ver). Cada app tem no máximo 0,8 s; quem demorar fica de fora daquela vez (a paleta avisa "algumas aplicações não responderam"), e o resto aparece.
- Resultados agrupados por aplicação, os mais relevantes primeiro; nomes de usuários só para quem gerencia usuários, implantações só para quem edita configurações e **nomes** de variáveis (nunca valores) só para quem vê Variáveis.
- Dentro de um app, a paleta busca aplicações e páginas na hora e oferece "Buscar em todas as aplicações" (abre a página de resultados) — a proteção (CSP) não deixa o código de um app consultar a plataforma.
- Buscas sem resultado entram no **Analytics de uso** só como contagem e um código (hash), para saber quantas vezes algo não foi achado sem guardar o que a pessoa digitou; guardar o texto é opcional (liga-se na API do Analytics, com auditoria). Os ids das pessoas no analytics usam uma chave guardada no cofre e trocada a cada período de retenção. Digitar na busca não grava nada; só a busca "confirmada" (Enter/abrir resultado) é registrada, e há limite de buscas por segundo por pessoa.

## 20. A plataforma é acessível? Tem tema escuro e outros idiomas?

**Em linguagem simples:** sim. No menu do seu nome você escolhe o **tema** (claro, escuro ou "Sistema", que segue o computador) e o **idioma** (português, inglês ou espanhol; "Automático" segue o navegador). A escolha fica salva na sua conta — vale em qualquer computador. Tudo funciona **só com o teclado** (Tab, Enter, setas, Esc), com leitor de tela e com "reduzir movimento" do sistema.

**Como funciona (onda 9):**
- **Tema escuro** gerado a partir dos design tokens: cada cor da paleta ganha a versão escura automaticamente (e as cores próprias dos apps também), e um verificador automático (axe) confere o contraste nos dois temas. O topo e o menu lateral continuam iguais nos dois temas (já são escuros).
- **Idiomas**: o hub traduz o shell (topo, menus, Início, Aplicações, Inbox, busca, tour, login) e **avisa o idioma às aplicações** (`x-hub-locale`); os SDKs Rust e Python trazem a tradução e a formatação de números, valores e datas. O app Gestão de Contratos está totalmente traduzido; as telas de Administração e os apps do protótipo seguem em português (pendência registrada).
- **Acessibilidade (WCAG 2.2 AA)**: link "Pular para o conteúdo", regiões nomeadas, foco visível, foco levado ao título quando se troca de seção num app e de volta ao botão quando um menu/diálogo fecha, avisos lidos pelo leitor de tela (Inbox, toasts, reordenação do Início), alvos de clique de pelo menos 24 px e animações desligadas com "reduzir movimento". O axe roda nos testes E2E em todas as páginas principais, nos dois temas e em dois idiomas — zero violações sérias ou críticas.
- **Primeira vez**: um **tour guiado** mostra onde fica cada coisa (só o que você pode usar), e o Início traz "Primeiros passos" que se marcam sozinhos conforme você usa. Dá para pular o tour, parar no meio e continuar depois, ou refazê-lo pelo menu do seu nome.

## 21. Como conectar o SharePoint?

**Em linguagem simples:** a TI cadastra **uma vez**, em **Administração › Conexões**, o acesso a uma biblioteca de documentos do SharePoint da empresa (por exemplo "Documentos" do site "Contratos"). Depois é só **vincular** a conexão às aplicações que podem usá-la (como se faz com as connection strings) e clicar em **Aplicar (reiniciar)**. A partir daí, quando alguém envia um documento num app (um anexo de fornecedor, o PDF de um contrato), o arquivo vai direto para aquela pasta do SharePoint — e a aplicação **nunca recebe a senha**: quem fala com a Microsoft é o hub, em nome do app, e só dentro da pasta que a TI escolheu.

**O que a TI precisa criar na Microsoft (uma vez por conexão):**
1. **Registro de aplicativo no Entra ID** (portal do Azure → Microsoft Entra ID → *Registros de aplicativo* → *Novo registro*). Anote o **ID do diretório (tenant)** e o **ID do aplicativo (client)**.
2. **Permissão de aplicativo `Sites.Selected`** do Microsoft Graph (*Permissões de API* → *Adicionar* → Microsoft Graph → *Permissões de aplicativo* → `Sites.Selected`) e **Conceder consentimento do administrador**. Com `Sites.Selected` o app registrado **não vê nenhum site** até receber acesso explícito — é o recomendado (evite `Sites.ReadWrite.All`, que abre todos os sites do tenant).
3. **Conceder o site** ao app (um administrador do SharePoint/Global faz uma vez): pelo Graph, `POST https://graph.microsoft.com/v1.0/sites/{id-do-site}/permissions` com `{"roles": ["write"], "grantedToIdentities": [{"application": {"id": "<client id>", "displayName": "Hub"}}]}` (Graph Explorer), ou em PowerShell `Grant-PnPAzureADAppSitePermission -AppId <client id> -DisplayName Hub -Site https://empresa.sharepoint.com/sites/Contratos -Permissions Write`.
4. **Credencial** — escolha uma:
   - **Certificado (recomendado)**: gere um par de chaves (ex.: `openssl req -x509 -newkey rsa:2048 -nodes -keyout chave.pem -out cert.pem -days 730 -subj "/CN=hub-sharepoint"` ou `New-SelfSignedCertificate` no Windows), envie **só a parte pública** (`cert.pem`/`.cer`) em *Certificados e segredos → Certificados* do registro, e entregue ao hub o **PEM com a chave** (`cert.pem` + `chave.pem` num arquivo só) ou o **PFX com a senha**. O PFX precisa ser **moderno** (AES-256: padrão do OpenSSL 3 em `openssl pkcs12 -export -inkey chave.pem -in cert.pem -out hub.pfx`, ou "AES256-SHA256" na exportação do Windows); PFX com algoritmos legados (RC2, RC4, DES/3DES — o padrão do OpenSSL 1.x e de exportações antigas do Windows) é recusado com a orientação para reexportar, ou envie o PEM. A senha do PFX não é guardada. A chave privada fica no cofre do hub; a Microsoft só guarda a parte pública. Validade típica: 1–2 anos (a tela avisa 30 dias antes).
   - **Segredo do cliente**: *Certificados e segredos → Segredos do cliente → Novo segredo*; copie o **Valor** na hora (ele não aparece de novo). Vence em até 24 meses — gere outro antes e use **Rotacionar credencial** no hub.

**No hub:** Administração › **Conexões** → **Nova conexão SharePoint** → nome (ex.: `contratos-docs`, é o que o app usa), Tenant ID, Client ID, certificado ou segredo, **URL do site** (`https://empresa.sharepoint.com/sites/Contratos`), biblioteca (vazio = a padrão, "Documentos") e, se quiser, uma **pasta-base** (ex.: `Hub/Contratos` — o app nunca sai dela). Clique **Testar conexão**: o hub pega o token, acha o site e a biblioteca, cria a pasta-base se preciso e **grava, lê e apaga** um arquivinho de teste — cada etapa aparece com ✓ ou com o motivo do erro em português (segredo expirado, client ID errado, "sem permissão no site: a TI precisa conceder o site ao app", biblioteca inexistente…). Depois **Vínculos** → marque app × ambiente → **Aplicar (reiniciar)** o app.

**Como funciona por dentro (onda 10):**
- A credencial fica **cifrada no cofre** do hub e nunca volta para a tela, a API, os logs ou a auditoria (só aparecem "••••••••", a impressão digital e a validade do certificado). "Rotacionar credencial" troca sem mexer nos vínculos.
- O app recebe só `HUB_CONNECTIONS` (quais conexões pode usar), o endereço do **broker** do hub e um **token próprio** (por app × ambiente). O broker só atende esse token vindo da rede daquele app, só nas conexões vinculadas a ele, só nas operações liberadas (enviar, baixar, listar, excluir, mover; **link de compartilhamento desligado por padrão**) e só dentro da pasta-base; cada operação vai para a **auditoria** com app, usuário, conexão e arquivo. Tirar o vínculo corta o acesso na hora.
- Arquivos grandes vão em pedaços (sessão de upload do SharePoint, 5 MB por vez) e os downloads passam em streaming — o hub não guarda o arquivo inteiro na memória. Se a Microsoft pedir para esperar (limitação 429/503), o hub respeita o tempo pedido e tenta de novo; se for longo, o app recebe "tente em instantes" (503) e o navegador repete sozinho.
- O mesmo código do app funciona com o **S3/MinIO** (tipo de conexão "S3") ou com o bucket próprio do app: trocar de destino é configuração. Para testes e demonstração existe um **simulador** do SharePoint (`hub-graphsim`, perfil `sharepoint-sim` do compose) — ele nunca é usado em produção: o hub só fala com ele se `HUB_GRAPH_URL`/`HUB_GRAPH_LOGIN_URL` forem apontadas para lá, e a tela mostra um aviso quando isso acontece.
- **Links de compartilhamento** (operação desligada por padrão): alcance **da organização** por padrão; "qualquer pessoa com o link" só se a conexão escolher esse alcance **e** o app estiver marcado em "Apps que podem gerar links anônimos" (os demais continuam só na organização); links de **edição** só com a caixa marcada e nunca anônimos; validade máxima por conexão (padrão 7 dias, até 30). Se o tenant ignorar a validade pedida, o hub apaga o link e avisa o app — confira a política de links do tenant (SharePoint admin center → Políticas → Compartilhamento).
- **Token do broker**: gerado e guardado pelo hub, um por app × ambiente — ninguém cria um "à mão" (os nomes `HUB_STORAGE_TOKEN`, `HUB_STORAGE_URL`, `HUB_CONNECTIONS` e `HUB_INTERNAL_TOKEN` são reservados em Variáveis). Em Conexões › **Vínculos**, "Rotacionar token" troca o de um app × ambiente na hora (depois, **Aplicar** no app).
- **Auditoria**: toda operação do broker (inclusive falhas, recusas e consultas de metadados) aparece na trilha. O autor é o **usuário** só quando o app repassa a identidade **assinada** pelo hub (`x-hub-user` + `x-hub-user-sig`, o que os SDKs fazem com `for_user`); caso contrário o autor é o app e o usuário aparece como "informado pelo app (não verificado)".
- **Proteções de rede**: com `HUB_BROKER_APP_CIDRS` configurada, app sem rede na lista é recusado; o endpoint de uma conexão S3 não pode apontar para endereços de metadados da nuvem (`169.254.x.x`, `fd00::/8`… — exceção explícita em `HUB_STORAGE_ALLOW_HOSTS`); o hub só envia/baixa arquivos do SharePoint em endereços do Graph, do site ou `*.sharepoint.com` (nuvens soberanas: `HUB_GRAPH_CONTENT_HOSTS`, ex. `.sharepoint.us`); mensagens de erro do provedor não chegam ao app (ficam na auditoria); memória de upload limitada no hub todo (`HUB_STORAGE_BUFFER_MB`, 256).
- **Ainda não testado com um SharePoint real** (só com o simulador, que imita as mesmas chamadas do Graph): antes de produção, rode o "Testar conexão" contra o tenant da empresa (ver PENDENCIAS).

## 22. API e MCP: dá para outros sistemas e assistentes de IA usarem o hub?

**Sim — e de forma controlada.** A partir da onda 11, tudo o que você faz nas telas do hub também pode ser feito por **programas**, e **assistentes de IA** (Claude, entre outros) podem consultar e operar o hub em seu nome.

**API (para sistemas e scripts).** É uma "porta de serviço" do hub, em `/api/v1`, que fala JSON (o formato que os sistemas trocam entre si). Por ela dá para listar os apps, ver a saúde, acompanhar e aprovar implantações, reiniciar containers, rodar backups, reprocessar tarefas, consultar a auditoria, a Inbox e a busca. A documentação fica em **`/api/docs`** (mesma aparência simples, sem nada vindo da internet) e o "contrato" técnico em `/api/v1/openapi.json` (padrão OpenAPI, que as ferramentas de mercado leem sozinhas).
- **Quem pode o quê:** exatamente as **mesmas regras da tela**. A API nunca dá a ninguém mais do que a pessoa já pode fazer clicando.
- **Como um programa se identifica:** com um **token** (uma senha longa, criada em Administração › Tokens de API ou pelo próprio usuário em `/api/me/tokens`). O token **age como a pessoa que o criou** e pode ser limitado: só leitura, leitura e escrita, só alguns apps, com validade. Revelar senhas guardadas (variáveis secretas) exige uma permissão extra no token. Toda alteração feita pela API entra na **auditoria**.
- **Pode ser desligada?** A parte pública (`/api/v1` e a documentação) pode, pelo arquivo de configuração (`[api] enabled = false`). As telas continuam funcionando normalmente (elas usam rotas internas).

**MCP (para assistentes de IA).** MCP (*Model Context Protocol*) é o "idioma" padrão com que assistentes de IA usam ferramentas. O hub tem um servidor MCP em **`/mcp`**, **ligado por padrão**: conectado ao hub com um token (escopo `mcp`), o assistente pode perguntar "quais apps estão com problema?", "o que tem na minha Inbox?", "procure o contrato X", "reprocessa aquela tarefa" — sempre **como você**, vendo só o que você vê.
- **Ferramentas dos apps também:** cada app publica as próprias ferramentas (ex.: `ativos.assets_list`, `contratos.contracts_get`). O assistente **nunca fala direto com o app**: pede ao hub, que confere se você tem acesso e chama o app pela rede interna, em seu nome.
- **Ações perigosas pedem a SUA aprovação, no hub:** implantar, voltar versão, restaurar backup, parar um app, cancelar tarefa e **qualquer alteração de dados nos apps** (criar, editar, excluir). No padrão (`destructive_actions = "approve_in_hub"`), a primeira chamada do assistente só cria um pedido **"Aprovar ação do agente"** na sua **Inbox do hub**, mostrando a ferramenta, os argumentos e o cliente de IA que pediu. Aprovar é numa **página própria** do hub (o botão "Revisar e aprovar" na Inbox), que mostra **todos** os argumentos — nada cortado; pedidos grandes demais nem são criados — e pede **a sua senha de novo** (quem entra por SSO faz um login novo no provedor). Só então a ação acontece. Nem o assistente (o token dele não decide pedidos) nem um programa rodando no seu navegador com a sua sessão conseguem aprovar sem essa confirmação. Se recusar, ou se o pedido vencer (5 min) — ou se o hub reiniciar antes —, nada acontece e o pedido aparece como **"Expirado"**.
  - **Cuidado que continua valendo:** as aplicações rodam no mesmo endereço do hub. Uma aplicação mal-intencionada não consegue aprovar sozinha, mas poderia **imitar** essa tela para tentar pegar a sua senha. Digite a senha só na página `/account/agent-approval/…` que você abriu pela Inbox. Para segurança máxima, a empresa pode exigir login por SSO com chave física/passkey (WebAuthn) — aí a aprovação pede o toque na chave.
  - Alternativa `"confirm"`: a primeira chamada devolve um "código de confirmação" e a segunda, com o código, executa. **Atenção:** aqui a confirmação depende da tela de aprovação do **próprio cliente de IA** (ex.: o Claude pergunta "permitir?") — o modelo consegue reenviar o código sozinho; não há confirmação humana garantida pelo hub. Use só com clientes que pedem aprovação por ferramenta.
  - `"deny"`: essas ações nem aparecem para o assistente.
- **Textos que vêm dos apps não são ordens:** descrições e resultados das ferramentas dos apps chegam ao assistente marcados como *"dado fornecido pelo app — não confiável"* e com tamanho limitado (proteção contra "injeção de instruções" escondidas em dados).
- **Token limitado a alguns apps vale para tudo:** um token restrito ao app A não mexe no app B por nenhum caminho (nem por número de implantação, backup, job, variável ou pacote enviado) — pela API ou pelo MCP.
- **Só com token:** o `/mcp` aceita apenas `Authorization: Bearer` (token com escopo `mcp`); a sessão do navegador não vale ali. Tokens pessoais vencem (padrão 90 dias, máximo 365), cada pessoa tem no máximo 20 ativos, e o limite de uso por minuto é **por pessoa** (vários tokens não multiplicam a cota).
- **API desligada = ferramentas do hub desligadas:** as ferramentas do hub no MCP são a mesma superfície da API; com `[api] enabled = false` elas somem (ficam só as dos apps).
- **Tudo registrado:** cada uso de ferramenta vai para a auditoria (quem, qual ferramenta, resultado).
- **Desligar:** `[mcp] enabled = false` no arquivo de configuração do hub (ou `HUB_MCP_ENABLED=0`). Um app específico pode desligar as próprias ferramentas no manifesto (`mcp.enabled: false`).

**E os apps novos?** Todo app precisa ter a sua API (regra da plataforma, com exceção possível só se a administração permitir). Quem usa os SDKs oficiais (Rust ou Python) ganha a API documentada e as ferramentas de IA quase de graça: as ferramentas são **geradas automaticamente** a partir da documentação da API.

**Como conectar o Claude:** veja `docs/IMPLANTACAO.md` §14 (Claude Desktop, Claude Code e outros clientes).

**E o Langdock (ou outro assistente na nuvem)? Preciso distribuir tokens?** Não (onda 12). O hub fala a versão atual do MCP e é também o **"porteiro" OAuth**: no Langdock basta colar o endereço `https://<hub>/mcp` e escolher OAuth. Na primeira vez, cada pessoa vê a tela de login do hub (ou do SSO) e depois uma tela **"Conectar um aplicativo ao hub"** mostrando quem está pedindo, para onde a resposta volta e o que ele poderá fazer — dá para tirar a permissão de alterar coisas e deixar só leitura. Autorizou, o assistente passa a agir **como aquela pessoa** (mesmas permissões, mesma aprovação na Inbox para ações perigosas). A pessoa vê e revoga os aplicativos em **Apps autorizados** (menu do usuário); a administração vê todos e pode bloquear um cliente em **Administração › Tokens de API**. O acesso emitido só vale para o MCP deste hub (não serve na API nem em outro servidor) e se renova sozinho; se alguém copiar e reutilizar a renovação, o hub percebe, corta o acesso e avisa. Quem preferir continua usando um token pessoal no cabeçalho ("API Key" no Langdock). Passo a passo: `docs/IMPLANTACAO.md` §14.

## 23. Um app pode imitar a tela de login ou de aprovação do hub para roubar a senha? (phishing)

Os apps rodam no **mesmo endereço** do hub (não há um domínio por app), então a defesa é
tirar do app a capacidade de agir como o hub:

- **App não roda JavaScript.** Nas páginas de app, o navegador só aceita os scripts do
  próprio hub (CSP + Trusted Types). Tudo o que o app faria com script — atualizar parte da
  tela, confirmar, abrir modal, filtrar/ordenar tabela, gráficos, abas, copiar, validar
  formulário — ele pede por atributos `data-hub-*`, e quem executa é o hub.
- **O HTML do app é limpo antes de entrar na página:** sem `<script>`, sem `onclick`/
  `onerror`, sem links `javascript:`, sem `<base>`/`<meta>`/`<iframe>`, e **nenhum
  formulário do app consegue enviar para o login, a conta ou o OAuth do hub** (bloqueado
  duas vezes: pelo hub e pela CSP).
- **Telas de senha e de aprovação são sempre do hub**, abertas em página própria (nunca
  dentro de um app), sem script, sem moldura (`frame-ancestors 'none'`) e isoladas de quem
  as abriu (`Cross-Origin-Opener-Policy`). **Regra para os usuários: o hub nunca pede sua
  senha dentro da área de um app.** (A antiga "frase de segurança" foi removida em
  01/10/2026 a pedido do produto: a proteção fica com a CSP, as telas de credencial de topo,
  o COOP e o sanitizador.)
- **Exceção controlada:** se um app realmente precisar de JavaScript, declara o arquivo no
  pacote; o hub calcula a impressão digital (sha384), o pipeline mostra "REVISE: App traz
  JavaScript próprio" e só uma aprovação explícita da administração (separada do deploy e
  auditada) libera — e o navegador recusa o arquivo se ele mudar.

O que ainda resta: o CSS do app pode desenhar um "cartão" falso **dentro da área do app**
(por isso a regra "senha só em tela própria do hub" e o bloqueio de envio para o login), e scripts aprovados rodam
com os poderes da página. Para quem precisa de mais, o caminho é MFA resistente a phishing
(passkeys/WebAuthn) no provedor de SSO. Detalhes técnicos em `platform/docs/CONTRATO-APP.md`
§13, §25 e §26.

## 24. A plataforma fala outros idiomas nas telas de administração? E como um app desfaz uma operação de vários passos que falhou no meio?

**Idiomas (onda 12):** tudo o que o hub mostra — Administração, Operações, Conexões, Inbox, a tela de autorizar um aplicativo, as telas de conta e as mensagens de erro — aparece em português, inglês ou espanhol, conforme a preferência do usuário (menu do usuário) ou, se ele não escolheu, o idioma do navegador. O que foi digitado por pessoas (nomes, descrições, registros de auditoria) aparece como foi gravado.

**Operações de vários passos (sagas):** quando um app precisa, por exemplo, cadastrar um ativo, anexar o documento e avisar o responsável, o SDK executa os passos em ordem e grava o progresso. Se um passo falhar de vez, ele desfaz o que já foi feito, do último para o primeiro. Se o servidor cair no meio, outro processo continua de onde parou. Em Operações › Tarefas aparece em que passo a operação está e o que já foi desfeito.

**Arquivos em duas fases:** o arquivo enviado fica numa área temporária até o app gravar o registro que aponta para ele — os dois confirmam juntos. Se a operação não termina, o arquivo temporário vence e é apagado; nunca fica um arquivo "solto" visível.

