# Implantação — hub Python (platform-py): servidor novo

Checklist curto. A partir de `platform-py/`, Docker Compose >= 2.24.

**Atalho:** `bash scripts/install.sh` num terminal pergunta cada opção (admin, porta, bind, URL pública, projeto,
faixa de rede, apps de exemplo), mostra um resumo e pede confirmação. Com opções ou `--yes` não pergunta nada:
`bash scripts/install.sh --admin-email voce@empresa.com --public-url https://hub.empresa.com` faz os
passos abaixo (segredos novos, imagens do código-fonte, chave mestra, primeiro administrador, subida). Depois siga do
item "Túnel/proxy TLS" em diante. Atualizações: [`ATUALIZACAO.md`](ATUALIZACAO.md).

**Opções da pilha** (no `.prod.env`; padrões = instalação original): `HUB_PROJECT` (prefixo de projeto, contêineres,
volumes, redes e imagens; `app-hub`), `HUB_PORT` (8090), `HUB_BIND` (127.0.0.1), `HUB_SUBNET` (10.231 → redes
`10.231.<n>.0/24` dos apps), portas de depuração `HUB_APPDB_PORT`/`HUB_APP_PORT_<APP>`, `HUB_MINIO_IMAGE`/`HUB_MC_IMAGE`.
**Apps de exemplo** (ativos, fornecedores, servicos, coletor) só sobem com `COMPOSE_PROFILES=demo-apps`; sem eles use
`HUB_APPS_JSON=./apps.empty.json` (catálogo vazio). O banco dos apps (`app-hub-app-db`) sobe sempre.

**MinIO/mc:** não há imagem pública utilizável; `infra/minio/Dockerfile` compila do código-fonte oficial (tags fixas;
AGPL-3.0, programa separado usado só pela rede — ver `NOTICE`). `install.sh` gera `<projeto>-mc` e `<projeto>-minio`;
manualmente: `docker build --target mc -t app-hub-mc:0.10.0 infra/minio` antes do `up --build` (o hub copia o `mc`).

**Imagens prontas (GHCR):** o workflow `.github/workflows/images.yml` é **manual** (Actions › images › Run workflow,
entrada `version`) e publica `ghcr.io/<dono>/app-hub-{web,minio,mc,ativos,fornecedores,servicos,coletor}:<versão>`
(+ `latest`). Para usar: `HUB_VERSION=<versão> docker compose --env-file .prod.env -f docker-compose.yml
-f docker-compose.apps.yml -f docker-compose.prod.yml -f docker-compose.ghcr.yml pull` e depois `… up -d`
(`HUB_REGISTRY` troca o registro). Pacotes do GHCR nascem privados: torne-os públicos nas configurações do pacote.

- [ ] `cp prod.env.example .prod.env && chmod 600 .prod.env`; preencher **todas** as variáveis obrigatórias com valores
      novos por servidor (`openssl rand -hex 24`): `HUB_PUBLIC_URL` (https), `APP_HUB_DB_PASSWORD`,
      `APP_HUB_APP_DB_PASSWORD`, `APP_HUB_OBJSTORE_PASSWORD`, `HUB_INTERNAL_TOKEN_<APP>` (4). Opcionais: CIDRs
      (`HUB_ADMIN_ALLOWED_CIDRS`, `HUB_TRUSTED_PROXIES`, `HUB_BROKER_APP_CIDRS`), `HUB_USER_SIG_KEY`, telemetria.
      **Não** defina `HUB_DEMO_USERS`/`HUB_DEMO_PASSWORD`.
- [ ] Subir: `docker compose --env-file .prod.env -f docker-compose.yml -f docker-compose.apps.yml -f docker-compose.prod.yml up -d --build`
      (recusa subir se faltar variável; cookies Secure; rotas de teste dos apps desligadas). Apps de exemplo: `COMPOSE_PROFILES=demo-apps` no `.prod.env`.
- [ ] Chave mestra do cofre: criada em `/data/master.key` no primeiro boot — copie para fora do servidor (sem ela os segredos cifrados não abrem).
- [ ] Primeiro administrador (nada secreto no log): `docker compose ... run --rm --no-deps app-hub-web python -m hub.admin create-admin --email voce@empresa.com --out /data/primeiro-acesso.txt`;
      leia a senha temporária do arquivo (`docker cp`), apague-o do volume; a troca de senha é obrigatória no primeiro login.
- [ ] Túnel/proxy TLS para `127.0.0.1:8090`; conferir `/healthz`, login, `/apps/<id>` e `/.well-known/oauth-authorization-server`.
- [ ] Backup antes de qualquer mudança: volume `app-hub-data` + `pg_dumpall` de `app-hub-db` e `app-hub-app-db`.
- [ ] E2E/ui-sweep só em instância temporária (`scripts/tmp-instance.sh`), nunca com contas demo em produção.

## Dimensionamento (usuários simultâneos)

Medido com `platform/loadtest/py-load.mjs` numa pilha isolada (`install.sh --project app-hub-load`), em 13/10/2026,
versão 0.2.0. Roteiro de uma pessoa real (Início, catálogo, abrir o app ativos, navegar, buscar, Inbox) com ~2 s de
pausa entre cliques; critério p95 < 300 ms e erros < 0,1%.

| Usuários simultâneos | req/s | p50 | p95 | p99 | Erros |
|---|---|---|---|---|---|
| 25 | 11,8 | 12 ms | 87 ms | 112 ms | 0% |
| 50 | 23,4 | 13 ms | 83 ms | 112 ms | 0% |
| 100 | 46,9 | 18 ms | 120 ms | 218 ms | 0% |
| 200 | 84,4 | 129 ms | 1020 ms | 1361 ms | 0% (lento) |

**Uma instância atende ~100 usuários simultâneos** (na ordem de 1.000 usuários cadastrados com uso normal ao
longo do dia). O limite é o hub rodar num único processo (um núcleo de CPU); a memória ficou em ~110 MiB.
Acima disso, a próxima medição é com mais processos/réplicas do hub. Repita a medição após mudanças no gateway,
na sessão ou no banco:

```bash
HUB=http://127.0.0.1:<porta> HUB_DEMO_PASSWORD=… node platform/loadtest/py-load.mjs --steps 25,50,100,200
```

## Podman rootless (RHEL, Rocky, Fedora)

Testado em Rocky 9 com Podman 5.8, usuário sem root/sudo (`docs/TESTE-IMPLANTACAO-RHEL-ROOTLESS.md`).

Pedir à TI (root, uma vez):
- `dnf install podman podman-docker git` e `loginctl enable-linger <usuário>` (sem linger o hub cai no logout);
- delegar CPU ao usuário — o RHEL 9 delega só `memory pids`; sem isso os limites de CPU dos apps ficam desligados:
  `/etc/systemd/system/user@.service.d/delegate.conf` com `[Service]` e `Delegate=cpu cpuset io memory pids`,
  depois `systemctl daemon-reload` e novo login do usuário.

Como o usuário:
- `systemctl --user enable --now podman.socket`;
- compose sem root: o `install.sh` oferece baixá-lo (ou rode com `--get-compose`) — binário oficial em versão fixa,
  sha256 conferido, em `~/.local/bin/docker-compose`; o `docker compose` do `podman-docker` o usa;
- `bash platform-py/scripts/install.sh …` detecta o Podman rootless e grava no `.prod.env`
  `HUB_CONTAINER_SOCKET_HOST=$XDG_RUNTIME_DIR/podman/podman.sock` e `DOCKER_GID=0`. Com `--bind` fora do loopback
  grava também `HUB_BIND6` (o Podman já ocupa o IPv6).

O hub detecta o Podman pela API: tmpfs dos apps com `U` (o Podman não aceita `uid=`) e, sem o controlador `cpu`
delegado, constrói e cria os contêineres sem limite de CPU (memória e pids seguem limitados).

## Registrar o app no Entra ID

Login com Microsoft (OIDC código + PKCE, endpoints v2). Opcional: sem configuração o hub mostra só o login local.

1. Portal do Azure › **Microsoft Entra ID › Registros de aplicativo › Novo registro**. Nome: o do hub. Tipos de conta: "somente este diretório" (um locatário) ou "qualquer diretório organizacional" (multi-locatário).
2. **URI de redirecionamento**, plataforma **Web**: `<HUB_PUBLIC_URL>/auth/entra/callback` (ex.: `https://hub.empresa.com.br/auth/entra/callback`). Precisa bater exatamente com `HUB_PUBLIC_URL`.
3. Copie **ID do aplicativo (cliente)** → `HUB_ENTRA_CLIENT_ID` e **ID do diretório (locatário)** → `HUB_ENTRA_TENANT_ID` (multi-locatário: `organizations` + `HUB_ENTRA_ALLOWED_TENANTS`).
4. **Certificados e segredos › Novo segredo do cliente**: grave o valor num arquivo fora do git (`chmod 600`) e aponte `HUB_ENTRA_CLIENT_SECRET_FILE`. Anote a validade e renove antes de vencer.
5. **Permissões de API**: `openid`, `profile`, `email` (Microsoft Graph, delegadas) — já vêm por padrão.
6. Opcional, funções por grupo: **Configuração de token › Adicionar declaração de grupos** (IDs de grupos de segurança) ou crie **Funções do aplicativo** e atribua em *Aplicativos empresariais*. Mapeie em `HUB_ENTRA_ROLE_MAP` (`{"<objectId do grupo ou valor do app role>":"Administrador"}`). Com mapeamento, as funções do usuário são sincronizadas a cada login; sem grupo mapeado, quem entra pela 1ª vez recebe `HUB_ENTRA_DEFAULT_ROLE` (padrão `Colaborador`).
7. Defina `HUB_SSO_PROVIDER=entra` e reinicie o `app-hub-web`. Confira em **Administração › Configurações › Login único (SSO)**.

Regras: o id_token é validado (assinatura pelo JWKS em cache, emissor, audiência, nonce, validade, locatário); `HUB_ENTRA_ALLOWED_DOMAINS` restringe domínios de e-mail; usuário desativado no hub não entra; o primeiro login cria o usuário (origem "Entra ID" em Usuários) ou vincula a um usuário local de mesmo e-mail. Logins SSO e recusas vão para a auditoria. O login local continua ligado (`HUB_LOCAL_LOGIN=1`) para o administrador entrar mesmo se o Entra falhar; `HUB_LOCAL_LOGIN=admins` deixa e-mail/senha só para administradores.
