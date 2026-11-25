# Task details

✅ **Clean code (tasks 1–9)**

Finished 2026-10-15 (`c4fb303`). Final list as it stood in TASKS.md:

## Foundation
- ✅ 1 · Foundation: AGENTS.md rules, ruff C901/PLR0912/PLR0915, file-size ratchet

## Hub (platform-py)
- ✅ 2 · Split hub/ops/routes.py into section modules
  - ✅ opsctx.py + routes_{deploys,runtime,backups,pages}.py
  - ✅ full pytest (483 passed)
  - ✅ E2E 06-ops/29 (7/7)
  - ✅ dropped from size-baseline
- ✅ 3 · Route handlers out of the mount closures (module-level router + OpsCtx)
  - ✅ routes_{runtime,deploys,backups,pages}: module routers + Depends(ctx)
  - ✅ ops tests (65 passed)
  - ✅ full pytest (483 passed)
  - ✅ E2E found a stale-cache race in rt.Engine (refresh in flight across a stop); fixed + test
  - ✅ capacity code moved to hub/ops/capacity.py (runtime.py 660 → 572)
  - ✅ re-run: pytest 484 passed, E2E 06-ops/29 7/7
  - ✅ long functions split: deploy_jobs.py, backup_jobs.py, backup_policy.py, runtime_view.py, backups_view.py (HTML identical)
  - ✅ OpsCtx is a class with the helpers; mount_ops only wires (79 lines); pattern in AGENTS.md
  - ✅ pytest 494 passed, E2E 06-ops/29 7/7
- ✅ 4 · Split the other large hub files (oauth.py, app.py, 15 files 500–720) and clear the hub noqa (69)
  - ✅ oauth.py 1168 → oauth_{util,base,clients,authorize,tokens}.py + oauth.py (all < 300); pytest 494, E2E 13 2/2
  - ✅ agent A: app.py 1119 → 142, auth/state/vault/storage/graphsim/insight/connections split; 8 out of the baseline
  - ✅ agent B: api/admin/ops/ux/gateway — all noqa in scope removed, 7 files out of the baseline; pytest 494
  - ✅ hub/filelock.py split
  - ✅ oauth*.py: last 7 functions split, mount_oauth → module router; responses identical
  - ✅ zero size noqa left in the repo; hub-check --all 30/30

## SDK, apps, CLI, JS
- ✅ 5 · SDK Python: jobs.py, storage.py, idempotency.py, dashboards.py + 17 noqa
  - ✅ agent C: 4 files < 500, 17/17 noqa removed, public API unchanged (AST check); SDK 117 passed
  - ✅ dev docs regenerated (6 new SDK sections titled)
  - ✅ DB-backed SDK tests with Postgres: 137 passed
- ✅ 6 · Sample apps: app.py of ativos/coletor/servicos/fornecedores, ativos/importer.py + 21 noqa
  - ✅ agent D: app.py 58–136 lines, routes/ per area, 21/21 noqa removed, OpenAPI identical
- ✅ 7 · CLI and scripts: 9 noqa
  - ✅ agent E: 9/9 noqa removed (CLI 36 passed, scripts 7 passed)
- ✅ 8 · Shell JS: hub-device.js, hub-ux.js, hub-kit.js, hub.js, ops.js
  - ✅ agent F: 5 files split into 15 (all < 500), baseline −5; pytest assets/CSP 173 passed
  - ✅ E2E @core 77 passed — found and fixed: app modals did not open (P.mount missing after the hub-kit split)
  - ✅ E2E 06/29 + Coletor 25/28: 23 passed; ui-sweep --quick: 0 findings

## Closing
- ✅ 9 · Size baseline already empty (no code file > 500 lines). Tighten limits (20 statements, complexity 8), empty baseline, heavy validation, CHANGELOG
  - ✅ limits tightened (complexity 8, 20 statements, 3 nested blocks); 105 noqa marked
  - ✅ agent G: hub ops, admin, gateway, ux, scripts (29)
  - ✅ agent H1: hub api, insight, app/gate/lifespan/routes_login (24)
  - ✅ agent H2: hub connections, auth, vault, storage, graphsim (18)
  - ✅ agent I: SDK + CLI (15)
  - ✅ agent J: sample apps (18)
  - ✅ PLR1702 enforced via explicit preview; 12 nested-block cases refactored (agent K)
  - ✅ bug fixed: auth.json written outside the cross-replica lock (lost update with several replicas)
  - ✅ zero size noqa in the repo; hub-check --all 30/30
  - ✅ heavy validation: E2E 148 passed, ui-sweep full 0 findings, DR/ZD/chaos OK
  - ✅ chaos found: rollout dropped network aliases (502 after hub restart) — fixed + test
  - ✅ DB tests with Postgres: hub 506, SDK 137, apps 42/43/31/29 passed (2 stale tests fixed)
  - ✅ CHANGELOG
  - ✅ owner instance updated (hub_update, zero downtime); healthz/readyz 200, tunnel 302

✅ **Pós-clean-code (tarefas 10–13, 15–17)**

Concluídas em 2026-10-15 (`1d189a8`). Lista final como estava no TASKS.md (pendências abertas seguem no TASKS.md):

## SDK
- ✅ 10 · openapi_of enxerga rotas de include_router
  - ✅ teste red→green; schemas só das rotas /api/
  - ✅ workaround routes.extend removido dos 4 apps; openapi.json idêntico

## Scripts
- ✅ 12 · dash-iso-proof.py: ajuda diz bind, código usa link
  - ✅ ajuda = setup|link|check; comando desconhecido → uso + exit 2 (antes rodava check); teste

## Apps
- ✅ 11 · Links dos apps pela base pública (x-hub-base)
  - ✅ hub_sdk.render.public_base (Renderer.page usa a base do request) + coletor usa o helper
  - ✅ ativos
  - ✅ fornecedores
  - ✅ servicos (inclui _opened)
  - ✅ testes dos 4 apps com Postgres: 43/43/33/33

## Backups
- ✅ 16 · Backups agendados falhavam toda noite desde 07/10 (HUB-WRONG-DB / No such file)
  - ✅ causa: apps em paralelo usavam o mesmo /tmp/hub-role.sql no app-db
  - ✅ tmp_sql(kind): arquivo único por execução (role, admin, restore); teste da corrida
  - ⬜ conferir o próximo backup agendado na :8090
- ✅ 17 · Clone p/ staging do contratos: permission denied to change default privileges (backup #9)
  - ✅ causa: o dump levava os grants da produção (ALTER DEFAULT PRIVILEGES FOR ROLE <papel de prod>); carregar exige ser membro desse papel — falha com admin sem superusuário (ex.: Postgres gerenciado)
  - ✅ pg_dump --no-privileges (o ensure_pg_role refaz os grants do backup a cada execução); teste
  - ✅ reproduzido e corrigido em Postgres descartável; DR na instância isolada OK; backup após restauração OK

## Instância do dono
- ✅ 13 · Deploy dos 4 apps na :8090 (build + up --no-deps, pin keep-20261015)
  - ✅ hub_update (correção do backup) sem parada; 4 apps healthy, readyz 200, status ok
- ▶️ 14 · App de teste do Entra: verificar que nada usa; você apaga no portal
  - ✅ :8090 sem conexões cadastradas (nada depende dele)
  - ⬜ dono apaga no portal do Entra

## Fechamento
- ✅ 15 · E2E dos apps, ui-sweep --quick, CHANGELOG, limpeza
  - ✅ conformidade 4/4; E2E apps 35 passed (07 exige o 06 antes); ui-sweep --quick 0 achados
  - ✅ CHANGELOG; worktrees e instância isolada removidas

✅ **Task 16 — confirmação**

2026-10-15: backup manual dos 4 apps ao mesmo tempo (como o agendador) na :8090, todos "Concluído": ativos 314,3 MB, fornecedores, servicos 529,7 MB, coletor (registros 64–67).

✅ **Task 14 — App de teste do Entra**

2026-10-15: registro "Application Hub - backup SharePoint (teste)" apagado via `az ad app delete` (sem conexões na :8090; restaurável por 30 dias em Aplicativos excluídos). IDs reais de tenant/app nos testes trocados por GUIDs fictícios.

✅ **Melhorias de UX/Design (tarefas 18–28)**

2026-10-16: paginação em /admin/users, /admin/runtime e /admin/backups (18); Desenvolvedores no menu do usuário, abre em nova guia (19); /admin/env com coluna Aplicação e filtro ?app=, botões de banco/token em Containers › detalhes (20); Inbox com select "Mostrar" e paginação (22); ícone do GitHub no header via HUB_SOURCE_URL (23); prompt de copiar aponta para /dev/docs/AGENTS.md (24); sem avisos "Duplicate Operation ID" (25); OAuth do MCP: cliente do registro dinâmico não usado vale 30 dias, validado com mcp-remote em container pela internet (26); botão "Entrar com Microsoft" claro, logo 18px sem contorno (27); travessões removidos dos textos da interface, com guarda test_no_em_dash.py (28); integração, E2E, ui-sweep 0 achados e deploy sem parada na :8090 (21). Pendente do dono: remover os clientes OAuth de teste diag-claude-ai, diag-copilot-cli e "MCP CLI Proxy" em Credenciais › OAuth.

✅ **MCP and manual import (tasks 29–33)**

Finished 2026-10-15 (`b7f845c`). Final list as it stood in TASKS.md:

## Docs
- ✅ 29 · Importação manual mais clara + passo a passo nas docs, testado de ponta a ponta
  - ✅ aba Importar: arquivo → prévia → variáveis/segredos → ambiente → implantar; validador de manifest na aba Pipeline e CI, vazio
  - ✅ prévia do pacote (POST /api/ops/packages/preview): id, nome, versão, novo/atualização, requires (sem implantar)
  - ✅ campos para requires.variables/secrets (vazio = manter; segredo sem eco, nunca devolvido); config também como arquivo
  - ✅ botão diz o efeito: "Implantar em staging" / "Enviar para aprovação (produção)"
  - ✅ guia Publicar + AGENTS, comecar, CONTRATO-APP, FAQ; E2E 31-import-preview e 06-ops; ui-sweep 0 achados

## MCP
- ✅ 30 · Publicar pacote pelo MCP: deployments_upload devolve link de envio de uso único (curl -F package=@…)
  - ✅ /mcp/uploads/<ticket>: 10 min, uso único atômico, dono e permissão conferidos no envio; passa pelo Access
  - ✅ corpo montado sem cópia por pedaço (pacote de 20 MB)
- ✅ 31 · Inventário da API: ~75 ferramentas novas no MCP (destrutivas com confirm_token)
  - ✅ fora de propósito: criar token, trocar senha, chave do cofre, aprovar JavaScript, estado de tela, SSE/CSV
- ✅ 32 · Guia do agente: publicar e operar pelo MCP (mcp-api.md, AGENTS.md); teste exige toda ferramenta no guia

## Fechamento
- ✅ 33 · hub-check, E2E e ui-sweep na instância isolada, deploy na :8090, limpeza
  - ✅ hub-check --all verde (30/30) na main combinada
  - ✅ E2E 06-ops + 31-import-preview 6/6; ui-sweep --quick 0 achados (instância isolada)
  - ✅ push, deploy sem parada na :8090 (readyz 200; /mcp/uploads responde pela internet), worktrees e instância isolada removidas

✅ **Task 45 — lab validation of the corporate-network install**

Lab on 2026-10-19: Rocky Linux 9.4 container with Podman 5.8.2, rootless user `hubsvc`, no systemd, on an internal
network whose only way out is a mitmproxy that inspects TLS with its own CA (the same shape as the corporate server:
curl exit 60 without the CA). `install.sh --doctor` named the proxy, the certificate issuer and `--ca-file`; with
`--proxy`, `--no-proxy`, `--ca-file` and `--data-dir` the full install passed (MinIO built from source, hub and the 4
sample apps, master key, admin, stack up, MinIO and /readyz reached from inside the hub). A new app from the hub-cli
template, imported through the Deployments API, was built by the hub through the proxy with the CA and deployed to
staging (blue/green, readiness ok); its container got the proxy, the hub names in NO_PROXY and SSL_CERT_FILE.

Real failures found and fixed (each with a test): the docker-compose download ignored the proxy and the CA; the
engine's own image pulls did not trust the CA; a template copied from a Windows checkout put CR in .prod.env; the
hub's own Podman socket needed a session of its own (setsid), otherwise conmon failed to attach (the step where the
corporate server stopped); the CA line of the template Dockerfile was split in two; buildah records build args in
the image history, so an authenticated proxy is left out of the build args on Podman. Lab detail only: the build
containers could not resolve the proxy's Docker-only DNS name, so the lab used its IP (a corporate DNS resolves it).

✅ **Corporate-network install (tasks 34–45)**
Cookie Secure following the URL, consent notice, proxy/CA/mirror options, `--doctor`, rootless Podman without systemd
(own socket, cron start), disk choice (`--data-dir`, `--engine-store`), HTTPS at the edge, clearer messages, internal
calls without proxy, app builds/containers with proxy and CA, the corporate-network guide, and the lab validation
(Rocky 9 + Podman rootless behind a TLS-inspecting proxy: 6 real failures fixed with tests).

✅ **Task 51 — validation of the single-Postgres default**
Isolated install (`app-hub-dbt`, port 8097, default `--db-mode single`, sample apps): no `-db` container; the 4 apps
healthy and ready through their own roles (no superuser, the three limits set, owners of their tables); `hub`, `ativos`
and `servicos` closed to PUBLIC; the vault DSNs use each app's role with the production password. `hub-check --all`
30/30 after updating certifi, filelock, opentelemetry-api and virtualenv (new advisories found by `uv audit`).
Removed: the `app-hub-dbt` stack, its volumes and images, the disposable test Postgres, the build cache.

✅ **Tasks 52–55 — one Postgres only (MVP)**
The two-server option was removed: the hub's `hub` database lives in `app-hub-app-db`. `scripts/db-merge.sh` moves an
older install's hub database (tested with real Postgres containers and on an isolated install of the previous version,
`app-hub-mig`: two servers → `config-only` (new secrets) → `build` → `db-merge.sh` → `up -d`; hub ready on the single
server, row counts compared at the merge, the 4 sample apps ready with their own roles, vault DSNs moved off the
superuser). That run found two bugs, fixed with tests: the install guard took the default project instead of the one in
`.prod.env`, and the update steps lacked `dc build`; the merge hint now brings up the whole stack. `hub-check --all` 30/30.
Removed: the `app-hub-mig` stack, its old server, volumes and images, the build cache.

✅ **Task 56 — Lab: user session without its bus**
Rocky 9 lab reproduced the server state (manager active, bus and `systemd/` gone): same aardvark-dns error, `install.sh`
stops with the admin hint. After `systemctl restart user@<uid>.service` the install went green on the first run, a new app
was published from another machine with no server command, the hub survived a reboot, and a `net=host` neighbour started
by cron survived the restart. CPU delegation applied the app CPU limit. Details: docs/TESTE-IMPLANTACAO-RHEL-ROOTLESS.md, round 4.

✅ **Task 57 — Lab: alternatives when the administrator says no**
S2 (bus restored, user units forbidden) and S3 (no restart, bus missing) with `--no-systemd --autostart cron`. Found and
fixed with tests: `hub-start.sh` from cron lacked `~/.local/bin` (no compose after reboot); install builds ran outside the
hub's socket (podman-docker ignores `DOCKER_HOST`) and failed with EOF; aardvark-dns needed the bus through systemd-run.
Both scenarios green after the fix: install, new app published, reboot. Details: TESTE-IMPLANTACAO-RHEL-ROOTLESS.md, round 5.

✅ **Task 58 — user manager disabled by the administrator**
The real server's `user@.service` was replaced by the IT team (`ExecStart=-/bin/true`): no `systemd --user` at all, so
the restart asked for in rounds 4–5 cannot help. Reproduced in the lab with the same unit file: `--no-systemd
--autostart cron` green, new app published, back after a reboot without a bus. The install now detects that unit and
switches to the mode without systemd by itself (test in `tests/test_install_rootless.py`). Record: round 6 of
`docs/TESTE-IMPLANTACAO-RHEL-ROOTLESS.md`.

✅ **Task 59 — corporate network fixes from the first real install**
Found by the agent that installed on a corporate server (rootless Podman, user manager disabled, TLS-inspecting proxy,
internal mirrors). Fixed with tests: the `mc` container of `make_bucket` (and `iso-instance.sh`)
runs with the proxy variables emptied (Podman passes the engine's proxy to every container and `app-hub-objstore` is
not in its `NO_PROXY`); the doctor's proof build uses `podman --cgroup-manager=cgroupfs` when the hub will run its own
socket and that socket does not exist yet; `--ca-file` over 64 KB is refused (it goes to the builds as one build
arg, `EXTRA_CA_PEM`; Linux caps one argument at 128 KB); a mirror failing through the proxy gets the
`--no-proxy <host>` hint; a rerun warns about instance options that differ from `.prod.env`.

✅ **Task 60 — install flags for Entra sign-in and a reverse proxy in front**
What the interactive "advanced options" already wrote to `.prod.env` now also comes from flags (`scripts/install/opts.sh`):
the Entra trio is validated together (same patterns as the questions), the client secret is read from a file
(CRLF-safe) and stored in the vault at step 4c, never in `.prod.env`, the output or `install-cmd.txt` (which keeps the
file path); `--local-login` other than `admins` without Entra is refused (nobody could sign in). `--trusted-proxy`
writes `HUB_TRUST_PROXY_HEADERS`/`HUB_TRUSTED_PROXIES`. The edge (Caddy) already trusts private ranges and the hub
trusts the edge network, so behind a host Nginx the forwarded client address reaches the hub; the doc says how to
check it in the audit trail.

✅ **Task 62 — Containers page on an engine without per-container cgroups**
From the browser test report of the first real install (rootless Podman, cgroupfs, only `memory pids` delegated):
every container showed ~2035 MiB and ~198 processes, above its own limits. A container's cgroup enforces its limits,
so usage above `HostConfig.Memory`/`PidsLimit` proves the engine is reporting another cgroup (`runtime.unmeasured`);
then all usage is dropped (shown as "—") with the reason. `CpuCfsQuota: false` in the engine info adds the warning
that the CPU limit is not enforced. Also: the socket on the host (`HUB_CONTAINER_SOCKET_HOST`, now passed to the hub
by the compose file), image names without `docker.io/library/`/`localhost/` (`runtime.image_label`) and an overcommit
note instead of a negative free share.

✅ **Task 66 — Deploy no Podman rootless e alias DNS**
Sugestão do agente implantador: staging implantada como sucesso e depois 502/`Unstable`. Causa: o candidato nasce
`<nome>__new` e é renomeado; o DNS do Podman (aardvark) guarda o nome de criação, então `http://<nome>:8080` não
resolve. Correção em `hub/ops/rollout.py`: `candidate_aliases` dá ao candidato o nome final como alias em cada rede
(exceto default/bridge/host/none); o primeiro candidato de staging, que copia o contêiner de produção, não herda mais
os aliases de produção (dividiriam o tráfego de produção com staging); `_settle` renomeia, testa a prontidão pelo
nome final e só então troca a rota; se falhar, a rota fica no candidato e a implantação falha com a causa. Testes em
`tests/test_rollout_dns.py`. Não feito: teste de integração real em Podman rootless (não há Podman no CI nem na
máquina de dev); a validação real é reimplantar a staging no servidor.

✅ **Task 67 — Stack dos cards só para admin**
O "i" e a ficha técnica (linguagem, SDK, banco, bibliotecas, API/MCP) aparecem só com `editar_configuracoes`
(`show_stack` em `hub/ux/routes.py`); a versão segue visível em todo card. Teste em `tests/test_ux.py`.

✅ **Task 68 — App só em staging marcado "Unstable"**
O catálogo mostra o estado de produção, e `Insight.targets()` sondava o upstream de produção de todo app do registro.
App novo (dinâmico) implantado só em staging não tem contêiner de produção: a sonda falhava a cada minuto. Agora
`targets()` deixa de fora app dinâmico sem implantação `Implantado` em Produção (`deployed_versions`). Teste em
`tests/test_staging_only_probe.py`. Não mexido: o aviso "SBOM ausente" é correto (o pacote não traz
`sbom.spdx.json`; `hub pack` não gera); o nome "Administrador" após SSO é a tarefa 61.

✅ **Task 69 — SSO não herda admin local**
Regra escolhida pelo dono: papéis vêm do SSO. `AuthStore.sso_login` recebe `local_admin` (conta com senha local e
permissão além de `acessar_aplicacoes`, calculada em `routes_login._local_admin`); essas contas ficam fora da busca
por vínculo e por e-mail, então o SSO cria uma conta própria (papel padrão, nome do Entra). Corrige também uma
instalação já em uso, em que o admin local foi ligado ao SSO antes. O login local com e-mail repetido escolhe a conta
com senha (`_user_login`). Teste em `tests/test_entra.py`.

✅ **Task 70 — App oculto no catálogo**
`settings.catalog_hidden_apps` (Configurações, "Ocultas no catálogo"): o app sai de "Outras aplicações" para quem
não tem acesso; quem tem acesso continua vendo. A busca já mostrava só apps acessíveis. Teste em
`tests/test_catalog_hidden.py`.

✅ **Task 71/72 — Home sem Inbox e sem Atalhos**
Widgets removidos de `WIDGETS`, do template e do CSS; layouts salvos com eles continuam carregando (`known`).
E2E `10-onda9` passou a usar Recentes e Status.

✅ **Task 73 — Mensagem geral do Status**
`status_data` usa o estado dos apps (indisponível, instável, degradado) além dos incidentes abertos.

✅ **Task 74 — Status de 12 meses**
`hub/insight/uptime_daily.py`: a cada `Metrics.save` os buckets de 5 min viram uma linha por dia em
`uptime-daily.json` (400 dias; um dia parcial nunca sobrescreve o completo). `daily_uptime` agrupa os buckets numa
passada só e usa a linha diária quando o dia saiu da janela de 30 dias. `/status?periodo=12m` mostra uma barra por mês.

✅ **Task 75/76 — URL própria de staging e selo no header**
`hub/gateway/env_path.py`: middleware ASGI externo (`StagingPath`) tira o prefixo de `/staging/apps/…` e
`/staging/_frag/…` e marca `scope["hub_env"]`; todas as rotas (página, fragmento, API, WebSocket) seguem iguais e
o ambiente vem do caminho (`Gateway.staging_target`, que exige a permissão de staging). `x-hub-base` e o `Location`
reescrito levam o prefixo. Na página do app, `data-hub-frag`/`data-hub-base` do ambiente; o cookie `hub_env` só
redireciona `/apps/<id>` para a URL de staging, e `/hub-env` leva a página do app para o ambiente escolhido. O JS do
shell (`hub.js`, `hub-kit.js`, `hub-safe.js`, `hub-device.js`) reconhece `/staging/apps/`. Selo "STAGING" no
header (`data-hub-env-badge`). Testes em `tests/test_staging_url.py`; E2E `07-staging` atualizado (precisa de app
com staging implantada: fica para a validação pesada, junto com o teste de carga do gateway).

✅ **Task 82 — Teste de adequação de app existente**

App "Frota" (FastAPI + Jinja, 8 páginas, sidebar, abas, modais, validação, JS próprio) feito por um subagente sem
conhecer o hub; roteiro de 36 verificações (prints desktop/celular/escuro + 12 funções) que vale para o original e para
a versão no hub. Três rodadas de adequação, cada uma por um agente novo só com o texto do Copy prompt:
rodada 1 = 31/36 (URLs achatadas por 404 de links profundos, sidebar sem identidade e com ícones errados, filtro sem
aplicar ao mudar, PRG quebrado, eixo do gráfico cortado); rodada 2 = 33/36 (URLs e identidade mantidas; restavam o
redirect de fragmento que embutia a página do hub, a confirmação sem rótulo próprio, o modal virando cartão, o nome
branco na sidebar clara e o botão de menu do celular sem nome acessível); rodada 3 = 36/36, com tema escuro coerente.
Achado à parte: o ambiente da CLI usava uma cópia do hub de outubro (force-include no build); o hub-check --all agora
reinstala antes de testar.

✅ **Task 94 — Validação da implantação (instalação nova e atualização)**

Clone no scratchpad, projetos isolados (nunca o do dono). Instalação da versão publicada (`a4f9cc9`, projeto
`app-hub-upg`, porta 8097): 3 min 13 s, hub + 4 apps saudáveis. Atualização para o código novo pelo
`docs/ATUALIZACAO.md`, com monitor a cada 250 ms no `/healthz` e em `/_frag/<app>` com sessão:
`install.sh --config-only` 1 s (só segredos novos); `hub_update.py --build` 2 min 08 s **sem nenhuma falha**;
`dc build` 27 s; `dc up -d` 14 s com **hub fora 9,6 s** (o compose recriou o web: rede nova do automacoes e faixa no
broker; o hub_update copia a configuração do contêiner em execução) e **apps fora ~17 s cada** (ativos 17,4 s,
fornecedores 16,7 s, servicos 16,8 s, coletor 17,8 s — recriados pelo compose, sem blue/green). Sessão preservada,
`automacoes` no ar logo após o `up`. Instalação nova da versão nova (`app-hub-new`, porta 8098): 56 s com cache de
camadas, 5 apps saudáveis, 3 funções padrão, 3 pacotes de exemplo, 0,5 CPU por app. Achado corrigido no caminho:
o `install.sh` gravava `HUB_ENTRA_DEFAULT_ROLE=Colaborador` (função que não existe mais nas instalações novas).
Proposta (não feita): `hub_update.py` aplicar a configuração do compose e conectar redes novas sem recriar o hub.


✅ **Task 100 — Sample apps in English (0.4.0): design of the rename and of the migration**

Map: `ativos→inventory` (not `assets`: it collided with the hub's `/assets/` static path and its legacy `/<id>/` redirect), `fornecedores→suppliers`, `servicos→servicedesk`, `coletor→collector`, `automacoes→automations`
(no hyphen: the id is also a Postgres database and role name). In-app routes also go English (e.g. automations:
`catalog`, `runs`, `processes`, `packages`, `triggers`). Screens keep following the user's language.

Where the id lives (inventory): hub `/data` JSON stores (admin.json: variable bindings, managed variable names
`HUB_INTERNAL_TOKEN_<APP>`, `DATABASE_URL_<APP>`, values with db/role names, role `apps`, catalog/status settings,
health/flags/SLOs/incidents; ops.json: deployments, limits, schedules, policies, maintenance, backup secrets keys;
connections.json, inbox.json, ux.json favorites/recents, auth.json token apps, analytics/usage/metrics/uptime), files
under `/data/backups/<app>`, `/data/apps/<app>`, `/data/outbox/<app>.json`; Postgres database = role = id (comment
`hub-managed:demo=<id>`); MinIO buckets (`fornecedores`, `app-<id>`); `.prod.env` keys `APP_HUB_APP_DB_PASSWORD_<APP>`,
`HUB_INTERNAL_TOKEN_<APP>`, `HUB_APP_PORT_<APP>`; compose services/containers/networks/images; job kinds inside each
app database (`ativos.import`, `automacoes.run`…). The audit chain is hash-linked and signed: old events keep the old
ids (history), new events use the new ones.

Migration (one offline command, modelled on `db-merge.sh`, `scripts/rename-sample-apps.sh --project <p>`), run after
`git checkout v0.4.0` and before `dc up -d`; idempotent (a step already done is skipped):
1. backup (`docs/ATUALIZACAO.md` §1); stop the hub and the old sample app containers;
2. Postgres: per pair, terminate connections, `ALTER DATABASE old RENAME TO new`, `ALTER ROLE old RENAME TO new`
   (SCRAM passwords survive a rename), comment `hub-managed:demo=new`; job kinds `old.*` → `new.*`;
3. MinIO: copy buckets `old` → `new` (old kept until removed by hand, for rollback);
4. `.prod.env`: rename the per-app keys, keeping their values;
5. hub data: `python -m hub.admin rename-apps` in the new image over `/data` (every store above, file/dir renames);
6. remove the old containers and networks; `dc up -d` creates the new ones.
Rollback: the same command with `--reverse` (same steps backwards) or the backup.

Validation (real upgrade, isolated project from v0.3.2 with the sample apps and their seed data): databases, roles,
job kinds, bucket, `.prod.env` keys (old values kept), hub variables (resealed under the new names), role apps,
favorites and recents (route segments too) moved; the old session kept working; `/apps/<new>` 200 and
`/apps/<old>` 404; ~1 min 40 s without the hub and the apps. Found and fixed during it: vault values are sealed with
the variable name as AAD (renaming without resealing put the hub in a boot loop), each new app network reuses the
old one's address range (old networks must go first), `.prod.env` new keys created by `--config-only` must take the
old values, and recents/Inbox paths needed the section map. Conformance 5/5, E2E of the apps 52/52, ui-sweep with the
apps up: 0 findings. Not covered: the binary sample packages `platform/samples/*.hubapp` keep the old ids (built by
`platform/scripts/sample-packages.sh` from images; regenerate in the heavy validation).


✅ **Task 101 — "Runtime unavailable" banner in plain words**

`runtime_view.runtime_banner(error)` maps the engine error to the likely cause: socket missing in the hub container
(check `HUB_CONTAINER_SOCKET` and the socket volume), no permission on the socket, connection refused (engine
stopped), timeout. An unknown error keeps a short detail (120 characters). Both languages in the catalog.

✅ **Task 102 — `hub_update.py` applies the rest of the compose without recreating the hub**

After a healthy swap it builds the apps (with `--build`) and runs `up -d --no-deps` on every service of the compose
plus the ones running from another profile (the sample apps), never the hub. `--hub-only` skips it. Found in the real
test: every build got a new image id even fully cached (buildx provenance attestation carries the build time, Docker
Desktop/containerd store), so compose recreated every app; builds now set `BUILDX_NO_DEFAULT_ATTESTATIONS=1` (here
and in `install.sh`). Isolated project from v0.4.0 (`app-hub-upg`), monitor every 250 ms: first update 0 hub
failures (all apps recreated once: images built with the attestation); second update with only the suppliers CPU
limit changed (0.4 → 0.3): recreated only `app-suppliers` and the hub (swap), 0 hub failures, limit applied.

✅ **Task 103 — Sample packages with the English ids**

`platform/scripts/sample-packages.sh` regenerated inventory, suppliers, servicedesk, collector and the two invalid
packages; the old `ativos`/`fornecedores`/`servicos` files removed. Sources at 0.5 vCPU (= the sample apps).
Without local `hub-<id>` images the packages point to deterministic `ghcr.io/org/<id>@sha256:` references (format
valid, the deploy fails at runtime as documented in the script).

✅ **Task 63 — Sample apps from the deployer's browser report**

Buttons with a fake success toast now say "Demonstração: ação desabilitada." (or link to the real create form:
inventory `assets?new=1`, servicedesk `requests?new=1`); inventory create keeps the list filters and answers 303 to
the list with the new asset open (conformance profile `ok: [303]`); dashboard KPIs from the database (inventory,
servicedesk `status_counts()`), the rest labelled illustrative; supplier contract status from the due date;
`hub_sdk.locale.zone()` (`HUB_TZ`, default America/Sao_Paulo, UTC-3 fallback) in collector, automations and the
three `i18n.py`; per-app test that every visible fragment text has an English entry.

✅ **Task 64 — Hub consistency from the same report**

Default language of Settings between the user's preference and the browser (`texts.default_lang`, cached by the
`admin.json` mtime); pt-BR numbers inside catalog templates converted in English; `/status` "desde dd/mm" when the
data starts inside the 30-day window (daily buckets labelled in UTC: they were one day early in GMT-3); Health
services seeded from the registry, apps added later offered once (`health_apps`); boot syncs never mark "restart
pending" (`ConfigOps.track_pending`); example variables only for sample apps present; `display.short` no longer used
on hub screens. The 0.2.0 report items 1–3 and 13 were task 62; 14–15 (a11y of menus, fetch to /apps/*) dropped by
the owner with 61.

✅ **Task 105 — MCP sign-in behind a corporate proxy**

Report from a 0.4.0 server: authorizing GitHub Copilot desktop failed with "cliente inválido: documento do cliente
inacessível: [SSL: CERTIFICATE_VERIFY_FAILED]". The CIMD download pins the vetted IP in the URL (SSRF guard) and sets
`sni_hostname`; httpcore honors `sni_hostname` on direct connections only — through the proxy tunnel the TLS
`server_hostname` is the URL's host, so the certificate was checked against the IP. Reproduced with a local squid:
pinned IP → "IP address mismatch", by name → HTTP 200. Through the proxy the hub now keeps the name (vetting of the
resolved IPs still runs; the proxy resolves again). If the error persists on a network that inspects TLS, the
corporate CA must be installed (`install.sh --ca-file`, check with `--doctor`).

✅ **Task 104 — admin/runtime: x / y, scale and the 5-minute chart**

Table: CPU and memory always "x / y" (stopped or not created = 0; platform rows x / limit, or only x without a
limit). Where the engine reports another cgroup (rootless Podman, no delegation) `container_proc` reads each running
container's /proc through `exec` (two tick readings 0.5 s apart, RSS from statm); without `sh` it stays "—". Host
panel split in two full-width panels: "Capacidade do host" (stacked bars with 25% marks, scale, "x / y · %", legend
with vCPU · GiB per part) and "Últimos 5 minutos" (stacked area per part, top = total, 25% grid, hover per sample).
History kept in the hub process (`host_chart.HISTORY`, one sample per poll, lost on restart). Iterated with the
owner on screen (proposals A/B/C → B1, separate panels, horizontal layout, legend values).

✅ **Task 106 — admin/deploys log in the History side panel**

`deploy_log.keep` appends the job (steps, build lines, final message) to the deployment record (`log`, last 600
lines) after an import and after approve/promote/rollback; the version panel shows it in a scrollable box. The toast
of a failed import/action is short and points to the History item. Pre-existing, not from this change: the E2E
"(c)+(b) JavaScript próprio" fails on v0.4.0 too (servicedesk candidate stuck in "migrações do banco em curso" on the
isolated instance) — to investigate.

✅ **Task 124–127 — Login único do CLI por OAuth e tokens só por usuário (2026-11-25)**
`hub login` usa o fluxo de código de dispositivo (RFC 8628, cliente embutido `hub-cli`, `hub/api/oauth_device.py`): o CLI
imprime link + código, a pessoa aprova em `/oauth/device` (com sessão) e o CLI recebe tokens com os escopos
`mcp api:read api:write app-api deploy` (`deploy` = ações `upload`/`status`), sempre limitados pelo RBAC da pessoa.
Só o token do CLI vale fora de `/mcp`; tokens de clientes MCP de terceiros continuam só em `/mcp`. Sessão: refresh
deslizante de 30 dias, teto de 90 dias desde o login (todas as concessões), rotação com detecção de reuso e 60 s de
tolerância só para o CLI (dois processos renovando juntos). `/admin/tokens` e `/api/admin/tokens` saíram
(`/admin/tokens` → 308 para `/account/access`); todo token tem dono; tokens antigos sem dono vão para quem os criou
(por nome, só se for exatamente uma pessoa; senão revogados) no `bootstrap`. "Revogar todos os acessos" em
`/admin/users` (`POST /api/admin/users/{id}/revoke-access`, MCP `users_revoke_access`). Achado no E2E: um login feito
com o cookie de outra pessoa substitui a sessão dela (proteção contra fixação de sessão, intencional); o teste usa um
contexto com cookies próprios.

✅ **Task 129–133 — Acesso de leitura ao banco por host:porta (2026-11-25)**
Opt-in (`install.sh --db-public-port/--db-allowed-cidrs`, pergunta avançada, e uma vez numa reexecução). O hub escreve
`pg_hba.conf` e o certificado no contêiner do banco via `exec` (`hub/admin/dbaccess_server.py`, a cada 5 min e no
início): de fora só membros de `hub_readers`, `hostssl`, das redes permitidas; papéis de serviço só das sub-redes
Docker; gateways recusados (origem perdida = falha fechada). Contas `ro_<nome>` com grupo `hub_read_<banco>`
(privilégios padrão do dono do banco), `VALID UNTIL`, `default_transaction_read_only`, 60 s por consulta, 5 conexões;
metadados no COMMENT do papel. Validado de verdade: leitura com TLS ok; escrita, sem TLS, banco `hub` e banco não
concedido recusados; porta publicada no Docker Desktop chega como gateway e é recusada (documentado). Compose: porta
pública igual à de depuração quando desligado (o compose deduplica).
