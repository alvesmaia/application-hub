# Platform (Rust + Topcoat)

> Visão geral e links: [README da raiz](../README.md) · Roadmap: [`docs/ROADMAP.md`](../docs/ROADMAP.md) · Pendências: [`docs/PENDENCIAS.md`](../docs/PENDENCIAS.md)

## Stacks suportadas (onda 7)

A plataforma (hub) é Rust + Topcoat. Os **apps** conversam com ela só pelo contrato HTTP de
[`docs/CONTRATO-APP.md`](docs/CONTRATO-APP.md) (fragmentos, `x-hub-user`, `/_hub/*` com token,
idempotência, outbox, tarefas duráveis, desligamento gracioso, CSP, manifesto) e são validados
pela suíte de conformidade [`conformance/`](conformance/) (caixa-preta por HTTP).

| Stack | SDK | App de exemplo | `runtime.stack` |
|---|---|---|---|
| **Rust** (Axum) | `crates/hub-sdk` | `ativos`, `fornecedores`, `servicos` (`demo-apps/`) | `rust` |
| **Python ≥ 3.11** (FastAPI) | `sdk-python/` — pacote `hub_sdk` (Starlette/FastAPI, asyncpg, Jinja2; mesmas tabelas e semântica) | `contratos` (`demo-apps-py/contratos/`) | `python` |
| Outras | — | implemente o contrato e passe na suíte | (omitir) |

Excel: leitura com `calamine` nas duas stacks (Rust: crate `calamine`; Python: `python-calamine`),
escrita `rust_xlsxwriter` / `openpyxl` write-only. Diferenças do SDK Python (semáforo por
processo, aba do Excel em memória nativa, `asyncio.to_thread` para CPU) estão no item 17 do
contrato.

### Criar um app em Rust

1. Crate com Axum; dependência `hub-sdk = { path = "../crates/hub-sdk" }` (tarefas, idempotência,
   outbox, `/_hub/*`, desligamento, semáforo — ver `demo-apps/src/sdk.rs` e `ativos/`).
2. Rotas sob `/<id>`: `GET /<id>` e `/<id>/<seção>` devolvem o **fragmento** do workspace
   (classes do mockup), `/<id>/_skeleton`, `/<id>/assets/{demo.css,app.css}` — **sem JavaScript** (onda 12: comportamentos por `data-hub-*`, `docs/CONTRATO-APP.md` §25).
3. Migrações do app + `hub_sdk::SCHEMA_SQL`; `HUB_DB_URL`, `HUB_INTERNAL_TOKEN`, `PORT` do ambiente.
4. `manifest.json` (`"runtime": {"stack": "rust"}`, `jobs`, `sdk: "1.0"`); Dockerfile multi-stage,
   não-root, raiz somente leitura, `HEALTHCHECK` com o próprio binário.
5. `hub dev --dir <app> --upstream http://127.0.0.1:<porta> --limits -- <binário>`; conformidade
   com um perfil em `conformance/profiles/<id>.json`; `hub pack --image <repo>@sha256:…`.

### Criar um app em Python (FastAPI)

1. `pip install -e sdk-python` (Python ≥ 3.11). No app:
   ```python
   from fastapi import FastAPI
   from hub_sdk.app import HubSdk
   from hub_sdk.render import AppInfo, NavItem, Renderer

   sdk = HubSdk.from_env("meuapp", app_sql=SCHEMA, handlers=[MinhaTarefa()])
   app = FastAPI(lifespan=sdk.lifespan)          # migra, worker de tarefas, SIGTERM
   sdk.install(app)                              # Idempotency-Key, /meuapp/_hub/*, x-app-version
   render = Renderer(AppInfo("meuapp", "Meu App", nav=(NavItem("dashboard", "Dashboard", "dashboard"),)), "templates")
   ```
   Páginas: templates Jinja2 que estendem `workspace.html` do SDK (`render.page(req, "x.html", "secao")`);
   ações que gravam: na transação, `outbox.write(conn, outbox.audit(...))` e
   `idem_ctx(req).mark_committed(conn)`; tarefas: classe com `spec = JobSpec("meuapp.x")`,
   `async def run(ctx)` e `ctx.save_checkpoint(conn, …)` na transação do lote; operações pesadas:
   `sdk.heavy.try_acquire()` → `busy(...)` (429 + `Retry-After`).
2. Rotas e assets como no item 3 do contrato (`contratos/app.py` é o exemplo completo).
3. `manifest.json` com `"runtime": {"stack": "python"}`; `Dockerfile.contratos` é o modelo
   (python:3.12-slim fixada por digest, venv no builder com as dependências de um lock com
   hashes, usuário 10001, `HEALTHCHECK` com `python -m <app> --health`).
   Arquivos no `/scratch`: apague-os no fim da tarefa (`after_final(job, status)` no handler) e
   registre uma limpeza periódica (`HubSdk.from_env(..., cleanup_hooks=[...])`). Downloads
   pesados: `PermitStreamingResponse(gerador, permit, headers=download_headers(...))` (a vaga
   volta mesmo se o cliente desistir). Fases que seguram muita memória numa tarefa:
   `with ctx.memory_phase():` (OOM no processo de tarefas → `failed`, sem repetir).
4. `hub dev --dir demo-apps-py/contratos --upstream http://127.0.0.1:9104 --limits -- .venv/Scripts/python -m contratos`
   (Windows: Job Object, sem administrador) — ou `bash scripts/dev-app.sh contratos`.
5. `ruff`/`mypy --strict`/`pytest` (ver "Onda 7" abaixo) e `bash conformance/run.sh <id>`.

## Demo: apps compostos no shell

```bash
cd platform
./scripts/demo.sh            # PORT=8080 por padrão
# abra http://localhost:8080/applications
```

O script compila e sobe dois processos:

- `hub-demo-apps` — três upstreams independentes: `ativos` (9101), `fornecedores` (9102),
  `servicos` (9103). Cada um serve `GET /{id}/{section}` como **fragmento** HTML (sem
  `<html>/<body>`), `GET /{id}/_skeleton` e `GET /{id}/assets/{demo.css,app.css}` (sem JavaScript desde a onda 12).
  Só confia em `x-hub-user` (Identity base64url JSON), usado para exibir o nome do usuário.
- `hub-web` — shell Topcoat. `/{id}` e `/{id}/{section}` renderizam o shell em modo
  "dentro do app" (rail oculto, header mantido) com a tela de carregamento do mockup (shell e
  assets vêm de `crates/hub-shell`, compartilhado com o `hub dev`); o
  `assets/hub.js` busca o fragmento em `/_frag/{id}/{section}` e troca o conteúdo de
  `#app-root` (loader → skeleton → conteúdo), intercepta links do mesmo app (pushState,
  skeleton, novo fragmento), trata `popstate` e, se o app estiver fora do ar, mostra a
  mensagem amigável (502 do gateway) dentro do shell.
- O gateway fica montado **só** sob `/_frag/{*rest}` (prefixo removido antes dele, que
  continua aplicando strip de `x-hub-*`, hop-by-hop, bloqueio de `%25`/traversal, timeouts e
  502 amigável). Os apps nunca são acessados diretamente pelo navegador.
- Cor, tinta, ícone e descrição de cada app ficam em `display` no `apps/hub-web/apps.json`,
  ao lado do manifesto (a validação de `hub-manifest` não muda).

### Regenerar os fragmentos a partir do mockup

Os fragmentos em `demo-apps/fragments/` e as cópias em `demo-apps/assets/{app2.css,app3.css}`
são gerados (e commitados, para o build Rust não depender de Node) pelas próprias funções de
renderização do mockup (`js/*.js` na raiz), executadas em jsdom:

```bash
cd platform/demo-apps/tools
npm install
npm run generate
```

O gerador converte `data-route` em `<a href>` reais com os ids do registro (`/apps/ativos/assets`),
serializa as configs dos gráficos no atributo `data-hub-charts`, converte as interações do mockup
em atributos `data-hub-*` (`tools/migrate-data-hub.mjs`: toasts, modal por `<template>`, filtros de
tabela e da base de conhecimento, menu móvel) e insere o marcador `{{HUB_USER}}` na sidebar. Quem
remonta os gráficos (TimeSeriesChart, agora `crates/hub-shell/assets/hub-chart.js`) e liga os
comportamentos é o hub (`hub-kit.js`) — os apps não servem JavaScript (onda 12).

## Desenvolvimento local (hub dev)

O dev de um app roda **só o seu app**, sem Docker, sem a plataforma (hub-web,
Administração, Operações) e sem administrador, e o vê exatamente como ficará dentro dela:

```bash
cd platform
./scripts/dev-app.sh ativos        # ou fornecedores | servicos
./scripts/dev-app.sh contratos     # app Python/FastAPI (cria o venv .venv na 1ª vez)
# abra http://localhost:4173/apps/ativos (ou /apps/contratos)
```

O script compila `hub` (crate `hub-cli`) e `hub-demo-apps`, e roda:

```bash
HUB_DEMO_APP=ativos PORT=9101 target/debug/hub dev \
  --dir demo-apps/dev/ativos --upstream http://127.0.0.1:9101 --limits \
  -- target/debug/hub-demo-apps
```

Uso geral (na pasta do app):

```bash
hub dev --dir . --upstream http://localhost:<porta> [--port 4173] [--role admin] [--limits] [-- <comando do app...>]
```

- **Mesmo shell da plataforma**: header, rail, tela de carregamento, `hub.js` (composição de
  fragmentos via `/_frag`) e CSS vêm do crate `crates/hub-shell`, usado também pelo hub-web.
  Os assets ficam embutidos no binário (`include_bytes!`): o `hub` é autocontido. Um teste
  (`apps/hub-web/tests/shell_parity.rs`) garante marcação e assets idênticos nos dois.
- **Rotas**: `/` → `/{id}`; `/{id}` e `/{id}/{seção}` → shell em modo "dentro do app";
  `/assets/*` → assets do shell; `/_frag/{id}/...` → gateway para `--upstream`.
- **Identidade e variáveis**: o gateway descarta todo `x-hub-*` vindo do navegador, envia
  `x-hub-user` com `dev.user` e o papel de `--role`, e só as chaves de `.hub/dev.env`
  declaradas em `manifest.env` (como `x-hub-env-<chave>`). As demais aparecem no banner
  como ignoradas.
- **Apresentação** (opcional): `.hub/dev.json` com os campos de `display` do apps.json
  (`color`, `ink`, `icon`, `description`) e, se quiser, `name` e `brand`. Sem ele, valem
  os padrões.
- **Auditoria**: com um comando após `--`, o `hub dev` gera um `HUB_INTERNAL_TOKEN` para o app
  filho, busca o outbox dele (`/{id}/_hub/outbox`, como o hub) a cada 2 s e imprime os eventos no
  terminal.
- **Processo do app**: com um comando após `--`, o `hub dev` inicia o app e o encerra junto.
  Com `--limits`, aplica `manifest.resources` (`cpu`, `memory`, `pids`) pelo SO e imprime o
  que foi aplicado:

  | SO | Mecanismo | Limita |
  |---|---|---|
  | Windows | Job Object (processo criado suspenso, atribuído ao job, retomado) | memória comprometida do job, CPU (hard cap), processos ativos, `KILL_ON_JOB_CLOSE` — sem admin |
  | Linux | `systemd-run --user --scope` (cgroups v2), se disponível | `MemoryMax` (sem swap), `CPUQuota`, `TasksMax` |
  | Linux sem systemd / macOS | `setrlimit` — "parcial" | memória virtual (`RLIMIT_AS`), processos (`RLIMIT_NPROC`, por usuário); CPU não |

  A cada 2 s o consumo é lido (Windows: memória privada, pico do job, CPU e processos do
  job; Linux: RSS) e o `hub dev` avisa ao passar de 80% de um limite. No Windows, estouro
  de memória faz a alocação falhar (em produção o container sofreria OOM kill) e o
  `hub dev` informa "limite de memória atingido". Quando o app termina, o `hub dev` também
  termina.
- Exemplos de pasta de app: `demo-apps/dev/<id>/` (`manifest.json`, `.hub/dev.env`,
  `.hub/dev.json`). Variáveis do script: `HUB_DEV_PORT`, `HUB_DEV_ROLE`, `APP_PORT`,
  `LIMITS=0` (sem limites).

## Login, sessões e permissões (onda 3)

Toda página, `/_frag/*` e `/api/*` exige login (públicos: `/login`, `/auth/sso/*`,
`/assets/*`, `/healthz`). Uma camada única no `hub-web` (`apps/hub-web/src/auth.rs`) resolve a
sessão ou o token, aplica CSRF e RBAC e só então chama a rota; a lógica fica no crate
`crates/hub-auth`.

**Usuários de demonstração** (`HUB_DEMO_USERS=1` — ligado em `scripts/demo.sh` e no
`docker-compose.yml`; ⚠️ **senhas públicas, só para demonstração — nunca em produção**; o `docker-compose.prod.yml` fixa `HUB_DEMO_USERS=0`). Essas senhas ficam fora da política de senha e são reaplicadas a cada partida; com `HUB_PUBLIC_URL` `https://` não local o hub loga um aviso):

| Login (usuário ou e-mail) | Senha | Função | Vê |
|---|---|---|---|
| `admin` (`admin@demo.local`) | `admin` | Administrador | tudo |
| `operador` (`operador@demo.local`) | `operador` | Tech Lead | apps, Configurações, Variáveis, Logs/Auditoria, Operações, Tokens de API |
| `leitor` (`leitor@demo.local`) | `leitor` | Colaborador | só as aplicações |

`HUB_DEMO_PASSWORD` (opcional): uma senha **única** para os três usuários de demonstração no lugar
das fixas — para ambientes de demonstração expostos (ex.: o dev público pelo túnel). Precisa passar
na política de senha (fora dela é ignorada com aviso) e nunca vai para o log. No modo dev ela vem de
`platform/.dev.env` (fora do git, `env_file` opcional do `docker-compose.dev.yml`); os testes E2E e a
varredura leem a mesma variável (`HUB_DEMO_PASSWORD=… npx playwright test`).

**Primeiro administrador:** se `auth.json` não tem usuários, o hub cria
`HUB_BOOTSTRAP_ADMIN_EMAIL` com `HUB_BOOTSTRAP_ADMIN_PASSWORD`; sem a senha, gera uma aleatória
forte e a grava **uma vez** em `$HUB_DATA_DIR/bootstrap-admin.txt` (0600 em Unix; o log mostra só
o caminho), com troca obrigatória no primeiro acesso (e-mail padrão `admin@hub.local`). O
arquivo é **apagado** (com aviso no log) assim que um Administrador entra com senha definitiva
ou troca a senha provisória. No Windows os modos Unix não se aplicam: proteja a pasta de dados
com ACL (ex.: `icacls <HUB_DATA_DIR> /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F"`).

- **Senhas:** Argon2id (19 MiB, 2 iterações, 1 via), mínimo 10 caracteres combinando letras com
  números ou símbolos. Erro de login sempre "E-mail ou senha incorretos." (conta inexistente,
  desativada ou senha errada respondem igual, com o mesmo custo).
- **Tentativas de login (sem bloqueio rígido da conta):** em 15 min, no máximo 5 falhas por
  IP+conta (`HUB_LOGIN_RATE_ACCOUNT`) e 30 por IP (`HUB_LOGIN_RATE_IP`); a partir de 5 falhas
  seguidas na conta, de qualquer IP (`HUB_LOGIN_MAX_ATTEMPTS`), um **atraso suave** exponencial
  (2 s dobrando, teto 60 s — `HUB_LOGIN_BACKOFF_BASE_SECS`/`_MAX_SECS`) que **não** se aplica aos
  IPs de onde o usuário já entrou (até 5, por 30 dias): quem ataca a conta de outros IPs não
  tranca o dono. Tudo é chaveado pelo texto do e-mail, sem consultar se a conta existe, e a
  recusa por limite também paga um Argon2: conta real, inexistente ou desativada dão os mesmos
  status (401/429), a mesma mensagem e o mesmo custo, de qualquer IP. Estado em memória (zera ao
  reiniciar; chaves limitadas). "Desbloquear" em Usuários limpa a conta em todos os IPs. A troca
  de senha (`/account/password`) tem limite próprio: 5 tentativas por usuário em 15 min.
  Atrás de um proxy reverso todos os clientes têm o IP do proxy (o teto por IP vira global).

- **Sessão:** id opaco de 256 bits no cookie `hub_session` (`HttpOnly`, `SameSite=Lax`,
  `Path=/`; `Secure` com `HUB_COOKIE_SECURE=1`, com `HUB_PUBLIC_URL` https ou — só com
  `HUB_TRUST_PROXY_HEADERS=1`, atrás de proxy — com `X-Forwarded-Proto: https`; nunca inferido
  de cabeçalho do cliente sozinho); no máximo 10 sessões por usuário
  (`HUB_MAX_SESSIONS_PER_USER`, a de uso mais antigo sai) e limpeza das vencidas a cada 10 min, guardado no servidor só como SHA-256 em
  `$HUB_DATA_DIR/auth.json` (sobrevive a reinícios). Expira após 30 min sem uso e 12 h no total;
  o id muda a cada login; cai no logout, na troca/redefinição de senha e ao desativar o usuário.
  Sem sessão: páginas → `303 /login?next=…` (só caminhos locais: `//host`, `https://…`, `/\…`
  viram `/`); `/api/*` e `/_frag/*` → `401` JSON (o `hub.js`/`admin.js`/`ops.js` voltam ao login).
- **CSRF:** métodos não seguros com sessão exigem mesma origem (`Sec-Fetch-Site: same-origin`
  ou `Origin` = `Host`), inclusive o formulário de login e o logout; as APIs continuam exigindo
  `application/json` (e `multipart` + `X-Hub-Request: 1` no envio de pacote).
- **Senha provisória** (usuário criado ou senha redefinida pelo administrador): o usuário só
  acessa `/account/password` até trocar.

**Permissões** — as seis da tela *Funções e permissões* (a matriz vale na hora, sem novo login):

| Permissão | Libera |
|---|---|
| Acessar aplicações | `/{app}`, `/_frag/{app}/…` e o catálogo — limitado às **aplicações permitidas** da função (padrão: todas) |
| Gerenciar usuários | Usuários e Funções e permissões (páginas e APIs) |
| Editar configurações da plataforma | Configurações, Saúde (editar), Implantações, Containers, Tarefas, Backups, SDK, Tokens de API, provisionar banco; ver Variáveis |
| Ver auditoria e logs | Logs, Auditoria, Saúde (ver e "Verificar agora") |
| Gerenciar variáveis de ambiente | Variáveis (criar, editar, excluir, revelar) e "Rotacionar" |
| Aprovar solicitações | repassada aos apps (`aprovar_solicitacoes` no `x-hub-user`) |

Sem a permissão: 403 amigável (página), JSON (API) ou fragmento (gateway); o rail só mostra os
itens liberados e botões que exigem outra permissão somem (`data-requires` +
`data-hub-perms` no `<body>`). O Administrador tem tudo, sempre.

**Administrador = superusuário.** Só quem tem a função Administrador: atribui funções de
administrador (a própria Administrador ou qualquer uma com "Gerenciar usuários"), **altera
Funções e permissões** (quem só tem "Gerenciar usuários" vê, mas não altera), edita, desativa
ou redefine a senha de contas de administrador. Quem não é Administrador só atribui funções
cujas permissões ele mesmo tem. Ninguém muda as próprias funções, se desativa ou redefine a
própria senha pela tela Usuários (use "Alterar senha"), e o último Administrador ativo não
pode ser desativado nem rebaixado.

**Identidade para os apps** — o gateway injeta `x-hub-user` (base64url de JSON, compatível com o
formato anterior `{user, roles}`):

```json
{ "user": "leitor@demo.local", "roles": ["Colaborador"], "id": "u3a1b2c3d4",
  "name": "Leitor", "email": "leitor@demo.local", "permissions": ["acessar_aplicacoes"] }
```

**Credenciais do hub nunca chegam ao app:** o gateway tira do `Cookie` os cookies da plataforma
(`hub_*`, inclusive `hub_session` e `hub_sso_state`; os do app — ex.: `svc_sid` — passam) e
descarta `Authorization`, salvo esquema listado no manifesto em `forward_auth_schemes` (ex.:
`["Basic"]`; tokens `hubt_…` nunca). Na resposta, `Set-Cookie` com nome `hub_*` é descartado
(o app não fixa a sessão de ninguém) e o atributo `Domain` é removido (cookie só do host). A
identidade chega só pelo `x-hub-user`.

`user` é o login (identificador estável); `name` é o nome para exibir ("Conectado como"). A
auditoria de Administração/Operações registra o nome do usuário real (ou `token:<nome>`); eventos
de outbox dos apps mantêm o app e o usuário do evento.

**Tokens de API (CI/CD)** — *Acesso › Tokens de API*: nome, aplicações (vazio = todas),
ambientes (`staging`/`producao`), ações (`upload` = `POST /api/ops/packages`; `status` =
`GET /api/ops/jobs/{id}` e `GET /api/ops/deployments`, filtrados ao app **e** ao ambiente do
token — job fora do escopo responde 404, como um id inexistente) e validade
(1–365 dias). O segredo (`hubt_…`) aparece uma vez; o hub guarda só o SHA-256. Revogar ou vencer
= `401`. Fora do escopo (outro app/ambiente) = `403`. Em qualquer outra rota o Bearer é recusado.

```bash
curl -fsS -H "Authorization: Bearer $HUB_TOKEN" -H "X-Hub-Request: 1" -F package=@app.hubapp \
  "$HUB_URL/api/ops/packages?env=staging&origin=ci&commit=$GITHUB_SHA&wait=true"
```

O `/api/*` continua atrás de `HUB_ADMIN_ALLOWED_CIDRS`. O caminho adotado é o **CI local**
(`scripts/ci-publish.sh` numa máquina da rede corporativa/VPN, dentro dos CIDRs). Se algum dia um
CI hospedado for usado: **runners hospedados do GitHub**
saem por IPs públicos variáveis e levam 403. Opções: runner self-hosted numa rede permitida;
VPN/túnel (WireGuard, Tailscale, Cloudflare Tunnel) até a rede do hub; ou liberar as faixas
`actions` publicadas em `https://api.github.com/meta` (mudam — automatize, e o token continua
obrigatório).

**SSO (OIDC) — implementado e desligado** (`HUB_SSO_ENABLED=false`). Authorization Code + PKCE
(S256), descoberta, `state` (uso único, ligado a um cookie do navegador) e `nonce`; ID token RS256
ou ES256 validado pelo JWKS (`iss`, `aud`/`azp`, `exp`, `iat`). Vincula por **emissor + `sub`**;
no primeiro login, à conta local com o mesmo e-mail **só** se o IdP disser `email_verified: true`
(claim configurável em `HUB_SSO_EMAIL_VERIFIED_CLAIM`; sem ela, o vínculo por e-mail fica
desligado, a menos de `HUB_SSO_TRUST_EMAIL=true` para IdPs corporativos que não a enviam, como
o Entra ID), nunca a uma conta de Administrador, nunca sobrescrevendo um vínculo existente e
nunca criando um segundo usuário com o mesmo e-mail. Testado contra um provedor falso em processo
(`crates/hub-auth/tests/oidc.rs`).

| Variável | Padrão | Uso |
|---|---|---|
| `HUB_SSO_ENABLED` | `false` | mostra "Entrar com SSO" |
| `HUB_SSO_ISSUER` | — | emissor (ex.: `https://sso.corp/realms/hub`) |
| `HUB_SSO_CLIENT_ID` / `HUB_SSO_CLIENT_SECRET` | — | cliente no IdP (segredo opcional: cliente público com PKCE) |
| `HUB_SSO_REDIRECT_URL` | — | `https://hub.corp/auth/sso/callback` |
| `HUB_SSO_SCOPES` | `openid email profile` | |
| `HUB_SSO_ROLE_CLAIM` / `HUB_SSO_ROLE_MAP` | — | claim de grupos e `grupo=Função;outro=Outra` (grupos mapeados definem as funções a cada login) |
| `HUB_SSO_AUTO_PROVISION` / `HUB_SSO_DEFAULT_ROLE` | `false` / — | cria o usuário no 1º login (sem grupo mapeado e sem função padrão = nega) |
| `HUB_SSO_EMAIL_VERIFIED_CLAIM` | `email_verified` | claim de e-mail verificado (só `true` libera o vínculo por e-mail) |
| `HUB_SSO_TRUST_EMAIL` | `false` | confia no e-mail sem a claim de verificação (vínculo por e-mail) |

Outras variáveis: `HUB_SESSION_IDLE_MIN` (30), `HUB_SESSION_ABSOLUTE_HOURS` (12),
`HUB_LOGIN_MAX_ATTEMPTS` (5), `HUB_LOGIN_LOCK_SECS` (60), `HUB_LOGIN_RATE_ACCOUNT` (5),
`HUB_LOGIN_RATE_IP` (30), `HUB_COOKIE_SECURE` (`1`/`0`; padrão: `Secure` se
`X-Forwarded-Proto: https` ou `HUB_PUBLIC_URL` https), `HUB_BOOTSTRAP_ADMIN_EMAIL`,
`HUB_BOOTSTRAP_ADMIN_PASSWORD`, `HUB_DEMO_USERS`.

`hub dev` continua **sem login**: a identidade simulada (`dev.user`, "Desenvolvedor (hub dev)",
papel de `--role`) aparece no header como **Modo desenvolvimento** e chega ao app no mesmo
formato de `x-hub-user`.

## Administração

Área da plataforma em `/admin` (Configurações), `/admin/env` (Variáveis de ambiente), `/admin/roles`, `/admin/logs`,
`/admin/audit` e `/admin/health`, renderizada no servidor dentro do shell com a marcação do
`adminContent()` do mockup. O rail ganhou o submenu expansível "Administração" (aberto nas
páginas `/admin/*`). Os itens de Operações (Implantações, Containers, Backups, SDK) entram em
`OPS_MENU` (`crates/hub-shell/src/shell.rs`) junto com suas páginas.

- **Estado** (`crates/hub-admin`): modelos tipados (configurações, variáveis, funções,
  serviços, auditoria), seed com os dados do mockup, `RwLock` em memória e persistência em
  `$HUB_DATA_DIR/admin.json` (padrão `platform/data/`, git-ignored) com escrita atômica
  (tmp + rename). Apague o arquivo para voltar ao seed.
- **Marca**: o "Nome da plataforma" das Configurações substitui o nome no header; vazio
  volta para `HUB_BRAND_NAME` (ou "Application Hub").
- **Variáveis de ambiente e connection strings** (`/admin/env`): cada variável tem **tipo**
  (`pública`, `secreta`, `connection string`), valor, **vínculos explícitos** app × ambiente
  (Staging/Produção) e, opcionalmente, o nome dentro do container (`inject_as`, ex.:
  `DATABASE_URL_ATIVOS` → `HUB_DB_URL`). Dois vínculos não podem injetar o mesmo nome no mesmo
  (app, ambiente); `HUB_APP_ID`, `PORT`, `HUB_SCRATCH_DIR`, `TMPDIR` e `HUB_INTERNAL_TOKEN` são da
  plataforma. Segredos e connection strings saem **mascarados** em toda listagem, HTML, API e
  auditoria (a auditoria só leva nome, tipo e vínculos); revelar é auditado. Abaixo da tabela, um
  card por app mostra o que o container recebe (públicas com valor, segredos só pelo nome, `[S]`/`[P]`
  = ambientes), **"Reinício pendente"** quando uma variável vinculada mudou depois da criação do
  container e as ações **Provisionar banco** e **Aplicar (reiniciar)**. O formato antigo
  (`scope` + `environment` + `secret`) é convertido na carga do `admin.json`.
- **Geridas pela plataforma**: o **token interno** de cada app (`HUB_INTERNAL_TOKEN_<APP>`, secreto,
  injetado como `HUB_INTERNAL_TOKEN` nos dois ambientes; gerado com 32 bytes aleatórios se faltar,
  ou fixado por `HUB_INTERNAL_TOKEN_<APP>` no ambiente do hub), a connection string criada por
  "Provisionar banco" e a **carga inicial** `HUB_APP_ENV_FILE` (no compose,
  `apps/hub-web/app-env.compose.json`, o mesmo ambiente com que os containers do compose nascem —
  um teste confere os dois).
- **Provisionar banco**: com `HUB_DB_ADMIN_URL` (só o hub a tem), cria de forma idempotente um banco
  + usuário dedicados **`app_<id>`** (prefixo obrigatório; nomes reservados como `postgres`,
  `template*`, `pg_*` e o usuário da URL de administração são recusados; um papel/banco que já
  existe só é alterado se tiver a marca `COMMENT … 'hub-managed:app=<id>'` e nenhum privilégio de
  administração — nunca toma um papel criado fora do hub) (`CONNECT` revogado de `PUBLIC`, `CONNECTION LIMIT 40`, como o
  `infra/db/init-apps.sh`), guarda `DATABASE_URL_<APP>` (connection string, injetada como
  `HUB_DB_URL`, staging + produção) e conecta o container do banco (`HUB_DB_CONTAINER`) à rede de
  dependências do app. Se a connection string guardada já funciona, nada muda ("Verificar banco");
  se não, a senha é refeita. **Rotacionar** (token interno ou senha do banco) é a única forma de
  trocar o valor de uma variável gerida pela plataforma: no formulário, tipo, valor e vínculos
  dela são somente leitura. A rotação é auditada (sem o valor) e deixa o app com "reinício
  pendente"; o reinício pendente só é limpo se o container recebeu a versão atual do ambiente
  (uma edição durante o deploy mantém o pendente). Endereço gravado: `HUB_DB_HOST` (padrão `hub-db:5432`). Object store por
  app: Aplicações › Conexões → **"Provisionar armazenamento"** (onda 12, 2.7) cria bucket
  `app-<id>` (staging `stg-<id>`) + usuário do MinIO com política restrita ao bucket + conexão S3 do broker
  ligada ao app × ambiente (o app nunca recebe a chave; idempotente, desfaz em falha parcial;
  desprovisionar pede o nome do bucket e, com objetos, `force` + senha). API
  `storage_provision`/`storage_deprovision`. O `hub-objstore-init` segue criando o de
  `fornecedores` (demo).
- **Prontidão (onda 12, 2.5)**: todo app responde `GET /{id}/_hub/ready` (200/503 com motivo)
  e `/_hub/health`; o blue/green só troca o tráfego com 200 no ready e recusa candidato sem a
  rota (docs/CONTRATO-APP.md §2).
- **Backups com privilégio mínimo (onda 12, 2.6)**: `pg_dump` com o papel só leitura
  `hub_bkp_<banco>` e `mc mirror` com a chave só leitura `bkp-<bucket>` (criados/conferidos a
  cada backup e na partida; senhas no cofre); Backups → "Rotacionar credenciais de backup"
  (`POST /api/ops/backups/credentials/rotate`). Restaurar usa a credencial de administração.
- **Auditoria dos apps (outbox)**: a cada `HUB_OUTBOX_POLL_MS` (2 s) o hub busca
  `GET {upstream}/{id}/_hub/outbox` de cada app com token, pela rede interna (nunca pelo gateway),
  grava cada evento novo na trilha (escopo = o app do registro; autor = o usuário do evento; campo
  `source` = `event_id`) e confirma (`POST …/_hub/outbox/ack`). Ordem: auditoria gravada em disco →
  ids registrados em `$HUB_DATA_DIR/outbox/<app>.json` → confirmação; o registro guarda só os ids
  ainda sem confirmação aceita (compactado a cada ack). Reentregas (ack perdido, hub reiniciado)
  são deduplicadas pelo registro e pelo `source` da trilha. Eventos com `event_id` inválido não
  são gravados nem confirmados (aviso no log). Respostas dos apps lidas com teto de 1 MiB; páginas
  de até 200 eventos. Até 5000 eventos guardados na trilha.
- **Logs**: buffer em memória (1000 linhas) + SSE. Entram requisições reais do gateway
  (`/_frag/*`: método, path, status, duração, app), toda mutação do admin e linhas simuladas
  a cada 2,5 s (`HUB_LOG_SIMULATION=0` desliga).
- **Cliente**: `apps/hub-web/assets/admin.js` (modais, menus de linha, revelar segredo,
  permissões, "Verificar agora", console com "Tempo real"). Após cada mutação a página é
  recarregada; a verdade fica no servidor.

### API (`/api/admin`)

| Método | Rota | Observação |
|---|---|---|
| GET/PUT | `/settings` | `platform_name`, `language` (`pt-BR`/`en`), `timezone` |
| GET/POST | `/env` | `{name, value, kind: publica\|secreta\|connection_string, bindings: [{app, env: staging\|producao}], inject_as?}`; listagem sempre mascarada |
| PUT/DELETE | `/env/{name}` | `value` vazio no PUT mantém o valor atual; muda vínculos/valor → "reinício pendente" nos apps afetados |
| POST | `/env/{name}/reveal` | devolve o valor; segredo revelado gera auditoria |
| GET/POST | `/roles` | |
| PUT/DELETE | `/roles/{name}` | Administrador não pode ser excluído |
| PUT | `/roles/{name}/permissions/{index}` | `{"granted": bool}`; Administrador é fixo |
| GET/POST | `/health` · PUT/DELETE `/health/{name}` | |
| POST | `/health/check` | `GET {upstream}/{id}` de cada app (timeout 2 s) |
| GET | `/audit?scope=platform\|{app}&limit=` | até 5000 |
| GET | `/outbox/{app}` | `{recorded, pending_ack}` da coleta do outbox (verificação/teste de caos) |
| GET | `/logs?app=&minutes=&user=` · `/logs/stream` (SSE) | |

Erros: `{"error": "<mensagem pt-BR>"}` com 400 (validação), 404 ou 415. Toda mutação exige
`Content-Type: application/json` (mitigação básica de CSRF) e grava um evento de auditoria.
Todas as rotas exigem sessão e a permissão da matriz de funções (ver "Login, sessões e
permissões"); o autor dos eventos é o usuário logado. Também:

| Método | Rota | Observação |
|---|---|---|
| PUT | `/roles/{name}/apps` | `{"apps": null | ["ativos", …]}` — aplicações permitidas (null = todas) |
| GET/POST | `/users` | `{email, name, roles, password, must_change_password?}` (senha provisória; hash nunca sai) |
| PUT | `/users/{id}` | `{name?, roles?, active?}`; desativar encerra as sessões; último administrador ativo é protegido |
| POST | `/users/{id}/reset-password` · `/users/{id}/unlock` | senha provisória (troca obrigatória, sessões encerradas) · desbloqueio |
| GET/POST | `/tokens` · POST `/tokens/{id}/revoke` | `{name, apps, envs, actions: [upload, status], expires_days}` → `{token, item}` (segredo só nessa resposta) |

## Operações

Páginas `/admin/deploys` (Implantações), `/admin/runtime` (Containers), `/admin/tasks` (Tarefas),
`/admin/backups` e `/admin/sdk` (SDK e pacotes), no submenu "Administração" do rail (`OPS_MENU`), com a marcação
de `js/ops.js` do mockup (`assets/ops.css` é a cópia de `css/ops.css` + estados reais).
Lógica em `crates/hub-ops`; estado em `$HUB_DATA_DIR/ops.json` (apague para voltar ao seed).
Toda ação grava auditoria (mesma trilha da Administração) e linhas `[ops]` no console de logs.

- **Implantações** — upload de `.hubapp` (até 20 MB) roda o pipeline real, passo a passo
  (a UI consulta o job e anima os passos com o resultado):
  1. *Manifest*: `hub_manifest::parse` + conflito de rotas com o registro;
  2. *Assinatura*: `checksums.txt` confere com cada arquivo; com cosign configurado, verifica a
     assinatura da imagem por digest (ver "Assinatura e scan de vulnerabilidades"), senão "não configurada";
  3. *Segurança*: rejeita `.env`, `*.pem`, `*.key`, `id_rsa`… e padrões de chave (PEM, AWS,
     GitHub, Slack, Google); SBOM ausente é aviso; com scanner configurado (trivy/grype), scan da
     imagem por digest com política `HUB_SCAN_FAIL_ON` (contagem por severidade e principais CVEs no passo);
  4. *Contrato*: `runtime.json` com imagem `nome@sha256:<64 hex>`, `sdk` (se houver) 1.x; tarefas
     duráveis declaradas (`manifest.jobs`) exigem `sdk` e respeitam a política (nome `<id>.<nome>`,
     tempo máximo 1 s–24 h, 1–20 tentativas) — o detalhe do passo lista o contrato;
  5. *Front-end*: só `ui/**` além dos arquivos de raiz, CSS/JS só em `ui/`, sem `<script>`
     inline nem `javascript:`;
  6. *Smoke test*: `GET {upstream}/{id}` se o app já está registrado, senão "pulado";
  7. *Aprovação*: produção → "Aguardando aprovação"; staging → automático.
  O zip é lido em memória (nunca executado nem extraído para disco) com defesa contra zip-slip,
  caminhos absolutos, links, zip bombs (64 MB descompactados, 2000 entradas, taxa ≤ 200:1).
  Pacotes aprovados ficam em `$HUB_DATA_DIR/packages/`; **Promover** copia a mesma versão/imagem
  de staging para produção (aguardando aprovação), **Rollback** reativa a versão anterior
  (instantâneo via `-prev` quando possível). Aprovar/Promover/Rollback respondem **202 + job** na
  hora; a tela acompanha cada passo do blue/green (troca, drenagem) pelo job. Implantações ativas
  em staging têm **Ver em staging**.
- **Containers** — **um card por ambiente** (produção sempre; staging quando o app tem staging),
  especificação endurecida de cada um (`hub_runtime::spec_for_env`/`host_config`:
  limites, read-only, cap-drop ALL, no-new-privileges, rede própria + rede das dependências, volume
  `/scratch`), variáveis vinculadas (nomes; "Reinício pendente" + **Aplicar (reiniciar)**), capacidade do host com
  reservas (`HUB_HOST_CPU`, `HUB_HOST_MEMORY_GIB`, `HUB_RESERVE_{SYSTEM,PLATFORM}_{CPU,MEMORY_GIB}`)
  e admission control na edição de limites (400 se não couber). A capacidade vem do **engine** (`docker info`: NCPU e MemTotal do host) — dentro do container a detecção local veria só o limite do próprio hub; `HUB_HOST_CPU`/`HUB_HOST_MEMORY_GIB` prevalecem. Iniciar/Reiniciar/Parar seguram a trava do app e conferem o dono pelas labels (403 se o container com o nome do app não é dele). Com o daemon acessível
  (`HUB_CONTAINER_SOCKET` ou o padrão do sistema), mostra estado/uso reais dos containers
  `hub-app-*` e habilita Iniciar/Reiniciar/Parar; limites editados são aplicados com
  `docker update`. Sem daemon: banner "Runtime indisponível — exibindo especificação". Cada chamada à
  Engine API (criar, conectar rede, iniciar, parar, inspecionar, `docker update`) tem teto de
  `HUB_DOCKER_TIMEOUT_S` (padrão 30 s): daemon travado vira erro "Docker não respondeu em 30 s" no job.
- **Tarefas** — tarefas duráveis (hub-sdk) de todos os apps, buscadas em `/{id}/_hub/jobs` pela
  rede interna com o token do app: filtros por app e estado, progresso, tentativas, duração, erro;
  **Reprocessar** (falhou / tentativas esgotadas / cancelada — volta à fila mantendo o checkpoint) e
  **Cancelar** (na fila: na hora; em execução: o worker para no próximo checkpoint), auditadas.
  Apps que não respondem aparecem num aviso. Staging (fila e banco próprios) aparece como
  "<app> (staging)" e as ações vão para o container de staging.
- **Backups** — políticas editáveis; "Backup agora" empacota `$HUB_DATA_DIR/apps/<id>/<volume>/`
  (dados de demo criados na primeira execução) em `$HUB_DATA_DIR/backups/<id>/<id>-<ms>.zip`
  com índice sha256 por arquivo e sha256 do arquivo; Restaurar / Clonar p/ staging
  (`apps/<id>__staging/`) / Teste de restore verificam os checksums antes de escrever;
  retenção (manter os N mais recentes) aplicada após cada backup, **por ambiente** ("Backup
  (staging)" usa `apps/<id>__staging/` e `backups/<id>__staging/`; as chaves antigas `<id>-staging` são migradas na partida). A agenda é só registrada.
- **SDK e pacotes** — estrutura do `.hubapp`, exemplo de manifest válido, comandos reais
  (`hub pack`, `hub dev`) e "Validar manifest" (`hub_manifest::parse` + conflito de rotas).

Pacotes de exemplo: `./scripts/sample-packages.sh` gera com o `hub pack` real, em `samples/`,
`ativos-2.5.0`, `fornecedores-1.9.0`, `servicos-3.2.0` (válidos) e dois inválidos
(`invalido-segredo-…`, `invalido-script-inline-…`).

### API (`/api/ops`)

| Método | Rota | Observação |
|---|---|---|
| POST | `/packages?env=staging\|producao&origin=ci&commit=<sha>&wait=true` | multipart (`package`) + header `X-Hub-Request: 1` + sessão ou `Authorization: Bearer` (token com `upload`); 202 + job, ou 201/422 com `wait` |
| GET | `/jobs/{id}` | passos, progresso e resultado |
| GET | `/deployments` · POST `/deployments/{id}/approve\|promote\|rollback` | **202 + job** (passos do blue/green em `steps`, resultado em `result.deployment`); `?wait=true` espera e devolve a implantação (200/422); transição inválida: 400 na hora |
| GET | `/runtime` · PUT `/runtime/{app}/limits` · POST `/runtime/{app}/start\|restart\|stop?env=staging` | 503 sem daemon; `env` padrão produção |
| POST | `/runtime/{app}/apply` | "Aplicar (reiniciar)": recria os containers do app (produção e staging) com a mesma imagem e as variáveis atuais, em blue/green |
| POST | `/provision/{app}/database?env=staging` | banco + usuário + connection string vinculada (idempotente); `env=staging`: banco próprio `app_<id>_staging` |
| GET | `/tasks` · POST `/tasks/{app}/{id}/retry\|cancel?env=staging` | tarefas dos apps (via `/_hub/jobs`), por ambiente |
| GET | `/backups` · PUT `/backups/policies/{app}` · POST `/backups/{app}/run?env=staging` | |
| POST | `/backups/records/{id}/restore\|clone\|test` | 202 + job |
| POST | `/manifest/validate` | corpo = manifest JSON |

CI (qualquer um — inclusive o `scripts/ci-local.sh`/`scripts/ci-publish.sh` rodando numa máquina da
rede do hub; o endpoint não depende de GitHub Actions):

```bash
curl -fsS -H "Authorization: Bearer $HUB_TOKEN" -H "X-Hub-Request: 1" -F package=@app.hubapp \
  "$HUB_URL/api/ops/packages?env=staging&origin=ci&commit=$GITHUB_SHA&wait=true"
```

O envio por CI autentica com **token de API** (`Authorization: Bearer`, ver "Login, sessões e
permissões"). Ainda não há: federação OIDC do GitHub (token sem segredo), agendador de backups nem
destino remoto (a criação de containers a partir das implantações está em "Implantação: staging,
produção e blue/green").

### Assinatura e scan de vulnerabilidades (opcionais)

Sem configuração, os passos *Assinatura* e *Segurança* mostram "não configurado" (checksums, segredos
e SBOM continuam valendo). Para ligar, defina no `hub-web`:

| Variável | Efeito |
|---|---|
| `HUB_COSIGN_PUBKEY` | caminho de um PEM (ou o PEM inline) → `cosign verify --key … <imagem@sha256>` |
| `HUB_COSIGN_IDENTITY` + `HUB_COSIGN_ISSUER` | keyless (Sigstore): `--certificate-identity` / `--certificate-oidc-issuer` (ex.: workflow do GitHub + `https://token.actions.githubusercontent.com`) |
| `HUB_COSIGN_BIN` | binário do cosign (padrão `cosign` no PATH) |
| `HUB_SCANNER=trivy\|grype` | liga o scan da imagem do pacote (por digest) |
| `HUB_SCANNER_BIN` | binário do scanner (padrão `trivy`/`grype` no PATH) |
| `HUB_SCANNER_IMAGE` | modo container: imagem do scanner **por digest** (ex.: `aquasec/trivy@sha256:…`), só se já estiver no daemon ou o registro estiver em `HUB_ALLOWED_REGISTRIES`; roda como `hub-scan-<hash>-<aleatório>` (nome único: scans simultâneos não se atropelam) com o socket do Docker montado para ler a imagem local — o mesmo acesso que o hub-web já tem; prefira o modo binário |
| `HUB_SCAN_FAIL_ON=LOW\|MEDIUM\|HIGH\|CRITICAL` | severidade a partir da qual há achado de política (padrão `CRITICAL`); valor desconhecido (e `HUB_SCANNER`/`HUB_SCAN_MODE` inválidos) **impede a partida** do hub |
| `HUB_SCAN_MODE=block\|warn` | `block` (padrão) reprova o pacote; `warn` só avisa |
| `HUB_SCAN_TIMEOUT_S` | prazo do cosign/scanner (padrão 300) |

A referência da imagem é validada na gramática do Docker (`nome@sha256:<64 hex>`, nunca começando com
`-`) antes de chegar às ferramentas, que recebem `--` antes da imagem. Configurado e indisponível
(binário ausente, assinatura inválida, imagem do scanner sem digest) →
o passo **falha** (em `warn`, o scanner só avisa): a plataforma nunca "passa" sem verificar. O passo
*Segurança* mostra a contagem por severidade (`CRITICAL n · HIGH n · MEDIUM n · LOW n`) e os principais
achados; o job guarda o resumo em `steps[2].scan`. Assinar no CI: `cosign sign --key cosign.key
ghcr.io/org/app@sha256:…` (ou keyless com `id-token: write` no GitHub Actions). A imagem de
exemplo `hub-demo-apps` é local e não assinada — com cosign ligado ela é recusada (esperado).

## Rodando em containers

> **Produção:** nunca o compose de demonstração sozinho — use `docker compose --env-file /etc/application-hub/prod.env -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build` (sem usuários de demonstração, cookies Secure, chave mestra como secret fora do volume, âncora da auditoria em outro disco, credenciais de infraestrutura do `prod.env`; exemplo em `prod.env.example`). Checklist completo: [`../docs/IMPLANTACAO.md`](../docs/IMPLANTACAO.md).

Imagens Linux multi-stage (build dentro do Docker, `rust:1-bookworm` → `debian:bookworm-slim`,
usuário não-root 10001, cache de dependências via BuildKit — sem cross-compilação no host):

- `Dockerfile.hub-web` → `hub-web` (binário + `assets/` + `apps.compose.json`; `HUB_DATA_DIR=/data`
  em volume);
- `Dockerfile.demo-apps` → `hub-demo-apps`: um binário para os três apps. Sem variáveis sobe os três
  (9101-9103, como na demo nativa); com `HUB_DEMO_APP=<id>` (ou `HUB_APP_ID`, que a plataforma injeta)
  sobe só esse app em `PORT` (8080) — um app por container.

```bash
cd platform
docker compose up -d --build        # ou: podman compose up -d --build
# http://localhost:8080/applications · /admin/runtime · /admin/deploys
docker compose down                 # mantém imagens e o volume hub-data (-v apaga os dados)
```

Com Podman (não testado aqui; deve funcionar, pois o hub só usa a Engine API): `podman compose` (ou `podman-compose`) lê o mesmo arquivo; troque o socket montado no
`hub-web` por `/run/podman/podman.sock` (rootless: `$XDG_RUNTIME_DIR/podman/podman.sock`) e rode
`systemctl --user enable --now podman.socket`.

**O que fica isolado** (`docker-compose.yml`; o teste `crates/hub-runtime/tests/compose.rs` garante
que é igual a `hub_runtime::host_config`):

- cada app em `hub-app-<id>` com `cpus`/`mem_limit`/`memswap_limit` (sem swap extra)/`pids_limit` do
  manifesto (padrão 1 vCPU · 1 GiB · 100), `read_only`, `cap_drop: [ALL]`, `no-new-privileges`,
  `restart: unless-stopped`, `cpu_shares: 1024` (o padrão do Docker, o mesmo dos containers que o
  runtime cria), volume próprio `hub-scratch-<id>:/scratch` com `TMPDIR=/scratch` e o ambiente =
  variáveis da plataforma + as vinculadas ao app (carga inicial `app-env.compose.json`);
- o próprio `hub-web` também é limitado: `cpus: 1.0`, `mem_limit`/`memswap_limit: 1g`,
  `pids_limit: 256`, `cpu_shares: 2048` (o dobro de um app: sob disputa de CPU o shell/gateway
  continua respondendo), `read_only` com `tmpfs` em `/tmp` (escrita só em `/data` e `/tmp`),
  `cap_drop: [ALL]` e `no-new-privileges` (o socket do Docker segue montado — trade-off de demo). O
  teste `hub_web_is_limited_hardened_and_wins_cpu_contention` cobre esses valores. Medições em um
  perfil de VM de destino: `loadtest/RESULTADOS.md`;
- duas redes **internas** por app (sem saída para a internet): `hub-app-<id>` (o app e o hub-web) e
  `hub-deps-<id>` (o app e as **suas** dependências: banco, object store, API externa; o hub-web não
  entra). Só o `hub-web` participa de todas as `hub-app-*`, então um app não alcança outro (nem por
  nome nem por IP) e nenhum app tem porta no host — o navegador só fala com o hub em `:8080`
  (rede `hub-edge`). O hub alcança o banco só pela `hub-data` (provisionamento), onde nenhum app entra.
  O teste `compose.rs` garante: rede do app + redes de dependências declaradas, nunca a de outro app;
- o registro usado é `apps.compose.json` (`HUB_APPS_FILE`), com upstreams `http://hub-app-<id>:8080`.
- Health check: `hub-web --health` e `hub-demo-apps --health` (o próprio binário faz o GET; a imagem
  não tem curl) são o `HEALTHCHECK` das imagens e o `healthcheck:` do compose — `docker ps` mostra
  `(healthy)`, inclusive nos containers que o hub cria/substitui.

**Plano de controle só pela borda (`HUB_ADMIN_ALLOWED_CIDRS`).** O app alcança o `hub-web` na rede
dele (é assim que o gateway fala com o app); além do login (onda 3), por defesa em profundidade o
`hub-web` só aceita `/api/*` e as páginas `/admin*` de conexões cujo IP **TCP** (nunca
`X-Forwarded-For`) esteja numa das faixas de `HUB_ADMIN_ALLOWED_CIDRS` (lista separada por vírgulas);
de outras origens responde `403`. Gateway (`/_frag`), `/assets`, páginas dos apps e `/applications`
continuam abertos. Sem a variável, nada é restrito (demo nativa, `hub dev`). No compose as redes têm
sub-redes fixas (`hub-edge` 10.231.0.0/24, `hub-app-ativos`/`fornecedores`/`servicos`
10.231.1-3.0/24) e a lista é `10.231.0.0/24,127.0.0.1/32`: o navegador do host entra pela porta
publicada e chega pelo gateway da `hub-edge` (verificado no Docker Desktop), enquanto um container de
app chega pelo IP da rede dele e leva 403. Se mudar as sub-redes, mude a lista (um teste em
`compose.rs` confere). O gate de rede vale antes do login: um runner de CI fora dessas faixas leva
403 mesmo com token válido (ver "Tokens de API").

**Registros de imagem permitidos (`HUB_ALLOWED_REGISTRIES`).** A imagem de um pacote continua exigindo
digest. Se ela não estiver no daemon, o hub só faz `pull` se o host do registro da referência
(`ghcr.io/org/app@…` → `ghcr.io`; sem host → `docker.io`) estiver em `HUB_ALLOWED_REGISTRIES` (hosts
separados por vírgula, ex.: `ghcr.io,registry.corp:5000`). Sem a variável (ou vazia, como no
compose), nenhum pull: só imagens já presentes localmente podem ser implantadas, e a implantação
falha com o motivo e restaura a versão anterior.

**Implantações concorrentes.** Cada app tem sua trava de implantação (os dois ambientes): ela é
tomada antes de ler a versão ativa (a que será restaurada se o runtime falhar) e só é solta depois
do deploy, do health check e da drenagem. Dois deploys do mesmo app entram em fila; apps diferentes
implantam em paralelo. A chamada HTTP não espera a trava: responde 202 + job.

### Implantação: staging, produção e blue/green (onda 4)

Com `HUB_RUNTIME_DEPLOY=1` (ligado no compose), toda implantação que fica ativa (staging
automático, aprovação, rollback) vira um container real; sem runtime ela é **apenas registrada**.

**Staging e produção isolados.** Cada ambiente tem o seu container: `hub-app-<id>` (produção) e
`hub-app-<id>__staging` (labels `hub.app`, `hub.env`, `hub.role=app`), com volume de trabalho
próprio (`hub-scratch-<id>` / `hub-scratch-<id>__staging`), **as variáveis vinculadas àquele
ambiente** (Aplicações › Variáveis, vínculo app × ambiente; logs e auditoria só com os nomes) e o
seu upstream no registro do gateway (`ops.json`: `upstreams` / `staging_upstreams`, reaplicados ao
iniciar o hub). Os dois ficam na rede do app (`hub-app-<id>`, só o hub os alcança); cada um na **sua**
rede de dependências: `hub-deps-<id>` (produção) e `hub-deps-<id>__staging` (staging, onde só entram
as dependências de staging — o banco de staging).

**Nomes sem colisão.** Todo nome derivado de um id usa o separador `__`, que ids não contêm
(`hub-app-<id>__staging`, `…__next`, `…__prev`, `…__swap`, volumes, redes, backups
`<id>__staging`), e o manifesto recusa ids que terminam em `-next`, `-prev`, `-swap`, `-staging` ou
contêm `--`. Antes de parar, renomear ou remover qualquer container o hub confere as labels
`hub.app`/`hub.env` (container de outro dono com o nome esperado → a ação é recusada e nada muda). Na
partida, nomes/chaves da convenção anterior (`-staging`, `-next`, `-prev`) são migrados pelas labels.

**Staging nunca recebe a credencial do banco de produção.** Provisionar vincula cada banco **só** ao
seu ambiente: `DATABASE_URL_<APP>` só em produção; **"Banco próprio de staging"**
(`POST /api/ops/provision/<id>/database?env=staging`) cria o papel e o banco `app_<id>_staging`
(marca `hub-managed:app=<id>:env=staging`, `REVOKE CONNECT … FROM PUBLIC` + `GRANT CONNECT` só ao
próprio papel), grava `DATABASE_URL_<APP>_STAGING` vinculada só a staging e conecta o `hub-db` a
`hub-deps-<id>__staging` (o banco escuta nas duas redes; o staging só alcança a sua). Uma implantação
em staging de um app que tem banco em produção e **não** tem banco de staging falha no contrato de
variáveis: "Staging sem banco próprio: provisione o banco de staging (Containers → “Banco próprio de
staging”)…"; uma connection string vinculada aos dois ambientes também é recusada. Na partida, o hub
tira de staging (auditado) as connection strings que também valiam em produção (instalações antigas).
Para isolar também pelo `pg_hba.conf`, restrinja cada papel ao seu banco (`host app_<id>_staging
app_<id>_staging …`) — o hub não edita o `pg_hba`.

**Produção por padrão; staging só explicitamente.** O gateway serve produção. Quem tem "Editar
configurações da plataforma" liga o staging de um app **na sessão**: "Ver em staging"
(Implantações/Containers, ou o link `/<id>?hub_env=staging`) abre uma **confirmação**, e só o botão
dela — um `POST /account/env?app=<id>&env=staging` same-origin, como navegação de página com gesto
do usuário (`Sec-Fetch-Mode: navigate` + `Sec-Fetch-User: ?1`) — liga o modo staging. A escolha fica
**no servidor**, por sessão, por até 30 min (`HUB_STAGING_VIEW_TTL_S`); não há cookie de ambiente, um
link de outro site ou um `fetch` não troca nada, e sair da sessão volta tudo para produção. A página
do app mostra a faixa **STAGING** (sempre visível) com o botão "Voltar para produção" (outro POST).
**Enquanto a sessão vê algum app em staging, mutações do plano de controle (`/api/*`) são
bloqueadas** ("Saia do modo staging para administrar"): o código de staging roda na origem do hub e
não consegue, por exemplo, aprovar uma implantação de produção com a sessão do administrador. O
gateway confere a permissão a cada requisição (sem ela → 403), responde 404 "Staging indisponível" se
o app não tem staging (nunca cai em produção em silêncio), repassa `x-hub-env: staging|producao` ao
app e marca `[staging]` no log do gateway. A auditoria dos apps (outbox) é coletada por ambiente:
eventos de staging entram com título `[staging] …` e origem `staging:<event_id>`. Saúde ("Verificar
agora") sonda staging à parte (`staging` na resposta, fora do histórico de produção). Tokens de API
continuam restritos por ambiente (upload, jobs e lista de implantações).

**Blue/green sem indisponibilidade** (`hub_runtime::deploy::activate`):

1. o candidato `hub-app-<id>[__staging]__next` sobe **ao lado** do atual (mesma imagem por digest do
   pacote, limites, endurecimento, variáveis do ambiente; herda os labels do atual, inclusive os do
   compose) — o atual continua atendendo;
2. health check do candidato (`GET /<id>` = 200 em até `HUB_RUNTIME_HEALTH_TIMEOUT_S`, 30 s; a
   primeira resposta também aquece o app). Falhou — inclusive um candidato em laço de reinício pela
   restart policy — → o candidato é parado e removido, **nada muda para o usuário**, a implantação
   vira **Falhou** e a versão anterior volta a ficar ativa no registro;
3. **troca atômica** do upstream no registro do gateway para o IP do candidato na rede do app (não
   muda ao renomear). O gateway escolhe o upstream e conta a requisição "em curso" sob a mesma trava
   (`Registry::checkout`), então depois da troca nenhuma requisição nova vai para o anterior;
4. **drenagem**: espera as requisições em curso no anterior (contadas até o fim do corpo da resposta
   — downloads e uploads em streaming terminam no container antigo) chegarem a zero, até
   `HUB_DRAIN_TIMEOUT_S` (60 s; estourar vira aviso no job e no registro);
5. o anterior é parado (SIGTERM com prazo: desligamento gracioso do hub-sdk) e guardado como
   `<nome>__prev` (um `__prev` mais antigo é removido — só se for do app e nenhum upstream nem
   requisição em curso o usa); o candidato é renomeado para `<nome>` e o upstream volta ao nome
   estável `http://<nome>:8080` (sobrevive a reinícios do container e do hub). **Upstream por IP nunca
   é gravado.** Se parar, renomear ou remover falhar, a implantação **falha e compensa**: os nomes
   voltam, o anterior volta a rodar, o tráfego volta ao nome estável e o candidato é drenado e
   removido (a versão anterior continua atendendo); se nem a compensação completar, a reconciliação
   da partida resolve.

**Durável e reconciliado.** A implantação ativada fica **Implantando** (a anterior continua
**Implantado**) até o runtime confirmar; o estado da ativação (`deploy_jobs` em `ops.json`: passos,
`em_andamento`/`concluido`/`falhou`/`interrompido`) é gravado antes de mexer no runtime e a cada
passo, e o job continua consultável em `GET /api/ops/jobs/{id}` depois de um reinício. Ao iniciar, o
hub reconcilia cada app/ambiente pelas labels dos containers: se o nome estável já é a versão nova,
conclui; senão restaura o anterior (nomes, `start`), remove o `__next` órfão, marca a implantação
como **Falhou** ("Interrompida: o hub reiniciou…") e aponta o upstream para o nome estável.

**Rollback** reativa a versão anterior: se o `__prev` é exatamente essa versão (label `hub.image`)
**e** nasceu com as variáveis atuais (label `hub.env_version`), a volta é **instantânea** (inicia o
`__prev` parado, health check, troca, drenagem; sem pull nem criação) e o atual vira o novo `__prev`
(desfazer também é instantâneo); senão (janela encerrada, ou variáveis/segredos mudaram) a versão é
implantada de novo com as variáveis atuais. Um `__prev` com variáveis antigas (segredo ou senha do
banco rotacionados, variável editada, "Aplicar") é **removido** — logo após a rotação/aplicação e numa
verificação a cada minuto —, então nenhum container parado guarda um valor revogado. **Aplicar
(reiniciar)** recria os containers do app (produção e staging) com a mesma imagem e as variáveis
atuais, também em blue/green, **cada ambiente de forma independente** (uma falha em staging não
desfaz produção; a auditoria registra o resultado de cada ambiente); o "reinício pendente" só é limpo
se todos receberam a versão atual das variáveis.

**Restart policy × blue/green.** `restart: unless-stopped` continua valendo para o container em
serviço; o `__prev` fica parado (a policy não o religa) e um candidato que cai em laço nunca recebe
tráfego. O backoff crescente de reinício é a onda 5.

Prova sob carga (3 implantações de produção com **imagens diferentes** + 1 rollback + 1 staging a
200 RPS, com gravações POST com `Idempotency-Key` e exportação CSV longa atravessando as trocas; cada
resposta traz `x-app-version` e o resultado confere a virada limpa):
`loadtest/results/<data>/zero-downtime.md` e `loadtest/RESULTADOS.md`
(`node loadtest/zero-downtime.mjs`). E2E das correções (staging, falha injetada na
renomeação, hub morto no meio de uma implantação): `node loadtest/e2e/w4-fixes.mjs staging|fault|kill`
(a fase `fault` exige o hub-web com `HUB_FAULT_INJECT=1 HUB_FAULT_RENAME_TO=__prev` — só para testes).

**Páginas e fragmentos com cabeçalhos de segurança.** Toda resposta do hub leva
`Content-Security-Policy` (scripts só de `/assets/` do hub e, numa página de app, de
`/_frag/<id>/assets/`; **nenhum script inline**; `connect-src` de uma página de app só o gateway do
próprio app; `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: same-origin`, `Cross-Origin-Opener-Policy: same-origin`; respostas de app/fragmento
levam `Vary: Cookie` e cache privado (`no-store` em staging). A CSP do hub sobrepõe a de um app.

Limpeza: `docker compose down` remove `hub-app-<id>` e o `__prev` (herdam os labels do compose); o
container de staging não é do compose — remova com
`docker rm -f hub-app-<id>__staging hub-app-<id>__staging__prev` (e o volume
`hub-scratch-<id>__staging` e a rede `hub-deps-<id>__staging`) antes, senão a rede `hub-app-<id>` fica
em uso. O `tests-e2e/tests/global-teardown.ts` já faz essa limpeza (containers e redes
`hub-{app,deps}-<id>__staging*`) ao fim da suíte E2E (06-ops/07-staging deixam
`hub-app-fornecedores__staging` + `hub-deps-fornecedores__staging` no ar); rodando a suíte fora do
`npm run e2e` (ex.: um teste isolado que falha antes do teardown), use o `docker rm -f`/`docker
network rm` acima manualmente.

**Digest local.** Os pacotes de `samples/` apontam para `hub-demo-apps@sha256:<Image ID>` da imagem
local (`./scripts/sample-packages.sh` lê `docker image inspect -f '{{.Id}}' hub-demo-apps`; rode de
novo após reconstruir a imagem, pois o ID muda). Um build local não tem *RepoDigest* (ele só existe
depois de push/pull); por isso o hub resolve nessa ordem: `nome@sha256:…` como RepoDigest →
`sha256:…` como Image ID de uma imagem local com tag do mesmo repositório → `pull` (só de registros em `HUB_ALLOWED_REGISTRIES`). (Com o image
store do containerd, o Docker Desktop também lista `nome@<Image ID>` em RepoDigests.)

**Socket do Docker (trade-off só de demo).** O compose monta `/var/run/docker.sock` em
leitura/escrita no `hub-web` (e adiciona o grupo 0 ao usuário 10001 para abri-lo) para a página
Containers mostrar estado/uso reais e o deploy criar containers. Quem controla esse socket controla o
host (pode subir containers privilegiados, montar `/`), então uma falha no hub vira comprometimento do
host. Em produção: proxy de socket com allowlist de endpoints, Podman rootless, ou um agente de
runtime separado com credenciais próprias. Sem o volume, o hub funciona e mostra "Runtime indisponível".

Teste com o daemon real (ignorado por padrão, não constrói imagens):

```bash
docker build -f Dockerfile.demo-apps -t hub-demo-apps .
DOCKER_AVAILABLE=1 cargo test -p hub-runtime --test docker -- --ignored
```

## Rotinas reais dos apps e dependências

Os três apps deixaram de ser só fragmentos estáticos (o visual continua o do mockup; as
seções dinâmicas são renderizadas no servidor, com tudo escapado):

| App | Rotinas | Dependências |
|---|---|---|
| **ativos** | `/apps/ativos/assets`: 300 mil ativos (30 colunas), busca/filtros/ordenação/paginação no servidor (índices trigram e btree); edição com **controle otimista** (`version`; versão velha → 409 com mensagem); **importação .xlsx** (até 50 MB/300 mil linhas, leitura em streaming com `calamine`, validação por linha, `COPY` por lote, tarefa durável `ativos.import` com checkpoint na transação do lote, progresso por polling, relatório CSV de erros); **exportação CSV** (streaming) e **XLSX** (tarefa durável `ativos.export_xlsx`: `rust_xlsxwriter` em memória constante, até `HUB_EXPORT_MAX_ROWS`=1 milhão, download por 6 h) | Postgres |
| **fornecedores** | `/fornecedores/indicators?id=FOR-0284`: anexos PDF/imagem (até 20 MB, extensão+tipo+assinatura conferidos) em **S3** com upload/download em streaming; **ficha em PDF**; **consulta de CNPJ** externa com timeout (1,5 s), 3 tentativas com backoff+jitter e disjuntor (5 falhas → 30 s de contingência com o último resultado conhecido) | MinIO, `hub-extapi` (Postgres opcional: com "Provisionar banco", outbox/idempotência duráveis) |
| **servicos** | `/servicos/requests`: 2 milhões de chamados, paginação por chave, abrir/atualizar (409 com versão velha); `/servicos/reports`: relatório agregado categoria × período (+ CSV); cache de sessão por usuário (LRU, `HUB_SESSION_CAPACITY`) | Postgres |

Comum (via **hub-sdk**, ver abaixo): toda ação que altera dados (e exportações/relatórios) grava um
evento de auditoria no **outbox** do app — na mesma transação da ação quando há banco — que o hub
coleta e grava na trilha com o id do app e o usuário do evento (`/admin/audit`, `/admin/logs`). O
antigo cabeçalho `x-hub-audit` foi removido do gateway. Toda rota que altera dados aceita
`Idempotency-Key` (o `hub.js` manda uma por submissão e repete com a mesma em falha transitória).
Importação e exportação Excel são **tarefas duráveis** (`ativos.import`, `ativos.export_xlsx`,
declaradas no manifesto). Operações pesadas passam por um semáforo por app (`HUB_HEAVY_MAX`,
padrão 2): o excedente recebe **429** com fragmento amigável e `Retry-After` (tarefas esperam na
fila). Numa página (ex.: relatório pesado do `servicos`), o `hub.js` mostra "Relatório em fila…" (rótulo
do app em `data-hub-queued`) e tenta de novo sozinho honrando o `Retry-After` com jitter (até 5 vezes;
esgotadas, o aviso do app).

**Gateway em streaming.** Corpos passam em pedaços nos dois sentidos; timeouts: conexão 5 s,
resposta (`HUB_UPSTREAM_TIMEOUT_SECS`, contado após o envio do corpo; 120 s no compose por causa
do .xlsx) e ociosidade (`HUB_UPSTREAM_IDLE_SECS`, 60 s). Não há teto de duração total: um download
longo que continua fluindo termina; se o app parar no meio, a conexão é abortada. Upload limitado
por app em `manifest.limits.max_upload_mb` (padrão 16, política ≤ 100) → 413 amigável.
`hub.js` intercepta formulários do app (GET vira navegação com query; POST/multipart por XHR com
progresso e troca do alvo `data-hub-target`), faz polling de `data-hub-poll` e deixa links de
download (`/_frag/...`) seguirem normalmente.

**Compose.** Novos serviços: `hub-db` (Postgres 16, 1 GiB, um banco + usuário por app, CONNECT
revogado de PUBLIC; entra só nas redes `hub-deps-ativos`/`hub-deps-servicos` — e na de outro app
quando o hub provisiona um banco para ele), `hub-objstore`
(MinIO, 512 MiB, só na rede `hub-deps-fornecedores`; `hub-objstore-init` cria bucket + usuário com
política restrita), `hub-extapi` (API simulada; `EXTAPI_FAIL_RATE`, `EXTAPI_LATENCY_MS`) e o
one-shot `hub-seed` (`HUB_SEED_SCALE`, padrão 1 = 300 mil ativos + 2 milhões de chamados, ~1,5 min;
idempotente) na rede interna `hub-data`. Os apps migram o esquema sozinhos em segundo plano: o
health check nunca espera banco nem seed. Todas as credenciais do compose são **de demonstração**.

**Demo nativa / `hub dev`.** Suba só as dependências (`docker-compose.deps.yml` publica as
portas no loopback) e aponte os apps por ambiente:

```bash
docker compose -f docker-compose.yml -f docker-compose.deps.yml up -d hub-db hub-objstore hub-objstore-init hub-extapi
HUB_DB_URL_ATIVOS=postgres://ativos:ativos-demo-senha@127.0.0.1:5433/ativos \
HUB_DB_URL_SERVICOS=postgres://servicos:servicos-demo-senha@127.0.0.1:5433/servicos \
HUB_S3_ENDPOINT=http://127.0.0.1:9002 HUB_S3_BUCKET=fornecedores \
HUB_S3_ACCESS_KEY=fornecedores HUB_S3_SECRET_KEY=fornecedores-demo-segredo \
HUB_EXTAPI_URL=http://127.0.0.1:9110 ./scripts/demo.sh
```

(`hub-demo-apps extapi` e `hub-demo-apps seed` também rodam nativos.) Sem as variáveis, cada
seção dinâmica mostra o fragmento do mockup com um aviso. Planilhas de exemplo:
`demo-apps/samples/gerar-planilha.sh [linhas]` (100 mil ≈ 15 MB, 250 mil ≈ 39 MB; só a de 1000
linhas é commitada).

Testes com banco (fora da suíte padrão): `HUB_DB_TEST_URL=postgres://... cargo test -p hub-demo-apps --test db -- --ignored`.

## hub-sdk (`crates/hub-sdk`) — ações que terminam mesmo se o app cair

SDK Rust dos apps (FAQ item 13), usado pelos três apps de demonstração (`demo-apps/src/sdk.rs`).
Tabelas no banco do próprio app (`hub_sdk::SCHEMA_SQL`, migradas junto com as do app):

- **Tarefas duráveis** (`jobs`, tabela `hub_jobs`): `enqueue` (com `Idempotency-Key` opcional: mesma
  chave → mesma tarefa), claim com `FOR UPDATE SKIP LOCKED` + lease (`HUB_JOB_LEASE_S`, padrão 30 s;
  15 s no compose) renovado por heartbeat, novas tentativas com backoff exponencial + jitter até
  `max_attempts`, tempo máximo por tentativa, **checkpoint gravado com o cliente/transação do lote**
  (`JobCtx::save_checkpoint_tx`), cancelamento, estado `dead` (dead-letter), progresso e evento final
  de auditoria no outbox na mesma transação da mudança de estado. Lease vencido (processo morto) é
  retomado por qualquer worker. **Fence por tentativa**: cada claim gera um `attempt_token`;
  heartbeat, checkpoint e fim só valem com ele (uma tentativa antiga, mesmo do mesmo processo,
  nunca grava sobre a nova) e o worker para se não renovar o lease por um lease inteiro. Cancelar
  uma tarefa cujo worker morreu a finaliza na hora. Worker com vagas do semáforo de operações pesadas.
- **Idempotência** (middleware `idempotency::middleware`, tabela `hub_idempotency`, TTL 24 h): (usuário `x-hub-user`, chave,
  rota, hash do pedido) → resposta; repetição → mesma resposta (`idempotent-replay: true`); mesma chave
  em paralelo → espera até 5 s e depois 409; mesma chave com outro pedido → 422. O handler grava a
  marca "efetivado" **na transação de negócio** (`IdemCtx::mark_committed`): se o app cai depois de
  confirmar e antes de responder, a nova tentativa recebe "operação já concluída" sem repetir. Um
  advisory lock de sessão identifica o dono (morre com a conexão numa queda). Pools separados
  (`HUB_DB_POOL` 12, `HUB_IDEM_POOL` 12, `HUB_JOBS_POOL` 5) para uma rajada não travar o heartbeat.
- **Outbox transacional** (`outbox::write` na transação; `Outbox::emit` fora dela), coletado pelo hub.
- **Endpoints internos** `/{id}/_hub/outbox` (GET), `/_hub/outbox/ack`, `/_hub/jobs`,
  `/_hub/jobs/{id}/retry|cancel`: exigem `x-hub-internal-token` = `HUB_INTERNAL_TOKEN` (injetado pela
  plataforma; sem token, 403). O gateway recusa (404) qualquer `/_frag/<app>/_hub/*`, inclusive
  codificado, e descarta `x-hub-*` do navegador.
- **Desligamento gracioso** (`shutdown`): em SIGTERM/Ctrl+C para de assumir tarefas, a tarefa em
  curso devolve o lease no próximo checkpoint (sem gastar tentativa) e o HTTP drena.
- **Limpeza** (`spawn_cleanup`, a cada hora): idempotência vencida e outbox confirmado há mais de 7
  dias. Corpo do pedido: SHA-256 do corpo inteiro (acima de 1 MiB ou chunked, em streaming para um
  arquivo temporário em `$TMPDIR`). Endpoints internos limitam `limit` a 500.
- Sem banco: outbox e idempotência em memória (sem durabilidade) e sem tarefas.

Testes: `cargo test -p hub-sdk` (puros) e, com Postgres,
`HUB_DB_TEST_URL=postgres://… cargo test -p hub-sdk --test db -- --ignored` (claim/lease/retomada,
backoff, checkpoint, cancelamento, dead-letter, idempotência, outbox). Teste de caos com o compose:
`node loadtest/chaos/chaos.mjs` (ver `loadtest/README.md`).


## Onda 5 — backups/DR, resiliência, abas múltiplas e staging separado

### Backups agendados e recuperação de desastre
- **Agenda** por política (Operações › Backups › Editar): cron de 5 campos (`0 2 * * *`, `*/15 * * * *`) ou os rótulos `Diário 02:00`, `A cada 6 h`, `Semanal dom 03:00`, `Manual`; fuso `HUB_BACKUP_TZ` (padrão `America/Sao_Paulo`) ou por política; ambientes (`producao`, `staging`); atraso aleatório (`jitter_s`, padrão 20 s); execução perdida com o hub fora do ar: `executar` (uma vez ao voltar) ou `pular`. Um job por app por vez (o agendado espera o manual). `HUB_BACKUP_TICK_S` (15), `HUB_BACKUP_MISSED_GRACE_S` (300); `HUB_BACKUP_SCHEDULER=0` desliga.
- **Conteúdo**: pacote `HUB_DATA_DIR/backups/<app>/<app>-<ms>/` com `files.zip` (arquivos do app), `db.dump` (`pg_dump -Fc -Z 6` do banco da connection string vinculada), `objects/` (bucket de `HUB_S3_BUCKET`, `mc mirror`) e `manifest.json` (sha256 de cada parte e objeto; o registro guarda o sha256 do manifesto). A retenção apaga pacotes inteiros.
- **Segurança**: `pg_dump`/`pg_restore`/`psql`/`mc` rodam em containers de tarefa descartáveis (`postgres:16-alpine` e `hub-mc`, compilado pelo compose — precisam existir no daemon) só na rede `hub-data`, rootfs somente leitura, `cap_drop ALL`, `no-new-privileges`, uid 10001, montando **só os subdiretórios** que usam do volume do hub (`HUB_BACKUP_DATA_MOUNT=hub-data`, via `volume-subpath` — Docker ≥ 26; caminho do host vira bind do subdiretório): o pacote do backup em curso (escrita só quando é o destino) e o diretório de credenciais da própria tarefa (somente leitura no `pg_*`; o `mc` grava a própria configuração nele). Credenciais de administração (`HUB_DB_ADMIN_URL`, `HUB_S3_ADMIN_URL`) só no hub e, durante a tarefa, em **arquivos 0600** (`pgpass` via `PGPASSFILE`, `config.json` do `mc` via `MC_CONFIG_DIR`) em `HUB_DATA_DIR/.jobsecrets/<aleatório>/` — nunca em variáveis de ambiente (não aparecem no `docker inspect`); apagados ao fim de cada tarefa. Containers de tarefa órfãos (`hub.role=job`, hub caiu no meio) e credenciais que sobraram são removidos na partida e, a cada 10 min, os com mais de 2 h. O fim do log de uma tarefa que falhou tem `usuario:senha@` redigido antes de ir para o registro/alerta. Sem essas variáveis o pacote tem só os arquivos (com nota).
- **Hooks**: `"backup": {"pre": "/<id>/_hub/backup/pre", "post": "/<id>/_hub/backup/post"}` no manifesto → `POST` com `x-hub-internal-token` antes/depois (o post sempre); sem hooks o backup é crash-consistent (indicado no registro).
- **Restaurar** (atômico): confere tudo antes; segura a **trava do app** exclusiva (a mesma de implantação, "Aplicar", Iniciar/Reiniciar/Parar e rotação de segredos; o backup segura a compartilhada — exclui tudo isso, mas deixa a supervisão religar um app que caia no meio do backup, e recheca a manutenção depois de pegá-la) e liga **"Em manutenção (restauração)"** no app × ambiente (durável em `ops.json`; Containers, Saúde e Backups mostram; implantação, aplicar, iniciar/reiniciar, backup e a supervisão ficam bloqueados — 409); para o app (só se estava rodando); tira um **snapshot de segurança** do estado atual pelo mesmo caminho de backup (registro "Pré-restauração", restaurável; o que não existe mais — ex.: banco apagado — vira nota; `HUB_RESTORE_SAFETY_SNAPSHOT=0` desliga); o banco atual é **renomeado de lado** (conexões derrubadas) e o dump restaurado no nome original com `pg_restore --create --clean`, conferido (`SELECT` no banco novo) e só então o de lado é apagado; bucket (`mc mirror --remove`) e arquivos. Falha em qualquer passo → **desfaz**: banco parcial apagado e o original devolvido ao nome, bucket e arquivos de volta ao snapshot; o app volta como estava. Se nem desfazer der certo, o app **fica parado** em "Em manutenção (restauração falhou)" com alerta no sino — nunca volta ao ar com dados pela metade — até um administrador conferir e usar **Liberar** em Backups (`POST /api/ops/maintenance/<id>/clear?env=`). **Hub reiniciado no meio de uma restauração**: na partida, a manutenção "restauração" que sobrou vira "restauração falhou" com alerta no sino e log (Liberar também limpa uma "restauração" sem tarefa ativa). O desfazer do banco é idempotente: o nome do banco de lado é gravado antes do rename e, na volta, o hub confere se ele existe e o devolve ao nome (nunca apaga o original). **Bancos de lado** (`<db>_pre…`) que sobrarem aparecem em Backups → "Bancos de lado" → Verificar (`GET /api/ops/backups/aside`), com "Devolver ao nome" (só se o banco original não existe) e "Apagar" (confirmado, auditado; `POST /api/ops/backups/aside/<nome>/rename-back|drop`) — nunca apagados sozinhos. Identificadores SQL sempre citados (`quote_ident`/`format('%I')`). App parado de propósito pelo operador continua parado depois. Religa e espera o health check (`HUB_RESTORE_HEALTH_TIMEOUT_S`, 90). "Clonar p/ staging" restaura no banco/bucket próprios de staging, se houver. "Teste de restore" confere todos os sha256 e roda `pg_restore --list`.
- **Cópia externa (opcional)**: `HUB_BACKUP_OFFSITE_URL=https://chave:segredo@s3.exemplo.com/bucket/prefixo` (+ `HUB_BACKUP_OFFSITE_NETWORK`, uma rede com saída) — cada pacote é espelhado para `bucket/prefixo/<app>/<pacote>`; falha vira alerta e o backup local continua valendo.
- **Falhas** viram registro "Falhou" em Backups, alerta no sino, auditoria e log.
- **Teste de DR** (compose no ar): `node loadtest/dr.mjs` → `loadtest/results/<data>/dr.md`.

### Reinício com backoff e alerta de OOM
Supervisão de `hub-app-<id>` e `__staging` (eventos do engine + consulta a cada `HUB_SUPERVISOR_POLL_MS`=2000). OOM ou `HUB_CRASH_THRESHOLD` (2) quedas em `HUB_CRASH_WINDOW_S` (600) → **Degradado** com motivo e ação sugerida em Containers e Saúde, restart policy `no` e reinícios pelo hub com espera `HUB_RESTART_BACKOFF_BASE_S` (5) × 2ⁿ até `HUB_RESTART_BACKOFF_CAP_S` (300); estável por `HUB_RECOVERY_STABLE_S` (60) → Disponível e `unless-stopped` de novo. `docker kill` também é religado; saída graciosa (0/143) e "Parar" não. Alertas no **sino** do header (para quem vê a Saúde; leitura por usuário; `GET /api/admin/notifications`, `POST /api/admin/notifications/read`). O usuário final vê "reiniciando" dentro do shell e o hub.js recarrega sozinho quando o app volta. A supervisão identifica o container pelas **labels** (`hub.app`/`hub.env`/`hub.role=app`) e pelo id: um container recriado (implantação, "Aplicar") com o app ainda degradado volta à restart policy `no` (reinícios com o hub) e o app só é declarado recuperado — com o alerta — depois de estável; o estado degradado nunca some sem esse alerta. A supervisão **nunca espera** a trava de um app (implantação longa não para a supervisão dos outros): app com a trava ocupada ou em manutenção fica para a próxima rodada; containers `__next`/`__prev`/`__swap` nunca são supervisionados. E2E: `node loadtest/e2e/w5-oom.mjs`. (limite de 256 MiB pelo caminho da UI — Limites + "Aplicar" —, e redeploy com o app degradado até o alerta de recuperação).

### Abas múltiplas
- **Carregamento**: o loader de ~4 s aparece só na primeira abertura do app sem os assets desta versão (`data-hub-version`) no cache do navegador; deep link (seção/consulta), recarregar/voltar (`performance` navigation type), app já aberto na sessão (`sessionStorage`) ou assets já vistos (`localStorage hub:assets:<app>`) vão direto ao conteúdo.
- **Logs em tempo real**: uma conexão SSE para todas as abas (`assets/logshare.js`: Web Locks elege a aba líder, BroadcastChannel repassa as linhas; failover automático quando ela fecha; cada aba filtra localmente). Testes: `node --test js-tests/` (usa o jsdom de `demo-apps/tools`: `npm ci --prefix demo-apps/tools`).

### Staging em origem separada
`HUB_STAGING_HOST` (ex.: `staging.localhost:8080`; `*.localhost` resolve para 127.0.0.1 nos navegadores atuais). "Ver em staging" → confirmação → `POST /account/staging?app=<id>` (sessão, same-origin, gesto do usuário, permissão "Editar configurações" e o app nas funções) emite um ticket de uso único (`HUB_STAGING_TICKET_TTL_S`, 60 s) e redireciona ao host de staging, que o troca por uma sessão própria (cookie `hub_stg`, host-only, HttpOnly; `HUB_STAGING_SESSION_TTL_S`, 1800) e confere a permissão a cada requisição. **Prova de posse** (o link não pode ser repassado): o host de staging grava um nonce num cookie próprio (`hub_stg_pre`) e manda o navegador confirmar em `<principal>/account/staging/confirm` — o `GET` não muda estado: responde um formulário same-origin com token anti-CSRF de uso único que se envia sozinho (`/assets/autosubmit.js`; sem JavaScript, o botão "Continuar") e só o `POST` confirma —, que só aceita a **mesma sessão principal** que emitiu o ticket (outro navegador, outra sessão ou anônimo → ticket invalidado); só então a volta ao staging com o nonce troca o ticket pela sessão. A sessão `hub_stg` é **amarrada à sessão principal**: sair, trocar a senha, revogar sessões ou desativar o usuário encerra o staging na hora (conferido a cada requisição). E2E: `node loadtest/e2e/w5-fixes-staging.mjs`. O host de staging só serve o app em staging (páginas, `/_frag`, assets): `/api`, `/admin`, login e contas → 404; CSP sem framing e `connect-src` só para o gateway do app na origem de staging. Sem `HUB_STAGING_HOST` não há staging (o modo na origem do hub foi removido; `?hub_env=producao` e `/account/env` antigos continuam compatíveis).

## Onda 6 — testes, CI e varredura de segurança

- **Varredura de UI** (`tests-e2e/ui-sweep/`, regra: toda mudança de UI passa nela): `cd tests-e2e && node ui-sweep/run.mjs [--quick]` com a pilha no ar — rotas descobertas × temas (claro, escuro, SO escuro com hub claro) × papéis (admin, operador, leitor); contraste, shell, jargão, `<select>` vazio, controles escuros no tema claro, menus/modais etc. Relatório em `loadtest/results/<data>/ui-sweep-<hora>/report.md`; sai ≠ 0 com achado fora de `ui-sweep/allowlist.json`. Etapa `ui-sweep` do `scripts/ci-local.sh` (`--standard`: rápida; `--heavy`: completa).
- **E2E no navegador** (`tests-e2e/`, Playwright): `cd tests-e2e && npm ci && npx playwright install chromium && npm run e2e`
  com o compose no ar — roteiro de apresentação (26 cenários: login e permissões dos três papéis, apps,
  Administração, Operações, staging) e várias abas (SSE único com failover, 409 entre abas). Detalhes
  e variáveis em [`tests-e2e/README.md`](tests-e2e/README.md). Diagnóstico usado pelo teste de abas:
  `GET /api/admin/logs/stats` → `{"subscribers": n}` (conexões SSE de logs abertas; permissão de logs).
- **Paridade `hub dev` × produção**: `node tests-e2e/parity/parity.mjs` (shell, fragmentos, assets e um
  formulário comparados com o compose).
- **Suíte**: `bash loadtest/suite.sh` roda E2E, troca sem indisponibilidade (`loadtest/zero-downtime.mjs`),
  DR (`loadtest/dr.mjs`), paridade, OWASP ZAP baseline autenticado e `cargo audit`/`cargo deny`;
  carga realista com rampa até o SLO: `node loadtest/realistic.mjs run --mem 1g --fresh`
  (ver [`loadtest/README.md`](loadtest/README.md) e [`loadtest/RESULTADOS.md`](loadtest/RESULTADOS.md)).
- **CI local — níveis leves** (máquina com pouca memória/disco): `bash scripts/ci-local.sh --standard`
  (padrão do dia a dia: quick + testes com banco + binários Linux num builder persistente
  `hub-builder` → imagens *prebuilt* só com o runtime + pilha leve `docker-compose.lite.yml` — hub-web,
  hub-db, objstore, ativos, seed 5% — + conformidade do ativos + Playwright `@core`);
  `--heavy` (= `--full`, tudo abaixo); `--changed [base]` roda só o afetado pelo diff (`--plan` mostra o
  plano). Binários: `bash scripts/build-linux.sh` (perfil `ci` do Cargo.toml, volumes
  `hub-builder-registry`/`hub-builder-target`, `--stop`/`--reset`); imagens pelo caminho de release:
  `CI_IMAGES=release`. Imagens com rótulo `hub.inputs` não são reconstruídas se as entradas não
  mudaram; MinIO/mc só se a tag não existir. Salvaguardas: trava global (`--wait` espera), memória
  livre/disco/VM do Docker conferidos antes das etapas pesadas (`CI_MIN_FREE_MB`, `CI_MIN_DISK_GB`),
  Ctrl+C derruba a árvore da etapa e a pilha, `target/` > `CI_TARGET_MAX_GB` (25) é limpo. O relatório
  traz pico de memória dos containers, disco e cache de build.
- **CI local** (o GitHub Actions **não é mais usado**; os workflows em `.github/workflows/` ficaram só
  como referência, com gatilho manual): `bash scripts/ci-local.sh --quick` (fmt, clippy, testes Rust,
  JS, Python — ruff/mypy/pytest) e `--full` (+ testes com banco num `hub-testdb` descartável e com
  Docker, `cargo audit`/`cargo deny` em `rust:1-bookworm` com o volume `hub-audit-cache`, imagens em
  sequência, regressão visual no hub nativo (Windows), compose com o perfil `sharepoint-sim`, publicação
  pelo caminho de CI com `scripts/ci-publish.sh` — token de API, `hub pack`, `POST /api/ops/packages`
  com Bearer, aprovação, versão nova no ar —, conformidade 4/4, Playwright completo, DR, troca sem
  indisponibilidade e caos curtos, e a derrubada de tudo). `--only a,b`, `--skip a,b`, `--list`,
  `--fail-fast`, `--keep-up`. Relatório em `loadtest/results/<data>/ci-local-<hora>.md` (logs ao lado,
  fora do git); código ≠ 0 se algo falhar. Hook opcional: `bash scripts/install-git-hooks.sh` (pre-push
  com `--quick`). **Release:** `bash scripts/release.sh <versão> [--dry-run] [--skip-ci] [--prerelease]`
  — roda o `--full`, gera binários (Windows + Linux via Docker), imagens (`docker save | gzip`), SBOMs
  (syft em container), pacotes `.hubapp`, pacote de implantação, `SHA256SUMS` e notas, cria a tag e
  publica com `gh release create`. Artefatos em `platform/dist/v<versão>/`.
- **Cabeçalhos** (após o ZAP): toda resposta do hub leva CSP, `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, COOP, `Cross-Origin-Resource-Policy: same-origin` e `Permissions-Policy`
  (câmera, microfone, localização, pagamento, USB, topics desligados) — inclusive 400/401/403/303 da
  camada de autenticação; HTML/JSON sem `Cache-Control` recebem `no-store`. A página de app libera no
  `connect-src` o gateway do próprio app (`/_frag/<id>` e abaixo); a confirmação "Ver em staging" libera
  a origem de staging em `form-action` (o Chrome aplica a diretiva ao redirecionamento do POST).
- **MinIO sem imagem pública**: as imagens `quay.io/minio/*` não são mais baixáveis sem credencial.
  O compose **compila MinIO e `mc` do código-fonte** (`infra/minio/Dockerfile`, mesmas versões, seções
  `build:`) na primeira subida: `docker compose up -d --build` funciona numa máquina nova sem passo
  manual (alguns minutos na primeira vez). `HUB_MINIO_IMAGE`/`HUB_MC_IMAGE` (só tags) apontam para
  imagens já existentes — `bash scripts/build-minio.sh` gera as duas; o CI as compila à parte e sobe
  com `--no-build`. O `hub-mc` também é a imagem das tarefas de backup.
- **Linux**: o socket do Docker costuma ser `root:docker`; exporte
  `DOCKER_GID=$(stat -c %g /var/run/docker.sock)` para o hub-web entrar nesse grupo (sem isso o runtime
  fica indisponível e as implantações não trocam containers). No Docker Desktop o padrão (0) funciona.

## Onda 7 — stack Python oficial (FastAPI + hub-sdk-python)

- **SDK** `sdk-python/` (pacote `hub_sdk`, Python ≥ 3.11, dependências fixadas: FastAPI,
  Starlette, asyncpg, Jinja2): identidade (`identity_of`), fragmentos Jinja2 com o DOM do mockup
  (`render`), tarefas duráveis (`jobs`: `SKIP LOCKED`, lease + heartbeat, fence por
  `attempt_token`, backoff com jitter, checkpoint na transação, cancelamento, dead-letter,
  retomada), idempotência (`IdempotencyMiddleware`: escopo usuário + rota, hash do corpo inteiro
  em streaming — idêntico ao Rust, 409/422/400, repetição), outbox + `/_hub/*` (token em tempo
  constante, tetos 500/1000), desligamento gracioso (lifespan + SIGTERM), semáforo (429 +
  `Retry-After`), limpeza periódica. Mesmas tabelas do SDK Rust. `HUB_JOBS_MODE=process`
  (padrão da imagem do contratos) roda as tarefas num processo separado do web, supervisionado
  pelo `python -m contratos serve` (ver `hub_sdk/app.py`). Pools por processo dentro do `CONNECTION LIMIT 40` do papel
  do app (web 12 + 12 + 2, tarefas 4 + 5; divididos por `UVICORN_WORKERS`). Carga, caos e
  dimensionamento: `loadtest/RESULTADOS.md` (Onda 7) — recomendação: 1 processo web por vCPU +
  tarefas em processo separado; 512 MiB bastam com uma planilha grande por vez.
- **App `contratos`** (`demo-apps-py/contratos/`): dashboard, lista de ~200 mil contratos (busca,
  filtro, ordenação, paginação no Postgres `app_contratos`), cadastro/edição com controle otimista
  (409), CSV em streaming, XLSX como tarefa, importação Excel como tarefa durável com checkpoint e
  relatório de erros (python-calamine + regras anti zip bomb), auditoria pelo outbox. Imagem
  `hub-contratos` (`Dockerfile.contratos`), serviço `hub-app-contratos` no compose (mesmo
  endurecimento), banco provisionado como o hub faria (`infra/db/contratos.sh`, one-shot
  `hub-db-init`), card no catálogo, pacote de exemplo (`scripts/sample-packages.sh`), blue/green
  pelo caminho de CI (`bash scripts/ci-publish.sh contratos 1.1.x`).

```bash
# testes (a partir de platform/; venv com: pip install -e "sdk-python[dev]" -e "demo-apps-py/contratos[dev]" -r conformance/requirements.txt)
ruff check sdk-python demo-apps-py/contratos conformance && ruff format --check sdk-python demo-apps-py/contratos conformance
(cd sdk-python && mypy src) && (cd demo-apps-py/contratos && mypy src)
(cd sdk-python && pytest) && (cd demo-apps-py/contratos && pytest)
# com Postgres descartável (hub-testdb, ver HANDOFF):
HUB_DB_TEST_URL=postgres://postgres:testpw@127.0.0.1:55499/postgres pytest sdk-python demo-apps-py/contratos
# compose no ar:
bash conformance/run.sh                         # contrato: 4 apps (hub-web parado durante a suíte)
node tests-e2e/parity/parity.mjs --app contratos
node loadtest/chaos/chaos-contratos.mjs          # caos no app Python
node loadtest/realistic.mjs run --mem 1g --apps contratos [--ct-mem 512m] [--ct-workers 2]
```

No CI (`CI=true`) os testes com banco **falham** se `HUB_DB_TEST_URL` não estiver definida
(nunca são pulados em silêncio).

### Atualizar dependências Python (imagem `hub-contratos`)

A imagem instala só o que está em `demo-apps-py/contratos/requirements.lock` (todas as
dependências, inclusive transitivas e o `setuptools` do build, com hashes; `pip install
--require-hashes --no-deps --only-binary=:all:`) e os dois pacotes locais com `--no-deps
--no-build-isolation --no-index`; a base é `python:3.12-slim-bookworm@sha256:…`.

1. Mude as versões em `demo-apps-py/contratos/requirements.in` (e as mesmas nos `pyproject.toml`
   do SDK e do app — são as versões do CI e do `hub dev`).
2. Regere o lock (a partir de `demo-apps-py/contratos/`):
   `uv pip compile requirements.in --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes --no-header -o requirements.lock`
   (ou `pip-compile --generate-hashes` num Linux x86_64 com Python 3.12).
3. Base: `docker buildx imagetools inspect python:3.12-slim-bookworm` → troque o digest no
   `ARG PY_IMAGE` do `Dockerfile.contratos`.
4. `docker build -f Dockerfile.contratos -t hub-contratos .` (o `pip check` do build falha se o
   lock estiver incoerente), rode os testes, a conformidade e o E2E; commite `.in` + `.lock` +
   Dockerfile juntos.

## Onda 8 — design tokens, observabilidade, cofre, SLOs/status, auditoria imutável e feature flags

### Design tokens
`crates/hub-shell/assets/tokens.css` (cor primitiva/semântica, espaço, raio, tipografia, sombras,
camadas, movimento) carregado antes do `styles.css`; `/assets/theme.css` (white label) sobrescreve
`--color-accent` com a **Cor da marca** de Plataforma › Configurações. Guia e regressão visual:
`docs/DESIGN-TOKENS.md` (`tests-e2e/visual`, `npx playwright test -c playwright.visual.config.ts`).

### Observabilidade
- **Operações › Observabilidade** (`/admin/observability`): por app × ambiente, requisições/s, taxa
  de erro 5xx, latência p50/p95/p99, CPU/memória do container, em curso, vagas de operações pesadas,
  fila de tarefas, traces mais lentos e a **cascata** (hub → app → banco).
- O gateway envia `traceparent` (W3C) ao app; os SDKs (`hub_sdk::telemetry` em Rust,
  `hub_sdk.telemetry` em Python) continuam o trace e criam spans de banco com o `traceparent` no
  comentário SQL; o hub busca os spans em `/{id}/_hub/telemetry` (a cada 2 s, `HUB_TELEMETRY_POLL_MS`).
- Métricas em memória (1 h em baldes de 10 s; 30 dias em baldes de 5 min em `$HUB_DATA_DIR/metrics.json`);
  `HUB_TRACE_BUFFER` (padrão 20 000 spans). `HUB_TELEMETRY=0` desliga tudo (interruptor).
- **OTLP opcional**: `OTEL_EXPORTER_OTLP_ENDPOINT` (+ `OTEL_SERVICE_NAME`) exporta traces e
  métricas por OTLP/HTTP (JSON). Perfil do compose com Collector + Jaeger (~450 MiB de teto):

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://hub-otel-collector:4318 docker compose --profile observability up -d
# Jaeger: http://127.0.0.1:16686 · métricas Prometheus do coletor: hub-otel-collector:8889 (rede hub-data)
```

### SLOs e status page
Em **Saúde da plataforma** › "Definir SLO" (disponibilidade %, latência alvo e % dentro dela, janela
7/30 dias) por app × ambiente; a tabela mostra SLI, orçamento de erro restante e taxa de queima.
Queima ≥ 14,4× (1 h e ~10 min) → alerta no sino + incidente automático. **/status** (qualquer
usuário logado; pública e somente leitura com "Status page pública" em Configurações): estado atual,
barras de 30 dias (sondas por minuto) e incidentes (automáticos: queda em laço/OOM, implantação que
falhou, SLO; manuais: "Registrar incidente", notas e resolução — quem tem "Editar configurações").

### Cofre (segredos e backups cifrados)
- Chave mestra: `HUB_MASTER_KEY_FILE` (64 hex; gere com `openssl rand -hex 32`; arquivo 0600 fora do
  volume de dados — no Windows proteja com ACL). Sem ela o hub gera `$HUB_DATA_DIR/master.key` e avisa.
  **Guarde uma cópia fora do servidor**: sem a chave, segredos e backups cifrados não abrem.
- `admin.json`: variáveis secretas/connection strings (inclui o token interno de cada app) cifradas
  (AES-256-GCM, chave de dados embrulhada pela mestra em `keyring.json`, AAD = nome da variável).
  Instalação antiga é migrada na partida (original cifrado em `admin.json.pre-vault-<ms>.enc`).
- Rotação: "Rotacionar chave de dados" (Configurações) ou, com o hub parado,
  `hub-web --rotate-master-key [arquivo-da-chave-nova]` (no compose:
  `docker compose run --rm --no-deps hub-web --rotate-master-key`). O arquivo em uso
  (`HUB_MASTER_KEY_FILE`) é trocado atomicamente pela chave nova — a variável não muda.
- Backups: cada parte do pacote cifrada (`*.hubenc`, `objects.enc/`; cabeçalho autenticado no AAD),
  restauração decifra e confere; cópia externa lida de volta e conferida. `HUB_VAULT=0` desliga o
  cofre (só diagnóstico).
- Cópias do `admin.json` com segredo em claro (`.bak`, `.old`, `.tmp`…) e backups em claro
  anteriores ao cofre (quando já existe backup cifrado mais novo do mesmo app/ambiente) são
  sobrescritos e apagados na partida, com log e alerta; `HUB_KEEP_PLAINTEXT_COPIES=1` /
  `HUB_KEEP_PLAINTEXT_BACKUPS=1` mantêm.

### Auditoria imutável
Cadeia de hashes em `$HUB_DATA_DIR/audit/` (`chain.jsonl`, `head.json` assinada, segmentos em
`archive/` com `archive/manifest.json` assinado), checkpoints Ed25519 a cada 100 eventos. A chave
confiável é só a derivada da semente do cofre; a âncora (contador monotônico da cabeça, piso da
retenção) fica no `keyring.json` cifrada pela chave mestra — cadeia apagada ou trocada por cópia
antiga bloqueia a cadeia e abre alerta crítico. A âncora é **obrigatória**: o registro selado do
keyring (release 2) lembra os valores protegidos e a **geração** de cada um (no AAD), então apagar
a âncora, repor um valor antigo ou rebaixar o keyring bloqueia em vez de adotar o estado atual (a
adoção acontece uma vez só: keyring novo ou migrado da release 1). A âncora é gravada nos
checkpoints/arquivamento/partida e no máximo a cada 30 s (janela documentada na FAQ 16; anterior em
`keyring.json.prev`). Arquivamento interrompido é concluído na partida. Auditoria › **Verificar
integridade** (`POST /api/admin/audit/verify`) e **Exportar** (`GET /api/admin/audit/export`, JSONL
com prova). Retenção: `hub-web --audit-prune-before <seq>` (hub parado); recomeço após perda
confirmada: `hub-web --audit-reinit-chain`.

**Âncora externa — a proteção real contra rollback do diretório inteiro:** `HUB_AUDIT_HEAD_URL`
(`POST`, +`HUB_AUDIT_HEAD_TOKEN`) e/ou `HUB_AUDIT_HEAD_FILE` (JSONL em outro armazenamento), a cada
`HUB_AUDIT_HEAD_EVERY_S` (padrão 3600). O documento sai do estado em memória e é assinado inteiro
(seq, hash, id do evento, piso, arquivados, id do keyring, geração, host, horário); cadeia
bloqueada publica `status: "blocked"`. Falha → alerta no sino; mais de 2× o intervalo sem sucesso →
alerta de atraso e "ATRASADA" na tela (estado em memória). Sem destino, a tela mostra **"Proteção
contra rollback: LOCAL APENAS (configure publicação externa)"**. Conferência fora do host:
`hub-web --audit-verify-published <arquivo-ou-url> [chave-pública|-] [intervalo-máx-s]` (assinaturas,
mesma chave/keyring, seq/geração/piso nunca descem, mesmo seq ⇒ mesmo hash, buracos; saída 0/1).
`/status` pública mostra só os apps marcados em Configurações; com login, os da allowlist do
usuário (Observabilidade idem).

### Feature flags
Plataforma › **Feature flags** (`/admin/flags`): por app × ambiente, liga/desliga ou variantes,
regras por função/usuário, percentual estável e kill switch (tudo auditado). Entrega: `x-hub-flags`
por requisição (avaliado pelo hub) e `POST /{id}/_hub/flags` para tarefas. Exemplos:
`novo-resumo` (ativos, Rust) e `vencimentos-destaque` (contratos, Python). Contrato: `docs/CONTRATO-APP.md` §18.

### Testes da onda 8
- `cargo test --workspace` (cofre, cadeia, métricas, SLO, flags, OTLP, backups cifrados, propagação),
  `cd sdk-python && pytest` (`tests/test_wave8.py`), conformidade 4/4 (`bash conformance/run.sh`, itens 18),
  Playwright `tests-e2e/tests/09-onda8.spec.ts` (7 cenários) e DR com pacotes cifrados
  (`node loadtest/dr.mjs`). Custo medido: `loadtest/RESULTADOS.md` (Onda 8).

## Onda 9 — experiência do usuário (Fase 3)

Tudo em pt-BR por padrão, white label, sem script inline (CSP) e respeitando a lista de apps
de cada função.

### Início personalizável e onboarding
- `/` — widgets **Primeiros passos**, **Favoritos**, **Inbox**, **Recentes**, **Status das
  aplicações** (estado atual dos SLOs/incidentes) e **Atalhos**. "Personalizar": arrastar ou,
  pelo teclado, ↑/↓/✕ em cada widget, "Adicionar widget", "Restaurar padrão"; quem edita
  configurações salva o **padrão de uma função** (`PUT /api/admin/home-layouts/{função}`).
  Layout, favoritos (estrela em **Aplicações**) e recentes ficam no servidor, por usuário
  (`$HUB_DATA_DIR/ux.json`).
- **Tour guiado** no primeiro acesso ao Início (só os pontos que o usuário vê; teclado:
  →/←/Esc; "Pular tour"; Esc interrompe e o próximo acesso retoma; refazer pelo menu do
  usuário). **Primeiros passos** se marcam sozinhos: abrir um app, escolher o tema,
  configurar notificações, usar a busca; para quem pode: cadastrar variável, publicar pacote,
  agendar backup.

### Inbox e aprovações
- Sino do header (todos os usuários) + página `/inbox`: alertas da plataforma (quem vê a
  Saúde), notificações e **pedidos de aprovação** dos apps (outbox, `kind`
  `notification`/`approval_request`) e **pedidos de acesso** (em **Aplicações**, apps fora da
  lista do usuário mostram "Solicitar acesso"; quem gerencia usuários aprova/revoga).
  Filtros, lido/arquivado por usuário, preferências por canal (no hub; e-mail/Teams
  documentados como extensão), contador ao vivo por uma conexão SSE compartilhada entre abas
  (`/api/me/stream` + `logshare.js`).
- Aprovar/recusar chama o app pela rede interna: `POST /{id}/_hub/approvals/{approval_id}`
  (CONTRATO-APP item 20). Demo: **servicos** — "Precisa de aprovação" no novo chamado;
  **ativos**/**contratos** avisam o fim da importação.
- Revisão da onda 9: decisão **atômica** (`pending|error → sending` numa trava; de duas
  simultâneas só uma segue, a outra recebe 409; pedido de acesso grava status + concessão na
  mesma linha do log); `sending` sem resposta em 60 s ou após reinício volta a "Falha ao
  avisar"; resultado do app só vale se bater com a decisão (senão: divergente + alerta);
  item que o usuário não vê → 404. Limites por app (`HUB_INBOX_APP_RATE`, padrão 120/min;
  `HUB_INBOX_RCPT_RATE`, 30/min por destinatário; 1 000 informativos e 500 pendentes por
  app; teto global 5 000 que nunca remove pendentes); funções administrativas só com
  `notify_roles` no manifesto; destinatário por login/e-mail/id (nunca nome). Persistência
  incremental: `$HUB_DATA_DIR/inbox.jsonl` (log de acréscimo, compactado) + `ux.json` só
  para preferências (migração automática). SSE filtra o feed de mudanças pelo público de
  cada item; até `HUB_SSE_PER_USER` (12) conexões por usuário. Pedidos de acesso: 1 pendente
  por app, 5 por usuário, 24 h após recusa; aprovador precisa alcançar o app; concessões
  aparecem em **Usuários**; só acesso ativo é revogável.

### Busca global (⌘K / Ctrl+K)
- Paleta acessível (diálogo + combobox + listbox agrupado), buscas recentes, página
  `/search?q=`. Plataforma (apps, páginas, usuários/implantações/nomes de variáveis conforme a
  permissão) + **federação** em paralelo nos apps (`GET /{id}/_hub/search`, item 19;
  `HUB_SEARCH_TIMEOUT_MS`, padrão 800 ms por app, 5 resultados por app). `GET
  /api/me/search` não tem efeito colateral; a busca "final" é `POST /api/me/search`
  `{"q"}` (mesma origem; grava recentes, passo e analytics). Limite por usuário: rajada de 20
  e 4 buscas/s (429 + `Retry-After`).

### Analytics de uso
- `/admin/analytics` (quem vê a Saúde): DAU/WAU/MAU por app, seções mais visitadas, páginas
  do hub, funil (login → abrir app → ação), buscas sem resultado, uso de recursos, conclusão
  do onboarding, exportação CSV (`/api/admin/analytics.csv`). Privacidade: ids opacos
  de 64 bits (HMAC-SHA256 com chave **selada no cofre**, `analytics.hmac_key`, trocada a cada
  período de retenção ou em `POST /api/admin/analytics/rotate-key`), só agregados diários,
  termos sem resultado só como contagem + hash (texto normalizado só com opt-in: `PUT
  /api/admin/analytics/terms` `{"raw": true}`), CSV sem caracteres de controle e com
  fórmulas neutralizadas, limite de chaves por dia, retenção configurável (7–400 dias,
  `HUB_ANALYTICS_RETENTION_DAYS`, padrão 90), **opt-out** por usuário (Inbox ›
  Preferências) e `HUB_ANALYTICS=0` desliga tudo. Arquivo `$HUB_DATA_DIR/analytics.json`.

### Tema escuro, idiomas e acessibilidade
- Menu do usuário: **tema** (Sistema/Claro/Escuro) e **idioma** (Automático/pt-BR/en/es),
  salvos na conta (`/account/prefs`, `/api/me/prefs`). Cores da identidade Alvesmaia (claro e
  escuro grafite) explícitas em `scripts/brand-palette.json`; `node scripts/gen-dark-theme.mjs`
  grava os blocos de `tokens.css` e converte cores literais de `app2.css`/`app3.css` em tokens
  (`--check` no CI via `js-tests`, inclusive contraste dos pares declarados). Ver `docs/DESIGN-TOKENS.md`.
- i18n: `hub_shell::i18n` (tabela pt-BR/en/es do shell), `x-hub-locale` + `locale` no
  `x-hub-user` para os apps, SDKs `hub_sdk::locale` (Rust) e `hub_sdk.locale` (Python, `t()`
  com o texto pt-BR como chave). **contratos** traduzido por completo.
- Acessibilidade: "Pular para o conteúdo", regiões nomeadas, foco visível, foco no título
  após troca de seção (`hub.js`), Esc nos menus, `aria-live` (toasts, Inbox, Início),
  `prefers-reduced-motion`, alvos ≥ 24 px. Checagem automática com axe-core no Playwright
  (`tests/10-onda9.spec.ts`) e sonda manual `node tests-e2e/a11y-probe.mjs <saída>`.

### Testes da onda 9
`cargo test --workspace` (store da Inbox/destinos, analytics, i18n, SDK search/approvals/
locale), `node --test js-tests/` (paleta, tour, Início, SSE da Inbox, tema escuro em dia),
`pytest` do SDK Python e do contratos (`test_wave9.py`), conformidade itens 19–21 e
`npx playwright test tests/10-onda9.spec.ts` (onboarding, aprovação de ponta a ponta pelo
servicos, pedido de acesso, ⌘K federado, Início persistente, analytics, tema/idioma, axe em
2 temas × 2 idiomas).

## Onda 10 — Conexões (SharePoint, S3) e broker de armazenamento

**Aplicações › Conexões** (`/admin/connections`, permissão "Editar configurações da plataforma"): cadastro de conexões **SharePoint** (Tenant ID, Client ID, segredo **ou** certificado PEM/PFX, site por URL ou id, biblioteca, pasta-base opcional, operações liberadas, tamanho máximo, alcance dos links) e **S3/MinIO**. Credenciais cifradas no cofre (`$HUB_DATA_DIR/connections.json`), nunca exibidas de novo (mascaradas; certificado só pela impressão digital/validade), "Rotacionar credencial". **Testar conexão** mostra cada etapa: token app-only (client credentials), site, biblioteca, pasta-base, gravar/ler/apagar uma sonda. **Vínculos** app × ambiente viram variáveis geridas (`HUB_CONNECTIONS`, `HUB_STORAGE_TOKEN`, `HUB_STORAGE_URL`) com reinício pendente → "Aplicar (reiniciar)". Tudo auditado.

**Broker** (`HUB_BROKER_ADDR`, compose `0.0.0.0:8081`, não publicado): os apps chamam `/_hub/storage/{conexão}/…` com o próprio token; o hub executa no provedor, confinado à pasta-base, em streaming (upload > 4 MiB por sessão em pedaços de 5 MiB), com 429/503 + Retry-After e auditoria por operação. `HUB_BROKER_APP_CIDRS` amarra cada token à rede do app; `HUB_BROKER_PER_APP` (8) limita operações simultâneas. Contrato: `docs/CONTRATO-APP.md` item 22 (modelo de ameaças). Para a TI: FAQ item 21.

**SDKs**: Rust `hub_sdk::storage::connection("nome")` / `Connection::s3_direct`; Python `hub_sdk.storage.connection("nome")` / `s3_direct(S3Config)` — mesma interface (upload, download em stream, list, meta, delete, move, share_link). Exemplos: anexos do `fornecedores` (conexão `fornecedores-anexos` se vinculada, senão o bucket do app) e documentos do `contratos` (`contratos-docs`).

**Simulador** (`crates/hub-graphsim`, só testes/demonstração):

```bash
HUB_GRAPH_URL=http://hub-graphsim:8080/v1.0 HUB_GRAPH_LOGIN_URL=http://hub-graphsim:8080 \
  docker compose --profile sharepoint-sim up -d
# tenant 00000000-0000-0000-0000-00000000c0de · client 11111111-2222-3333-4444-555555555555
# segredo segredo-do-simulador · site https://contoso.sharepoint.com/sites/Hub · bibliotecas Documentos, Arquivos
```

Limitação (`GRAPHSIM_THROTTLE_EVERY`, `GRAPHSIM_LATENCY_MS`, `GRAPHSIM_FAIL_RATE`) e controle `/_sim/config|stats|reset`. A tela avisa quando os endpoints do Graph estão sobrescritos.

**Testes**: `cargo test -p hub-storage -p hub-graphsim` (caminhos, token/cache/renovação, 429, sessões, certificado), `cargo test -p hub-web --test connections` (API, broker, isolamento entre apps, origem, SDK Rust contra o broker real), `pytest` em `sdk-python` (`test_storage.py`) e `contratos` (`test_wave10.py`), conformidade item 22, E2E `tests-e2e/tests/11-onda10.spec.ts`.

## Onda 11 — API pública, MCP e API/MCP dos apps

**Configuração** (`HUB_CONFIG_FILE`, TOML; modelo com os padrões em `config/hub.example.toml`;
o compose monta `./config/hub.example.toml` só leitura em `/etc/hub/hub.toml`, produção usa
`HUB_CONFIG_HOST_FILE`). Seções `[api]`, `[api.docs]`, `[mcp]`, `[mcp.limits]`, `[apps]`;
o ambiente (`HUB_API_ENABLED`, `HUB_MCP_ENABLED`, `HUB_MCP_DESTRUCTIVE_ACTIONS`, …) sobrescreve;
inválido → o hub não sobe (código 2). Conferir: `hub-web --check-config`.

**API pública** (`apps/hub-web/src/api_v1.rs`): `/api/v1/*` — uma tabela (`OPS`) gera o
roteamento, o OpenAPI 3.1 (`/api/v1/openapi.json`; cópia conferida por teste em
`docs/openapi/hub-v1.json`, regenerada com `hub-web --print-openapi`) e as ferramentas MCP. As
operações são **fachadas** das rotas internas (`/api/ops`, `/api/admin`, `/api/me`, conexões)
chamadas em processo com a mesma identidade — mesmo RBAC e mesma auditoria da UI — mais
leituras nativas (catálogo, status, observabilidade). Erros RFC 9457, listas
`{items,total,limit,offset,next}` com `?q=` e filtros por campo, limite por usuário/token,
evento "API v1: <operação>" em toda mutação. Documentação em `/api/docs` (JS/CSS próprios em
`apps/hub-web/assets/api-docs.*`, sem script inline). Escrita com sessão: mesma origem (CSRF);
com token: escopo `api:write`.

**Tokens** (`hub-auth`): ações novas `api:read`, `api:write`, `mcp`, `app-api`,
`secrets:reveal` — exigem **dono** (quem criou com a sessão); o token age como ele (permissões
atuais, apps ∩ escopo do token). Tokens pessoais: `GET/POST /api/me/tokens`,
`POST /api/me/tokens/{id}/revoke`. Tokens de CI (`upload`/`status`) inalterados.

**MCP** (`apps/hub-web/src/mcp.rs`): Streamable HTTP em `[mcp].path` (`/mcp`), JSON-RPC
(`initialize`, `ping`, `tools/list`, `tools/call`, `resources/list|read` — `hub://apps`,
`hub://status`), `Mcp-Session-Id`, `MCP-Protocol-Version`, validação de `Origin`. Ferramentas
do hub = operações de `OPS` marcadas `mcp`, filtradas pelo RBAC do usuário e pelo escopo do
token; ferramentas dos apps agregadas como `<app>.<ferramenta>` (hub → `POST
{upstream}/{app}/_hub/mcp` com o token interno + `x-hub-user`/`x-hub-user-sig`). Destrutivas:
`confirm_token` de uso único (ou ocultas com `destructive_actions = "deny"`). Auditoria
"MCP: <ferramenta>". Ponte stdio para clientes locais: `hub mcp --hub https://…` com
`HUB_TOKEN` (`crates/hub-cli/src/mcp.rs`). Clientes de IA: `docs/IMPLANTACAO.md` §14.

**Apps** (contrato §23–24): manifesto `api`/`mcp` (padrão ligados), `openapi.json` no pacote
(o `hub pack` exige), passo Contrato valida o OpenAPI (e reprova app sem API, salvo
`[apps].allow_without_api`), sonda MCP do candidato no blue/green. API dos apps pelo gateway em
`/{app}/api/*` (sessão ou token `app-api`). SDKs: Rust `hub_sdk::api` (`ApiSpec`, `ApiOp`,
`problem`, `missing_user`) + `hub_sdk::mcp::McpServer`; Python `hub_sdk.api.install_api`
(OpenAPI nativo do FastAPI) + `hub_sdk.mcp` (instalado por `HubSdk.install`). Apps de exemplo:
`ativos` (ativos CRUD/busca/exportação), `fornecedores` (fornecedores, consulta cadastral,
metadados de anexos), `servicos` (chamados, relatório, aprovações pendentes), `contratos`
(contratos CRUD, exportação, tarefas, documentos). Regenerar os `openapi.json` dos pacotes:

```bash
for a in ativos fornecedores servicos; do cargo run -q -p hub-demo-apps -- openapi $a > samples/src/$a/openapi.json; done
(cd demo-apps-py/contratos && ../../.venv/Scripts/python -m contratos openapi > ../../samples/src/contratos/openapi.json)
bash scripts/sample-packages.sh
```

**Testes**: `cargo test -p hub-web --test api_mcp` (matriz sessão × token × RBAC, problem+json,
CSRF, MCP: protocolo, sessões, filtragem por usuário, confirmação, agregação, auditoria, ponte
`hub mcp`, API dos apps pelo gateway, configuração desligada/inválida), `api_v1::tests`,
`mcp::tests`, `hubconfig::tests`, `hub-ops` (`api_is_mandatory…`, `mcp_probe…`), `hub-sdk`
(`api`, `mcp`), `hub-demo-apps` (`api_contract_tests`), `pytest` `test_wave11.py` (SDK e
contratos; valida todos os OpenAPI com `openapi-spec-validator`), conformidade §23–24 e
`npx playwright test tests/12-onda11.spec.ts` (docs, token pela tela, cliente MCP contra o
compose).

## Onda 12 — estado compartilhado e várias réplicas do hub-web (item 2.3)

O estado de execução que vivia só na memória de um processo fica no **PostgreSQL da
plataforma** (hub-db, banco próprio `hub`, esquema `hub`; crate `crates/hub-state`), e o
hub-web pode rodar com **duas ou mais réplicas** atrás do mesmo endereço, **sem sessão
grudada** — e sobrevive a reinícios sem perder sessões nem pedidos pendentes.

**Ligar:** `HUB_STATE_DB_URL=postgres://hubstate:<senha>@hub-db:5432/hub` (no compose base já
vem ligado; `HUB_STATE_DB_URL=` vazio volta ao modo de réplica única). Na primeira partida o
hub cria o usuário e o banco pela `HUB_DB_ADMIN_URL` (ou `HUB_STATE_DB_ADMIN_URL`) e aplica as
migrações sob advisory lock. URL definida e banco fora por 90 s (`HUB_STATE_DB_WAIT_S`) → o hub
**não sobe** (nunca roda "meio" réplica única ao lado de réplicas que usam o banco). Sem a
variável (`hub dev`, `scripts/demo.sh`): arquivos + memória como antes, com o aviso "várias
réplicas NÃO são suportadas" no log. Outras variáveis: `HUB_STATE_DB_POOL` (12),
`HUB_STATE_DB_SCHEMA` (`hub`), `HUB_REPLICA_ID` (padrão: nome do host + sufixo),
`HUB_LEADER_TTL_S` (15), `HUB_STATE_REFRESH_MS` (1000), `HUB_STATE_STALE_S` (5: sem recarga
há mais que isso, token de API e mutação do plano de controle → 503), `HUB_TRUSTED_PROXIES`
(CIDRs dos balanceadores cujo `X-Forwarded-For` vale — ver IMPLANTACAO §15),
`HUB_REPLICA_HEADER=1` (cabeçalho `x-hub-replica` em toda resposta — diagnóstico/testes).

| Estado | Onde fica | Entre réplicas |
|---|---|---|
| Sessões de login (hash do cookie) | tabela `sessions` | login numa réplica vale nas outras; logout/troca de senha/desativação derrubam em todas; teto por usuário sob advisory lock |
| Limitador de login (IP, IP+conta, atraso da conta) | `records` (`login.*`, atualização com `SELECT … FOR UPDATE`) | contagem única; sobrevive a reinício |
| Limites de taxa da API (`[api].rate_per_min`), do MCP (`calls_per_min`) e da Inbox | `rate_windows` (janela fixa de 1 min; `INSERT … ON CONFLICT DO UPDATE … WHERE n < limite`) | a cota é do cluster (não "por réplica") |
| Sessões MCP (`Mcp-Session-Id`), `confirm_token`s de ações destrutivas | `records` (chave = SHA-256; consumo com `DELETE … RETURNING`) | sessão aberta numa réplica vale nas outras; pedido pendente **sobrevive a reinício** (não vira mais "expirado" na partida) |
| Tokens de step-up e marcas de reautenticação SSO | `records` (hash) | volta do IdP numa réplica, aprovação em outra |
| Tickets e sessões de staging | `records` (hash) | ticket emitido numa réplica, confirmado/trocado em outra |
| Jobs de Operações (validação, backup, restauração) | `jobs` (ids da sequência do banco) | a UI acompanha o job por qualquer réplica; job de réplica morta vira "Interrompido" |
| Travas de implantação por app | advisory lock de sessão (exclusiva/compartilhada) | dois deploys do mesmo app nunca rodam em réplicas diferentes |
| Chave do `x-hub-user` (sem `HUB_USER_SIG_KEY`) | `kv`, **cifrada pelo cofre** | uma chave só para o cluster |
| Liderança | `leases` (relógio do banco, **fencing token**) + `replicas` (sinal de vida) | um só líder; morto → outro assume em ≤ TTL + ⅓ TTL |
| Arquivos de dados (`admin.json` + cadeia de auditoria, `auth.json`, `ops.json`, `ux.json`, `inbox.jsonl`, `connections.json`, `keyring.json`, `analytics.json`) | no volume **compartilhado** (`hub-data`), gravados sob trava + geração no banco (`file_gen`) | nenhuma gravação perdida; quem vai gravar recarrega antes se outra réplica gravou; as outras recarregam em ≤ 1 s |
| Métricas, traces, logs ao vivo, conexões SSE | memória **de cada réplica** | cada réplica mostra o que passou por ela (ver PENDENCIAS) |

**Só no líder** (lease `hub-leader`): coleta do outbox dos apps, agendador de backups,
supervisão dos containers (religar/backoff/OOM), reconciliação com o runtime (ao assumir),
limpeza de `__prev` e de containers de tarefa, expiração de pedidos de agente, limpeza do
banco de estado, alertas de SLO, publicação da âncora da auditoria e troca da chave do
analytics. Ao assumir, o novo líder reconcilia e converte as restaurações e os jobs das
réplicas sem sinal de vida (30 s). No `SIGTERM` o líder solta o lease (troca imediata).

**Partida em série:** cada réplica abre/migra os arquivos de dados segurando a trava
`hub-startup` (a segunda espera a primeira terminar).

**Duas réplicas no compose** (`docker-compose.replicas.yml`): `hub-web` (A) e `hub-web-2` (B)
no mesmo volume e no mesmo banco de estado, atrás do `hub-lb` (nginx round-robin, sem sessão
grudada, `infra/lb/nginx.conf`) em `http://localhost:8080`; A e B também em
`127.0.0.1:8091`/`8092` para os testes. `HUB_RUNTIME_ATTACH=hub-web,hub-web-2` põe as duas
réplicas na rede de cada app (re)criado.

```bash
docker compose -f docker-compose.yml -f docker-compose.replicas.yml --profile sharepoint-sim up -d --no-build
node loadtest/e2e/w12-replicas.mjs      # login A→B, token, taxa do cluster, MCP/aprovação, staging,
                                        # liderança (mata o líder), reinício com pedido pendente
bash scripts/ci-local.sh --only replicas   # o mesmo, subindo o override e voltando a uma réplica
```

**Testes com banco** (`hub-testdb`, `ci-local.sh --only db-tests`): `cargo test -p hub-state
--test db -- --ignored` (uso único sob concorrência, vencimento, taxa exata entre réplicas,
teto de sessões, lease com tomada após morte e fence, travas, jobs, gerações de arquivo),
`-p hub-auth --test db` (login/logout/teto/limitador entre duas "réplicas", gravações
concorrentes do `auth.json`) e `-p hub-admin --test db` (trilha e cadeia de auditoria íntegras
com escritores concorrentes em duas réplicas, rotação de DEK vista pela outra, eventos de app
em fila).

## Onda 12 — MCP conforme a especificação atual e OAuth 2.1 (item 2.2)

O `/mcp` segue a especificação atual do Model Context Protocol para clientes **remotos**
(ex.: Langdock), sem distribuir tokens à mão:

- **Transporte** (`apps/hub-web/src/mcp.rs`): revisão **2026-07-28** (sem sessão; `_meta` +
  `MCP-Protocol-Version`/`Mcp-Method`/`Mcp-Name` conferidos; `server/discover`) e legado
  2025-03-26…2025-11-25 (`initialize`, `Mcp-Session-Id`, `GET` SSE, `DELETE`, lotes na
  2025-03-26). `tools/call` moderno responde em SSE com keep-alive.
- **OAuth 2.1** (`apps/hub-web/src/oauth.rs`; o hub é servidor de autorização e recurso):
  `/.well-known/oauth-protected-resource[/mcp]`, `/.well-known/oauth-authorization-server`,
  `/oauth/register` (DCR), `client_id` por URL (Client ID Metadata Document),
  `/oauth/authorize` (login do hub + consentimento), `/oauth/token` (PKCE S256; refresh com
  rotação e detecção de reuso), `/oauth/revoke`. Tokens `hubo_…` valem só no `/mcp` deste hub.
  Usuário: **Apps autorizados** (menu do usuário, `/account/authorized-apps`); administração:
  seção **Clientes OAuth do MCP** em Tokens de API. Configuração `[mcp.oauth]`; emissor =
  `HUB_PUBLIC_URL` (sem ela, o `Host` da requisição). Teste local do CIMD com endereço
  privado: `HUB_OAUTH_CIMD_ALLOW_PRIVATE=1` (só testes).
- **Testes**: `cargo test -p hub-web --test oauth_mcp` (+ `oauth::tests`, `api_mcp`);
  compose no ar: `bash scripts/ci-local.sh --only mcp-client` (SDK oficial TypeScript faz
  descoberta → DCR → PKCE → consentimento → token → tools/list/call → refresh),
  `--only replicas` (§5b: autoriza numa réplica, troca o código na outra) e
  `npx playwright test tests/13-onda12-oauth.spec.ts`.
- **Decisão `rmcp`**: avaliado o `rmcp` 3.5.0 (revisão 2026-07-28); mantido o JSON-RPC próprio
  (sessões em memória do processo no `StreamableHttpService` × réplicas no banco; identidade e
  tabela de ferramentas por requisição; fluxo de aprovação) — ver PENDENCIAS.

## Onda 12 — anti-phishing com os apps na mesma origem (item 2.1)

Os apps continuam na **mesma origem** do hub (um domínio por app não é possível). Defesas:

- **Apps sem JavaScript.** A CSP das páginas de app (`auth::csp_with`) aceita só os scripts
  do hub (`/assets/`), com **Trusted Types obrigatório**, `form-action` restrito ao próprio
  app (+ formulários do header), `object-src`/`base-uri`/`frame-ancestors 'none'`; `/_frag/*`
  aberto direto vira documento `sandbox`. Todo HTML de app passa pelo sanitizador
  `crates/hub-shell/assets/hub-safe.js` (DOMParser + lista de permissão; sem `<script>`,
  `on*`, `javascript:`, `<base>`, `<meta http-equiv>`, `<iframe srcdoc>`, formulários para
  `/login`, `/account/*`, `/oauth/*`).
- **Comportamentos declarativos** `data-hub-*` (`hub-kit.js`): atualização parcial
  (get/post/target/swap/trigger, `every Ns`), confirmação, modal por `<template>`, tabela
  (filtro, ordenação, páginas), upload com progresso, gráficos (TimeSeriesChart + barras,
  linha, área, rosca — `hub-chart.js`), toast (`X-Hub-Toast`), copiar, abas, selects
  dependentes, validação. Os 4 apps de demonstração foram migrados (sem `chart.js`/`interact.js`).
- **Exceção revisada:** `manifest.scripts = [{path, reason}]` (arquivos `ui/assets/*.js` do
  pacote); o pipeline calcula o sha384 e mostra **REVISE: App traz JavaScript próprio**; a
  administração aprova à parte (Implantações › "Aprovar JavaScript" /
  `POST /api/ops/deployments/{id}/scripts/approve`, auditado) — sem isso a produção não é
  aprovada; aprovado, o hash entra na CSP da versão ativa e o `hub.js` carrega com SRI
  (arquivo diferente = bloqueado; hash novo = nova aprovação). SDKs: `hub_sdk::scripts`,
  `hub_sdk.scripts`.
- **Telas de credencial** (login, senha, aprovação de ação do agente, consentimento OAuth):
  páginas de topo do hub, sem conteúdo de app, sem script, `frame-ancestors 'none'`; ids
  `account`/`oauth` reservados; `Cross-Origin-Opener-Policy: noopener-allow-popups`. (A
  "frase de segurança" foi removida em 01/10/2026.)
- **Testes:** `node --test js-tests/onda12.test.mjs` (regras + corpus de XSS num Chromium
  real com Trusted Types), `cargo test -p hub-web csp_tests`, `-p hub-ops` (pipeline/modelo),
  `-p hub-manifest`, `-p hub-demo-apps --test fragments`, conformidade
  (`test_no_app_javascript_in_fragments`), Playwright `tests/14-onda12-antiphishing.spec.ts`
  (fragmento hostil — rota **só de teste** `HUB_DEMO_E2E_HOSTILE=1` do compose de
  demonstração —, SRI aprovado × adulterado, REVISE bloqueando, telas de credencial de topo).

## Onda 12 — OpenAPI tipado, idiomas nas telas do hub, sagas e envios em duas fases (itens 2.4, 2.8, 2.9)

- **API pública tipada:** tipos `utoipa` em `apps/hub-web/src/api_types.rs`, usados pela tabela `api_v1::OPS`; o documento `docs/openapi/hub-v1.json` é gerado (`cargo run -p hub-web -- --print-openapi > docs/openapi/hub-v1.json`) e um teste falha se estiver desatualizado. `cargo test -p hub-web --test api_contract` valida uma resposta real de cada operação contra o esquema; `bash scripts/ci-local.sh --only openapi-lint` roda o Redocly (etapa do `--quick`).
- **Idiomas:** Administração, Operações, Conexões, Inbox, OAuth, conta e mensagens de erro em pt-BR/en/es pela preferência do usuário ou `Accept-Language`. Catálogo em `crates/hub-shell/i18n/catalog.json` (`[pt-BR, en, es]`); textos novos: acrescente a linha (o teste `cargo test -p hub-web --test i18n_scan` lista os que faltam); `cargo test -p hub-web --test i18n_pages` confere as páginas; `node --test js-tests/i18n.test.mjs` o tradutor do navegador.
- **Sagas e envios em duas fases nos SDKs:** `hub_sdk::saga` / `hub_sdk.saga` e `hub_sdk::uploads` / `hub_sdk.uploads` (contrato §27). Exemplo: `POST /apps/ativos/api/v1/assets/registrations` (`demo-apps/src/ativos/saga.rs`; `falhar_em: "notificar"` demonstra a compensação). Testes com Postgres/MinIO descartáveis no `ci-local.sh --only db-tests`.

## Onda 13 — app Coletor, recursos do aparelho e PWA

**Coletor** (`demo-apps-py/coletor`, Python/FastAPI + hub-sdk-python, imagem `hub-coletor`,
banco `app_coletor`, 384 MiB): coletor de dados para celular e leitor de código de barras, com
identidade própria (amarelo de segurança sobre grafite, claro e escuro, alvos ≥ 48 px, campos
de 18 px). É uma **bancada de leitura** com três telas: **Escanear** (celular: alternância
"Código de barras" / "QR" lembrada por usuário — muda os formatos da câmera e a mira —, botão
da câmera em leitura contínua, campo do leitor físico sempre pronto, cartão grande do último
resultado com bipe/vibração e as últimas 10 leituras; cada leitura é gravada por usuário, com
fila offline), **Folha de teste** (computador: QR "abrir o app" à parte e os códigos por grupo
— Itens, Locais, Pedidos, Formatos variados — com **Leituras ao vivo** ao lado: o que o mesmo
usuário lê no celular aparece em ~1 s, com hora, valor, formato, origem câmera/coletor físico e
o que é — "Item: …", "Local: LOC-A01", "Pedido REC-1001", "Link para o app", "Código
desconhecido" —, contador e "Limpar") e **Diagnóstico do aparelho**. As telas antigas
(Contagem, Recebimento, Consulta, Insumos) mostram o Escanear/Folha.
Seed: 200 itens (EAN-13 `789…` + Code128 `COL-nnnn`), 12 locais `LOC-A01…C04`, 3 pedidos
`REC-1001…1003` com datas relativas a hoje.

- **Sem JavaScript próprio**: câmera, leitor tipo teclado, bipe/vibração/flash, foto, fila
  offline, instalação e diagnóstico são atributos `data-hub-*` interpretados pelo
  `crates/hub-shell/assets/hub-device.js` (CONTRATO-APP §25.1). Decodificador: `BarcodeDetector`
  nativo (Android/Chrome) ou ZXing em JS puro (`/assets/vendor/zxing.min.js`, sem WebAssembly).
- **PWA do hub**: `/manifest.webmanifest` (`?start=/apps/coletor` nas páginas do app), `/hub-sw.js`
  (só casco + `/offline`) e o botão "Instalar no aparelho" no Diagnóstico.
- **Stack no catálogo**: campo opcional `stack` do manifesto (linguagem, framework, SDK, banco,
  bibliotecas) mostrado em `/applications` no hover, foco e botão "i".

Testar no celular (câmera só em HTTPS): no computador abra **Coletor › Folha de teste**; no
celular abra `https://hub.example.com/apps/coletor` (Cloudflare Access + login, mesmo
usuário), toque em **Ler com a câmera** e aponte para a folha — a leitura aparece em
"Leituras ao vivo" no computador. App isolado: `bash scripts/dev-app.sh coletor`
(localhost conta como contexto seguro). Testes: `cd demo-apps-py/coletor && pytest` (com
`HUB_DB_TEST_URL` os de banco), `node --test js-tests/` (`onda13-device`), E2E
`npx playwright test tests/25-coletor.spec.ts tests/26-skeleton.spec.ts tests/27-catalogo-stack.spec.ts`
(câmera falsa do Chromium com um quadro `.y4m` gerado da folha de teste), conformidade
`bash conformance/run.sh coletor`, paridade `node tests-e2e/parity/parity.mjs --app coletor`.

**Ao vivo e fila de teste (13c).** A Folha começa com o QR do app, "Iniciar sessão"
("Avançar automaticamente após a leitura") e a folha completa para imprimir; iniciada, mostra
**um código por vez** em tamanho grande, em ordem sorteada (24 códigos, "3 de 24"), com
Próximo, Parar/Continuar, Cancelar e Reiniciar (confirmação; nova ordem), a situação (aguardando,
certo, "leu X — não é este código") e os resultados; no fim, acertos, erros, pulados e tempo
médio por leitura. O Escanear (celular) mostra o código esperado, a posição e os mesmos controles,
e o modo do leitor: **Interativo** (depois de uma leitura, espera "Próximo") ou **Automático**
(pausa configurável, 0–5 s; o mesmo código em 5 s é ignorado). O estado da sessão é do servidor
(`col_sessions`, uma por usuário) e chega às duas telas por **WebSocket**
(`/apps/coletor/ws/live`, pelo gateway do hub — `HUB_WS_MAX_PER_USER` 8, `HUB_WS_PING_SECS` 25,
`HUB_WS_IDLE_SECS` 60, `HUB_WS_MAX_SECS` 900, `HUB_WS_MAX_MESSAGE_KB` 256; CONTRATO-APP §25);
sem socket, polling de 2 s. E2E `tests/28-coletor-fila.spec.ts` (duas páginas: Folha 1440 +
Escanear 390 com câmera simulada).

## Manutenção do target/

`target/` cresce com o uso (builds de dev/test/release acumulam incrementalmente e nunca
encolhem por conta própria). O `scripts/ci-local.sh` limpa `target/release`, `target/ci` e os incrementais (e, se
ainda preciso, `cargo clean`) quando `target/` passa de `CI_TARGET_MAX_GB` (padrão 25 GiB); os
binários Linux ficam no volume `hub-builder-target`, não no `target/` do host.

## Licença / License

Ainda não definida — todos os direitos reservados até a escolha de uma licença aberta (`license = "UNLICENSED"` no `Cargo.toml`).

Not yet defined — all rights reserved until an open-source license is chosen.

## Modo de desenvolvimento ("runserver")

Para iterar na pilha em containers sem recompilar imagens a cada mudança (aprovado pelo dono; trade-offs abaixo aceitos). **Só para desenvolvimento** — a validação final é sempre nas imagens reais.

```bash
bash scripts/dev-mode.sh up          # liga (≈ 20 s com o target do builder já aquecido)
bash scripts/dev-mode.sh status      # modo atual, builder, memória
bash scripts/dev-mode.sh logs        # builds e reinícios (segue o log do hub-dev-builder)
bash scripts/dev-mode.sh reload-on   # recarga ao vivo do navegador (padrão: desligada)
bash scripts/dev-mode.sh reload-off
bash scripts/dev-mode.sh down        # volta à pilha normal (imagens prontas, sem montagens), ainda na :8080
```

**Como funciona** (`docker-compose.dev.yml` sobre o `docker-compose.yml`):

- **Assets do disco.** Com `HUB_DEV_MODE=1` o hub-web lê os assets do shell (`crates/hub-shell/assets`, montado em `HUB_DEV_SHELL_ASSETS_DIR`) e os próprios (`apps/hub-web/assets` → `HUB_ASSETS_DIR`) **a cada requisição**, com `Cache-Control: no-store` em todo `/assets/*` (só nomes da lista embutida; arquivo ausente cai na cópia embutida). Os apps Rust já liam fragmentos/CSS do disco (`HUB_DEMO_DIR`): no modo dev `demo-apps/fragments` e `demo-apps/assets` são montados por cima e respondem `no-store`. Fora do modo dev as variáveis são ignoradas. **CSP/SRI:** a CSP do hub autoriza scripts pela URL `/assets/` (sem hash do conteúdo), então editar `hub.js`/`hub-kit.js` não diverge; os hashes `sha384` dos scripts aprovados de apps vêm da aprovação da implantação (não do disco) e continuam exigidos; Trusted Types e o resto da CSP valem iguais.
- **Rust: recompila e reinicia sozinho.** O container `hub-dev-builder` (imagem `hub-builder:2` = a do `build-linux.sh` + `cargo-watch` pronto da release oficial; volumes `hub-builder-registry`/`hub-builder-target` compartilhados, 4 CPUs / 4 GB, `CARGO_BUILD_JOBS=4`) roda `scripts/dev-watch.sh`: `cargo watch --poll --no-restart` (um build por vez) no perfil `ci`; build ok → os binários que **mudaram** vão para o volume `hub-dev-bin` (rename atômico) e só os containers afetados reiniciam pela API do Docker (`hub-web`; `hub-demo-apps` → os 3 apps Rust + `hub-extapi`; `hub-graphsim`). Build com erro: os serviços seguem com o binário anterior (o erro aparece em `dev-mode.sh logs`). Os containers usam o binário do volume (`entrypoint` em `/opt/hub-dev/bin`), sem rebuild de imagem.
- **Python (contratos):** `uvicorn --reload` (varredura a cada 1 s) com `contratos` e `hub_sdk` montados e na frente do venv (`PYTHONPATH`); tarefas no mesmo processo (`HUB_JOBS_MODE=inline`).
- **Recarga ao vivo (opcional, `reload-on`):** `HUB_DEV_RELOAD=1` liga o SSE `GET /_dev/reload` (a rota **não existe** sem `HUB_DEV_MODE=1` + `HUB_DEV_RELOAD=1` — teste `devmode::tests`) e o `<meta name="hub-dev-reload">` no shell; o `hub-ux.js` escuta e recarrega a página quando algo em `HUB_DEV_WATCH` muda (assets, fragmentos, código do contratos, binários novos — varredura de 700 ms) ou quando o hub volta de um reinício (id da partida diferente). Nas páginas de app a CSP ganha `connect-src …/_dev/reload` só nesse modo.
- **URL pública do túnel.** O `apphub-devtunnel` (container avulso, `scripts/dev-tunnel.sh`) encaminha para `host.docker.internal:8080`; o hub vê essa conexão — e a do navegador local — vindo do gateway da `hub-edge` (`10.231.0.1`, medido). Por isso: `HUB_PUBLIC_URL=https://hub.example.com`, `HUB_TRUSTED_PROXIES=10.231.0.1/32` (vale o `X-Forwarded-For`/`X-Forwarded-Proto` do cloudflared), `HUB_TRUST_PROXY_HEADERS=1`, plano de controle aberto a IPs públicos (o Access é o portão externo; redes internas `10.231.0.0/16` continuam fora), `HUB_MCP_PUBLIC_NETWORK=1`. Só no modo dev: conexão do gateway **sem** `X-Forwarded-For` = navegador local (aceita pelo IP da conexão); cookie `Secure`/esquema da CSP **por requisição** (https só com `X-Forwarded-Proto: https` do túnel — senão `http://localhost` teria a CSP com `https://localhost` e nenhum script carregaria); emissor OAuth = a própria origem para pedidos de loopback e a URL pública para o túnel. Porta publicada **só no loopback** (`127.0.0.1` e `[::1]`): ninguém da rede local forja `X-Forwarded-For`.
- **Nunca em produção:** o `docker-compose.prod.yml` fixa `HUB_DEPLOYMENT=production` e `HUB_DEV_MODE=0`, e o hub-web **recusa subir** com `HUB_DEV_MODE=1` junto de qualquer sinal de produção (`HUB_DEPLOYMENT=production`, `HUB_DEMO_USERS=0`, SSO ligado, `HUB_MASTER_KEY_FILE`, `HUB_USER_SIG_KEY`, `HUB_BOOTSTRAP_ADMIN_EMAIL`) — `devmode::startup_check`.

**Medições (03/10/2026, Windows 11 + Docker Desktop, 16 vCPU / 16 GB na VM):**

| O quê | Tempo |
|---|---|
| Editar CSS (`hub.css` do shell ou `app2.css` de um app) → servido novo (basta recarregar a página) | ≈ 0,3 s |
| Recarga ao vivo ligada: salvar CSS → navegador recarrega sozinho | 0,3–0,7 s |
| Editar um `.rs` do hub-web → processo novo no ar (detecção ≈ 1 s + build `ci` 28–44 s + reinício ≈ 2 s) | 31–46 s (saudável ≤ 40–48 s) |
| Recarga ao vivo: reinício do hub-web → navegador recarrega | ≈ 8 s |
| Editar um `.py` do contratos → uvicorn recarregado / respondendo | ≈ 0,7 s / ≈ 2 s |
| `dev-mode.sh up` (target aquecido) / `down` | ≈ 20 s / ≈ 10 s |

Memória: hub-dev-builder ≈ 55 MiB ocioso, **pico ≈ 1,4 GiB** recompilando o hub-web (teto 4 GiB); hub-web e apps como na pilha normal (hub-web ≈ 40–200 MiB, apps Rust ≈ 2–10 MiB, contratos ≈ 70–85 MiB). CPU ociosa: contratos ≈ 1,5 % com `--reload-delay 1` (≈ 30 % com o padrão de 0,25 s, por isso o ajuste); `cargo watch --poll` ≈ 3 %.

**Trade-offs:**

- **Memória/CPU:** o builder fica no ar (até 4 GB/4 CPUs durante um build). Numa máquina com pouca memória, não rode o modo dev junto do `ci-local.sh`: os dois usam o mesmo target (`hub-builder-target`) e o `dev-mode.sh up` para o `hub-builder` do CI.
- **Reinícios derrubam estado em memória:** cada build do hub-web reinicia o processo — conexões SSE (Inbox, recarga) caem e reconectam, aprovações de agente em memória expiram, uploads em andamento falham. Sessões ficam (banco de estado).
- **Montagens do Windows não entregam inotify:** tudo é por varredura (cargo watch 500 ms, uvicorn 1 s, recarga 700 ms) e ler do disco montado custa alguns ms por asset.
- **Dev ≠ produção:** binários `ci` (opt-level 1), assets fora do binário, porta só no loopback, IP/esquema/emissor por requisição, contratos com tarefas no processo web. **A validação final é sempre nas imagens reais:** `bash scripts/dev-mode.sh down` → `bash scripts/ci-local.sh --standard` + varredura de UI completa (`cd tests-e2e && node ui-sweep/run.mjs`).
- Uma implantação pelo hub (blue/green em Operações) recria o container do app **sem** as montagens do modo dev — rode `dev-mode.sh up` de novo depois.

## Ambiente dev público

- **URL:** https://hub.example.com, via Cloudflare Tunnel nomeado `app-hub-dev` + **Cloudflare Access** (login por e-mail/OTP). Quem pode entrar é definido em Cloudflare Zero Trust → Access → Applications (`hub.example.com`). O PC precisa estar ligado e a pilha no ar.
- **Túnel (container avulso):** `bash scripts/dev-tunnel.sh up|down|status`. O cloudflared roda **fora do compose**, no container `apphub-devtunnel` (`restart unless-stopped`, 64 MB), com `.tunnel/config-standalone.yml` (gitignored; ingress `hub.example.com → http://host.docker.internal:8080`, Host preservado; resto 404) e as credenciais copiadas de `~/.cloudflared/<id>.json` para `.tunnel/`. Ele encaminha para **o que estiver servindo a porta 8080 do host** — pilha normal ou modo de desenvolvimento —, então subir/descer/recriar a pilha não derruba o túnel. `up` não recria um túnel existente (só garante que está no ar); `down` o remove.
- **Configuração do hub-web atrás do túnel** (no `docker-compose.dev.yml`, ligada pelo `scripts/dev-mode.sh up`): `HUB_PUBLIC_URL=https://hub.example.com` (cookies `Secure`; emissor OAuth); `HUB_TRUSTED_PROXIES=10.231.0.1/32` — o cloudflared chega por `host.docker.internal:8080` → porta publicada, e o hub vê a conexão vindo do **gateway da `hub-edge` (10.231.0.1)**, o mesmo endereço do navegador local (medido com um container de sonda); o `X-Forwarded-For` do cloudflared (IP do visitante) passa a valer. Uma conexão do gateway **sem** `X-Forwarded-For` é o navegador local — só no modo dev o hub a aceita pelo IP da conexão (fora dele, proxy confiável sem `X-Forwarded-For` é negado no plano de controle). Para ninguém da rede forjar o cabeçalho, no modo dev a porta fica **só no loopback** (`127.0.0.1:8080`; o `host.docker.internal` do Docker Desktop continua alcançando). `HUB_TRUST_PROXY_HEADERS=1`; `HUB_MCP_PUBLIC_NETWORK=1`. A pilha normal (`dev-mode.sh down`) também responde pelo túnel, mas sem essas variáveis (IP do visitante não reconhecido, cookies sem `Secure`).
- **Trade-off do plano de controle:** `HUB_ADMIN_ALLOWED_CIDRS` passa a aceitar qualquer IP público (v4/v6) — o Access já barra todos antes do hub. As redes internas `10.231.0.0/16` (apps/dependências) continuam bloqueadas (a lista é o complemento dessa faixa). Usuários demo (`admin/admin`) seguem ativos; o portão externo é o Access.
- **MCP/Langdock:** `/mcp` e `/oauth` ficam fora do filtro de CIDR, mas o Access ainda bloqueia clientes de nuvem; para o Langdock seria preciso uma política Bypass ou Service Token no Access para esses caminhos (não configurada).
- **Endurecimento futuro:** validar no hub o JWT do Access (`Cf-Access-Jwt-Assertion`, chaves em `https://<time>.cloudflareaccess.com/cdn-cgi/access/certs`) para recusar requisições que não passaram pelo Access.
