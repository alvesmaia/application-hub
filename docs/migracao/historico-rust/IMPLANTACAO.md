# Implantação — checklist para quem coloca o Application Hub em produção (pilha Rust, legado)

Este repositório entrega a aplicação **pronta** (código, imagens, compose de produção, scripts,
CI/release locais e suítes de validação). O que depende do ambiente real — a VM de destino, o
tenant do SharePoint, o IdP de SSO, cosign/scanner, as validações sob carga na máquina real — é
**responsabilidade de quem implanta** e está descrito aqui, na ordem em que deve ser feito. Nada
disto é pendência do repositório.

Convenções: comandos a partir de `platform/` (do checkout ou do `deploy-<versão>.tar.gz` do release),
como root (ou `sudo`) na VM. Os artefatos vêm do GitHub Releases (`release.sh`) ou de um build local
(`bash scripts/ci-local.sh --only images`).

---

## 1. VM de destino

| Item | Mínimo | Observação |
|---|---|---|
| vCPU | **4** | limites somam 7,75 vCPU (sobrecompromisso proposital ~1,9×; ver `docker-compose.vm-destino.yml`) |
| RAM | **8 GB** | ~6,6 GiB de limites + SO/cache do Postgres; sem swap para os containers |
| Disco | **80 GB** SSD | dados (Postgres, MinIO, `hub-data`), imagens (~1 GB), backups locais |
| Disco 2 (recomendado) | 1–5 GB | só para a série da auditoria (`/srv/application-hub-audit`), ver §5 |
| SO | Linux x86_64 — Ubuntu Server 24.04 LTS ou Debian 12 | kernel ≥ 5.15, cgroup v2 |
| Docker | Docker Engine ≥ 27 + plugin Compose ≥ 2.24 | `!override` no compose de produção exige Compose ≥ 2.24 |
| Rede | proxy TLS na VM (nginx/Caddy) → `127.0.0.1:8080` | nomes: `hub.<empresa>` e `staging.hub.<empresa>` (certificado cobrindo os dois) |
| Saída | `login.microsoftonline.com`, `graph.microsoft.com`, `*.sharepoint.com`, IdP, destino offsite | só se usar SharePoint/SSO/offsite |

- [ ] VM criada com os números acima; relógio via NTP (tokens OIDC/Graph dependem da hora).
- [ ] Docker instalado pelo repositório oficial; `docker compose version` ≥ 2.24.
- [ ] Usuário de operação no grupo `docker`; SSH só por chave.
- [ ] Firewall: entrada só 443 (e 80 → redireciona); 8080 **não** exposto (o compose de produção publica só no loopback).
- [ ] `DOCKER_GID=$(stat -c %g /var/run/docker.sock)` anotado para o `prod.env`.

## 2. Imagens e arquivos

```bash
# do release (GitHub Releases, ver scripts/release.sh):
sha256sum -c SHA256SUMS
for f in *.image.tar.gz; do gunzip -c "$f" | docker load; done
mkdir -p /opt/application-hub && tar -xzf deploy-<versão>.tar.gz -C /opt/application-hub
cd /opt/application-hub
```

- [ ] `SHA256SUMS` conferido; SBOMs (`sbom-*.cdx.json`) arquivados junto com o release.
- [ ] Imagens `hub-web`, `hub-demo-apps`, `hub-contratos`, `hub-minio`, `hub-mc` carregadas.
- [ ] Os apps de **demonstração** (`ativos`, `fornecedores`, `servicos`, `contratos`) e as senhas
      `*-demo-senha` deles existem só para validação. Em produção, substitua-os pelos apps reais
      (importados pela UI/CI) ou remova-os do catálogo depois da validação.

## 3. Segredos e configuração (`prod.env`)

```bash
install -d -m 0750 /etc/application-hub
cp prod.env.example /etc/application-hub/prod.env && chmod 600 /etc/application-hub/prod.env
openssl rand -hex 32     # → HUB_USER_SIG_KEY
openssl rand -base64 30  # → HUB_INFRA_PASSWORD (Postgres/MinIO de infraestrutura)
```

- [ ] `HUB_PUBLIC_URL=https://hub.<empresa>` (liga cookies `Secure` e o nome do host na âncora de auditoria).
- [ ] `HUB_STAGING_HOST=staging.hub.<empresa>` (DNS + certificado).
- [ ] `HUB_ADMIN_ALLOWED_CIDRS`: rede corporativa/VPN + `10.231.0.0/24` (hub-edge) + `127.0.0.1/32`. É daqui que qualquer CI (inclusive o `ci-publish.sh` rodando numa máquina da rede) chama `POST /api/ops/packages`.
- [ ] `HUB_STATE_DB_PASSWORD` (banco de estado, onda 12 — ver §15) gerado agora.
- [ ] `HUB_USER_SIG_KEY` (≥ 32 caracteres) e `HUB_INFRA_PASSWORD` gerados agora — **antes** da primeira subida (o Postgres/MinIO gravam a senha na criação do volume; trocar depois exige `ALTER ROLE`/`mc admin`).
- [ ] **Primeiro administrador — não existe conta padrão em produção** (`HUB_DEMO_USERS=0`; o `admin`/`admin` é só da pilha de demonstração). Defina `HUB_BOOTSTRAP_ADMIN_EMAIL` e escolha **uma** forma:
  - `HUB_BOOTSTRAP_ADMIN_PASSWORD` com a sua senha (passa pela política: ≥ 10 caracteres, letras + números/símbolos; senha fraca → o admin **não** é criado e o erro sai no log); tire-a do `prod.env` depois da primeira subida; ou
  - senha vazia → o hub gera uma senha aleatória **uma única vez** em `/data/bootstrap-admin.txt` (arquivo 0600; caminho no log), marcada como provisória: o primeiro login obriga a trocar (com a política) e o arquivo é apagado ao concluir.
  Só acontece com o `auth.json` vazio; depois disso as variáveis são ignoradas.
- [ ] Confira no log que **não** aparece o aviso `HUB_DEMO_USERS=1 com HUB_PUBLIC_URL=…` (usuários de demonstração expostos).
- [ ] `HUB_ALLOWED_REGISTRIES` com o registro das imagens dos apps reais.
- [ ] `DOCKER_GID` do host.
- [ ] Nenhum valor de exemplo (`TROQUE-…`) sobrou: `grep TROQUE /etc/application-hub/prod.env` vazio.

Validar sem subir:

```bash
docker compose --env-file /etc/application-hub/prod.env -f docker-compose.yml -f docker-compose.prod.yml config -q
```

O que o `docker-compose.prod.yml` muda: sem usuários de demonstração, cookies `Secure`, SSO por
ambiente, simulador do Graph impossível de subir, chave mestra como secret, âncora da auditoria em
outro disco, credenciais de infraestrutura do `prod.env`, porta só no loopback.

## 4. Chave mestra do cofre

O cofre cifra segredos (variáveis, credenciais de conexões) e **backups**. Sem `HUB_MASTER_KEY_FILE`
o hub gera `/data/master.key` **dentro do volume de dados** — um backup do volume levaria a chave
junto. Em produção a chave fica fora:

```bash
sudo bash scripts/gen-master-key.sh            # /etc/application-hub/master.key
```

- [ ] Arquivo criado com `root:10001`, modo `0440`, diretório `0750` (o hub-web roda como uid 10001 e
      lê pelo grupo 10001, somado no compose de produção; ninguém escreve). O script **recusa
      sobrescrever** uma chave existente.
- [ ] **Backup da chave, agora:** cópia em mídia offline (pendrive cifrado/cofre de senhas corporativo)
      entregue ao **responsável de segurança**, com data e a impressão que o script mostra. Guardar
      **separado** dos backups do hub (backup + chave no mesmo lugar anula a cifra). Sem a chave, segredos
      e backups cifrados **não abrem** — não há recuperação.
- [ ] Nunca no git, no `prod.env`, no volume `hub-data`, em ticket ou chat.
- [ ] Depois da subida, o log do hub-web mostra `cofre aberto (chave mestra <impressão>)` — **não** deve
      aparecer "chave mestra gerada em /data/master.key".

**Rotação da chave mestra** (anual ou quando alguém com acesso sair), com o hub parado:

```bash
docker compose … -f docker-compose.prod.yml stop hub-web
docker compose … -f docker-compose.prod.yml run --rm --no-deps --user 0 \
  -v /etc/application-hub:/keydir -e HUB_MASTER_KEY_FILE=/keydir/master.key hub-web --rotate-master-key
chown 0:10001 /etc/application-hub/master.key && chmod 0440 /etc/application-hub/master.key
docker compose … -f docker-compose.prod.yml up -d hub-web
```

A rotação troca o arquivo atomicamente (`master.key.new` → rename; o diretório precisa estar gravável
nesse momento, por isso o diretório montado em outro caminho + `--user 0`); faça o backup offline da chave **nova** e mantenha a antiga até conferir um backup
novo (os antigos continuam abrindo: as chaves de dados não mudam).

## 5. Auditoria imutável: âncora externa

A cadeia de auditoria detecta adulteração, mas um **rollback do diretório de dados inteiro** (keyring
junto) só é detectado comparando com algo fora do servidor. Sem destino configurado a tela
Governança › Auditoria mostra **"LOCAL APENAS"**: a proteção contra rollback depende só do próprio
servidor — ou seja, quem controla o host consegue voltar a trilha a um estado anterior sem que o hub
perceba. Em produção, configure os dois níveis abaixo.

**5.1 Arquivo em outro disco** (`HUB_AUDIT_HEAD_FILE`, já ligado pelo compose de produção):

```bash
mkfs.ext4 /dev/sdb && mkdir -p /srv/application-hub-audit
echo '/dev/sdb /srv/application-hub-audit ext4 defaults,nodev,nosuid,noexec 0 2' >> /etc/fstab && mount -a
touch /srv/application-hub-audit/heads.jsonl
chown 10001:10001 /srv/application-hub-audit /srv/application-hub-audit/heads.jsonl
chattr +a /srv/application-hub-audit/heads.jsonl   # só-acréscimo: nem o hub consegue reescrever
```

- [ ] `HUB_AUDIT_HEAD_DIR=/srv/application-hub-audit` no `prod.env` (ou um NFS/SMB de outra máquina).
- [ ] `chattr +a` aplicado (`lsattr` mostra `a`). Tirar o atributo exige root — registre quem pode.
- [ ] Opcional: `HUB_AUDIT_HEAD_URL` (webhook interno que recebe `POST` do documento assinado) + `HUB_AUDIT_HEAD_TOKEN`.
- [ ] Depois de ~1 min no ar, a tela de Auditoria mostra o destino e "última publicação"; `heads.jsonl` ganha uma linha por `HUB_AUDIT_HEAD_EVERY_S` (padrão 3600 s).

**5.2 Cópia diária fora do servidor** (repositório GitHub **privado**, com `gh`):

1. Crie o repositório privado (ex.: `org/application-hub-audit`) e um token *fine-grained* restrito a
   ele (Contents: read/write). Na VM, como o usuário do agendamento: `gh auth login --with-token` e
   `gh auth setup-git`.
2. Ensaio: `AUDIT_OFFSITE_REPO=org/application-hub-audit bash scripts/audit-head-offsite.sh --dry-run`.
3. Agende (systemd):

```ini
# /etc/systemd/system/hub-audit-offsite.service
[Unit]
Description=Application Hub: copia a serie da auditoria para fora do servidor
[Service]
Type=oneshot
Environment=AUDIT_OFFSITE_REPO=org/application-hub-audit
ExecStart=/opt/application-hub/scripts/audit-head-offsite.sh
# /etc/systemd/system/hub-audit-offsite.timer
[Timer]
OnCalendar=*-*-* 02:15
Persistent=true
[Install]
WantedBy=timers.target
```

`systemctl enable --now hub-audit-offsite.timer` (ou cron: `15 2 * * * AUDIT_OFFSITE_REPO=… /opt/application-hub/scripts/audit-head-offsite.sh`).
O script só **avança** a cópia: se a série do servidor não começar exatamente pela que já foi enviada
(encolheu, foi reescrita), ele **não envia** e sai 1 — trate como incidente.

**5.3 Verificação fora do servidor** (estação do responsável de segurança ou outra VM):

```bash
AUDIT_OFFSITE_REPO=org/application-hub-audit bash scripts/audit-verify-offsite.sh   # 1ª vez: anote a chave
AUDIT_OFFSITE_REPO=org/application-hub-audit AUDIT_PUBKEY=<base64> \
  AUDIT_ALERT_WEBHOOK=https://chat.interno/webhook bash scripts/audit-verify-offsite.sh
```

Roda `hub-web --audit-verify-published` (binário do release ou a imagem `hub-web`) em cada série:
assinaturas, mesma chave/keyring, `seq`/geração/piso nunca descem, mesmo `seq` ⇒ mesmo hash,
intervalos ≤ `AUDIT_MAX_GAP_S`; e confere no histórico do git que nenhum commit encolheu a série.
Sai ≠ 0 e avisa o webhook em qualquer regressão. Agende diariamente **depois** da cópia (ex.: 02:45).

- [ ] Primeira verificação feita e `AUDIT_PUBKEY` fixada no agendamento.
- [ ] Teste de alarme: altere uma cópia local e confirme o alerta.

## 6. SharePoint (Conexões) com um tenant real

Passo a passo completo na FAQ, item 21. Resumo do que fazer no Entra ID:

1. [ ] **Registro de aplicativo** (Entra ID → Registros de aplicativo → Novo registro): anote Tenant ID e Client ID.
2. [ ] **Permissão de aplicativo `Sites.Selected`** (Microsoft Graph) + **consentimento do administrador**. Não use `Sites.ReadWrite.All`.
3. [ ] **Concessão do site** ao app, por um administrador do SharePoint:
       `POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions` com
       `{"roles":["write"],"grantedToIdentities":[{"application":{"id":"<client id>","displayName":"Application Hub"}}]}`
       (Graph Explorer) ou `Grant-PnPAzureADAppSitePermission -AppId <client id> -DisplayName "Application Hub" -Site <url> -Permissions Write`.
4. [ ] **Credencial** — preferir **certificado**: `openssl req -x509 -newkey rsa:2048 -nodes -keyout chave.pem -out cert.pem -days 730 -subj "/CN=application-hub-sharepoint"`;
       suba só `cert.pem` no registro; entregue ao hub o PEM (cert + chave) ou um **PFX AES-256**
       (`openssl pkcs12 -export -inkey chave.pem -in cert.pem -out hub.pfx` no OpenSSL 3; no Windows,
       exportar com "AES256-SHA256"; PFX RC2/3DES é recusado). Segredo do cliente só se certificado
       não for possível (vence em até 24 meses — agende a rotação).
5. [ ] No hub: Aplicações › Conexões → Nova conexão SharePoint → **Testar conexão** (token, site,
       biblioteca, pasta-base, gravar/ler/apagar) todo ✓.
6. [ ] Vincular aos apps, **Aplicar**, enviar/baixar um arquivo por um app e ver a operação na Auditoria.
7. [ ] Política de links do tenant conferida (links anônimos desligados, a não ser que decidido).
8. [ ] Apagar `chave.pem`/`hub.pfx` locais depois do cadastro (a credencial fica só no cofre).

## 7. SSO (OIDC) com Entra ID

1. [ ] Registro de aplicativo (pode ser outro, separado do SharePoint), plataforma **Web**, URI de
       redirecionamento `https://hub.<empresa>/auth/sso/callback`.
2. [ ] Segredo do cliente (ou cliente público só com PKCE) e **Token configuration → Add groups claim**
       (grupos de segurança; "Group ID").
3. [ ] `prod.env`:
       `HUB_SSO_ENABLED=true`, `HUB_SSO_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0`,
       `HUB_SSO_CLIENT_ID`, `HUB_SSO_CLIENT_SECRET`, `HUB_SSO_REDIRECT_URL`,
       `HUB_SSO_ROLE_CLAIM=groups`, `HUB_SSO_ROLE_MAP=<id-grupo>=Administrador;<id-grupo>=Operador`,
       `HUB_SSO_AUTO_PROVISION=true`, `HUB_SSO_DEFAULT_ROLE=` (vazio = nega quem não está em grupo mapeado).
       O Entra ID não envia `email_verified`: o vínculo com contas locais por e-mail fica desligado,
       a menos de `HUB_SSO_TRUST_EMAIL=true` (decisão de segurança; registre).
4. [ ] Login com um usuário de cada grupo; conferir função e apps visíveis; usuário sem grupo é negado.
5. [ ] Manter **um** administrador local (senha forte, guardada no cofre corporativo) para quando o IdP cair.

## 8. Assinatura (cosign) e scan (trivy/grype) das imagens dos apps

- [ ] Instale `cosign` e `trivy` (ou `grype`) na VM e confirme que o hub-web os alcança — **modo
      binário** exige os binários dentro do container (imagem derivada de `hub-web`) ou use o modo
      container: `HUB_SCANNER=trivy` + `HUB_SCANNER_IMAGE=aquasec/trivy@sha256:<digest>` (imagem já
      carregada ou registro em `HUB_ALLOWED_REGISTRIES`).
- [ ] Chave: `cosign generate-key-pair` (a privada fica com quem publica os apps; a pública em
      `HUB_COSIGN_PUBKEY` — caminho montado ou PEM inline).
- [ ] `HUB_SCAN_FAIL_ON=CRITICAL` (ou `HIGH`) e `HUB_SCAN_MODE=block`.
- [ ] Prova: um pacote com imagem **assinada** passa em *Assinatura* e *Segurança*; um com imagem não
      assinada é recusado (a `hub-demo-apps` local não é assinada — esperado).

## 9. Validação na VM real (carga, DR, zero downtime, caos)

Os números do repositório são de Docker Desktop (indicativos). Na VM, com o compose **de
validação** (demo + perfil da VM), antes de ligar produção ou numa VM gêmea:

```bash
docker compose -f docker-compose.yml -f docker-compose.vm-destino.yml up -d --no-build
bash scripts/ci-local.sh --only ci-publish,conformance,playwright,dr,zero-downtime,chaos
node loadtest/realistic.mjs run --mem 1g --fresh                       # carga realista, rampa até o SLO
node loadtest/zero-downtime.mjs --rps 200 --deploys 3                  # versão longa
node loadtest/chaos/chaos.mjs && node loadtest/chaos/chaos-contratos.mjs
node loadtest/dr.mjs
```

| Validação | Critério de aprovação |
|---|---|
| Carga realista | p95 < 500 ms em lista e detalhe, erros < 1%, sem OOM, nos números de `loadtest/RESULTADOS.md` (ativos ≥ 75, os três juntos ≥ 150 usuários com `hub-db` 2 vCPU; contratos ≥ 75) |
| Troca sem indisponibilidade | 0 respostas não-2xx no gerador e na sonda; virada limpa de versão; POSTs idempotentes aplicados uma vez |
| DR | dados restaurados com hashes idênticos; RTO ≤ 60 s por app no porte da demo (medido: ~20 s) |
| Caos | importação completa sem duplicados; toda gravação aplicada exatamente uma vez; outbox entregue exatamente uma vez |
| Conformidade | 4/4 apps |
| Playwright | todos os cenários verdes |

- [ ] Resultados salvos em `loadtest/results/<data>/` e anexados ao registro de go-live.
- [ ] Se algum critério falhar: ajustar limites (`APP_CPUS`, `APP_MEM`, `DB_CPUS`) ou dimensionamento antes de seguir.

## 10. App Python: staging e troca sem indisponibilidade

- [ ] Provisionar o banco de staging do `contratos` (Operações › Variáveis → "Provisionar banco" em staging).
- [ ] Implantar uma versão em **staging** (pela UI, Operações › Implantações, ou `POST /api/ops/packages?env=staging`
      com o token de CI) e abrir "Ver em staging" (faixa STAGING, banco próprio).
- [ ] Troca de produção sob carga: `node loadtest/realistic.mjs run --mem 1g --apps contratos` em paralelo a
      duas implantações de produção do `contratos` → 0 não-2xx, virada limpa (`x-app-version`), tarefas em
      andamento concluídas (retomadas pelo lease).

## 11. Backups e cópia externa

- [ ] Políticas de backup por app × ambiente (Operações › Backups) com agenda diária.
- [ ] `HUB_BACKUP_OFFSITE_URL=https://chave:segredo@host/bucket/prefixo` (S3 de outro site/provedor);
      backups saem **cifrados** pelo cofre.
- [ ] "Testar restauração" da cópia externa (Operações › Backups) uma vez por mês; registrar.
- [ ] Chave mestra (§4) guardada fora — sem ela a cópia externa não abre.
- [ ] Retenção e custo do destino definidos.
- [ ] (Onda 12, 2.6) Backups com **privilégio mínimo**: na partida o hub cria/confere o papel só
      leitura `hub_bkp_<banco>` de cada banco de app e a chave só leitura `bkp-<bucket>` de cada
      bucket (log "backup com privilégio mínimo: …"). A `HUB_DB_ADMIN_URL` precisa ser superusuário
      (ou ter `ADMIN`/`SET` sobre os donos dos bancos) e a `HUB_S3_ADMIN_URL` a raiz do MinIO — só o
      hub e as tarefas de provisionamento/restauração as usam. Rotacione em Backups → "Rotacionar
      credenciais de backup" depois do go-live e periodicamente (auditado).
      Use **PostgreSQL 15+** e bancos de app provisionados pelo hub (marca `hub-managed:app=`):
      só neles o hub revoga `CREATE` no `public` e `TEMP` de `PUBLIC`. Em banco alheio o papel
      de backup fica só com `SELECT`/`USAGE` e o backup registra o aviso "endurecimento … não
      aplicado".
- [ ] (Onda 12, 2.7) Buckets de apps novos: Aplicações › Conexões → "Provisionar
      armazenamento" (bucket + usuário restrito + conexão do broker). Não crie usuários do MinIO
      à mão com a raiz.
- [ ] (Onda 12, 2.5) Apps de terceiros: SDK atualizado (rota `GET /{id}/_hub/ready`) antes da
      primeira implantação nesta versão — sem ela o candidato é recusado.

## 12. Monitoramento

- [ ] Perfil `observability` (Collector + Jaeger) ou coletor corporativo:
      `OTEL_EXPORTER_OTLP_ENDPOINT=http://<coletor>:4318` no `prod.env`.
- [ ] SLOs em Operação › Saúde e status page `/status` conferidos; alertas do sino roteados para quem opera.
- [ ] Monitor externo em `https://hub.<empresa>/healthz` (1 min).
- [ ] Disco: alerta em 80% (volumes Docker, `/srv/application-hub-audit`).
- [ ] Verificação diária da auditoria (§5.3) com alerta.

## 13. Go-live

- [ ] §1–§8 feitos; `config -q` do compose de produção passa.
- [ ] `docker compose --env-file /etc/application-hub/prod.env -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build`.
- [ ] `curl -fsS https://hub.<empresa>/healthz`; login do administrador inicial; senha provisória trocada e arquivo apagado.
- [ ] Log do hub-web: cofre aberto pela chave do secret; **sem** "LOCAL APENAS"; SSO ligado (se configurado).
- [ ] Tela de Auditoria: destino externo e última publicação recentes.
- [ ] Nenhum usuário `@demo.local` existe (Acesso › Usuários).
- [ ] §9 aprovado (na própria VM ou numa gêmea) e §10 feito.
- [ ] Primeiro backup completo executado e restaurado em teste.
- [ ] Token de API do CI criado com escopo mínimo (apps/ambientes/ações) e validade; guardado no cofre do CI.
- [ ] Plano de rollback: imagens da versão anterior carregadas (`docker load`) e o `deploy-<versão>.tar.gz` anterior guardado.
- [ ] Contatos: responsável de segurança (chave mestra, auditoria), operação (alertas), TI Microsoft (Entra ID/SharePoint).

## 14. API pública e MCP (onda 11)

**Arquivo de configuração.** O hub lê `HUB_CONFIG_FILE` (no compose de produção, o arquivo da
VM em `HUB_CONFIG_HOST_FILE`, montado só leitura em `/etc/hub/hub.toml`). Modelo comentado:
`platform/config/hub.example.toml` (todos os valores são os padrões). Chave desconhecida, tipo
errado ou valor fora da faixa **impedem a partida** (mensagem clara no log, código 2) — valide
antes de reiniciar: `docker compose run --rm --no-deps hub-web --check-config`
(sai 0 e mostra o que vale, ou 2 com o erro); o log de partida mostra `configuração …: API pública ligada · MCP
ligado em /mcp (destrutivas: ApproveInHub, ferramentas dos apps: sim)`. Variáveis de ambiente
(`HUB_API_ENABLED`, `HUB_MCP_ENABLED`, `HUB_MCP_DESTRUCTIVE_ACTIONS`, …) sobrescrevem o arquivo.

| Decisão | Onde |
|---|---|
| Desligar a API pública (`/api/v1`, OpenAPI e `/api/docs` → 404; a UI continua) | `[api] enabled = false` |
| Desligar só a documentação | `[api.docs] enabled = false` |
| Desligar o MCP (`/mcp` → 404; `hub mcp` avisa que está desligado) | `[mcp] enabled = false` ou `HUB_MCP_ENABLED=0` |
| Ações que alteram algo pelo MCP: aprovação humana na Inbox do hub (**padrão**) / confirmação só no cliente de IA / esconder | `[mcp] destructive_actions = "approve_in_hub"` / `"confirm"` / `"deny"` |
| Não publicar as ferramentas dos apps | `[mcp] expose_app_tools = false` |
| Limites (chamadas/min, tempo, tamanho do resultado, validade da confirmação) | `[mcp.limits]`, `[api] rate_per_min` |
| Aceitar app sem API (exceção) | `[apps] allow_without_api = true` |

`/api/v1` e `/mcp` fazem parte do **plano de controle**: valem `HUB_ADMIN_ALLOWED_CIDRS`
(rede corporativa/VPN) **e** o login/RBAC. Publique `/mcp` no proxy TLS como o resto do hub
(sem buffering de resposta; timeouts ≥ `tool_timeout_s`).

**Tokens para clientes de IA.** Cada pessoa cria o **próprio** token (ele age como ela, com as
permissões atuais dela — perder uma permissão tira do token também): pela API
`POST /api/me/tokens` (sessão) com `{"name": "Claude", "actions": ["mcp"], "expires_days": 30}`,
ou um administrador em Acesso › Tokens de API (marque só "MCP" e, se o assistente puder
alterar coisas, "API v1: alterações"). Recomendações: validade curta (30–90 dias), restrinja a
apps quando fizer sentido, **nunca** `secrets:reveal` para IA, revogue ao trocar de máquina.
Toda chamada de ferramenta fica na auditoria como "MCP: <ferramenta>".

**Aprovação das ações do agente (padrão `approve_in_hub`).** Toda ferramenta que altera algo
(implantar, rollback, restaurar, parar, cancelar e **toda mutação POST/PUT/PATCH/DELETE dos
apps**, salvo `mcp.confirm_exempt` revisado no manifesto) funciona em duas etapas: a 1ª
chamada cria um pedido **"Aprovar ação do agente"** na Inbox do **dono do token** — com a
ferramenta, os argumentos e o cliente (`clientInfo` do `initialize`) — e devolve um
`confirm_token`; a ação só roda quando o cliente chama de novo com ele **depois** de a pessoa
aprovar na página `/account/agent-approval/{id}` (link "Revisar e aprovar" na Inbox)
**reautenticando-se**: senha da conta ou, em conta só de SSO, um login novo no IdP
(`prompt=login`, `max_age=0`; o hub confere `auth_time` ≤ 5 min — o IdP precisa emitir essa
claim). A página mostra os argumentos completos (acima de 16 KiB o pedido nem é criado), não
tem JavaScript e não pode ser emoldurada. A reautenticação gera um token de step-up (uso
único, 120 s, amarrado a usuário + pedido + argumentos) exigido pela API de decisão: nem o
token do cliente de IA (API v1, `inbox_decide`) nem a **sessão sozinha** (um `fetch` de mesma
origem — o código dos apps roda na origem do hub) aprovam. Pedido recusado ou vencido
(`confirm_ttl_s`, padrão 300 s) nunca executa e fica "Expirado" na Inbox (varredura a cada 30 s;
na partida, todos os pendentes expiram — a aprovação em memória não sobrevive a um
reinício); no máximo 20 pedidos pendentes por pessoa e 500 no hub, com taxa por app e por
pessoa (`HUB_INBOX_APP_RATE`/`HUB_INBOX_RCPT_RATE`) e retenção própria (50 encerrados por
pessoa, 7 dias) — não despejam outros itens da Inbox. **Risco residual:** um app malicioso na
mesma origem pode imitar a página para capturar a senha; em ambientes de alta segurança use SSO
com MFA resistente a phishing (WebAuthn/passkey) no IdP ou `destructive_actions = "deny"`.
**Tokens pessoais legados sem validade** ganham vencimento em 90 dias na primeira partida desta
versão (auditado; o dono recebe um aviso na Inbox). **Elicitation do MCP** não é usada:
o hub responde só JSON (sem stream SSE), e a elicitation exige o servidor pedir algo ao
cliente no meio da chamada — o pedido na Inbox cobre todos os clientes.
- `destructive_actions = "confirm"`: a 2ª chamada com o `confirm_token` executa direto.
  **Isso depende da UI de aprovação do próprio cliente de IA** (ex.: o Claude perguntar
  "permitir esta ferramenta?"): o modelo consegue reenviar o token sem ninguém ver. Use só com
  clientes que pedem aprovação por chamada e com usuários treinados.
- Conteúdo que vem dos apps (descrições e resultados de ferramentas) chega rotulado como
  "dado não confiável" e com tamanho limitado.

**Superfícies e limites.** `/mcp` aceita **só** `Authorization: Bearer` (a sessão do navegador
recebe 401). As ferramentas **do hub** no MCP são a mesma superfície da API pública: com
`[api] enabled = false` elas somem (ficam só as dos apps). Um token restrito a apps vale para
**todas** as operações — o hub resolve o app também por ids (implantação, backup, job,
variável, conexão, item da Inbox, manifesto do pacote enviado) e a camada de Operações repete
a checagem. O limite de taxa (`rate_per_min`, `calls_per_min`) é **por pessoa** (tokens do
mesmo dono dividem a cota); tokens com dono **sempre vencem** (padrão 90 d, máximo 365 d) e
cada pessoa tem no máximo 20 ativos. Conceder `secrets:reveal` exige a permissão Gerenciar
variáveis também pela tela de Administração.

**Conectar clientes MCP.**
- **Claude Code** (HTTP direto):
  `claude mcp add --transport http application-hub https://hub.empresa.local/mcp --header "Authorization: Bearer hubt_…"`
- **Claude Desktop** e clientes que só iniciam processos locais (stdio): use a ponte da CLI
  do hub (binário `hub` do release). Em `claude_desktop_config.json`:
  ```json
  { "mcpServers": { "application-hub": {
      "command": "C:\Program Files\ApplicationHub\hub.exe",
      "args": ["mcp", "--hub", "https://hub.empresa.local"],
      "env": { "HUB_TOKEN": "hubt_…" } } } }
  ```
  (`HUB_MCP_PATH` se `[mcp].path` não for `/mcp`.) A ponte recusa `http://` fora de
  `localhost`/loopback (o token iria em texto claro) e lê o token de `HUB_TOKEN` ou de
  `--token-file`/`HUB_TOKEN_FILE`; `--token` ainda funciona, com aviso (fica visível na lista
  de processos e no histórico). O stdin é do protocolo, então o token não é lido por ele.
- **Outros clientes** (Streamable HTTP): URL `https://hub.empresa.local/mcp`, com **OAuth**
  (o cliente descobre tudo sozinho a partir do `401`) ou com o cabeçalho
  `Authorization: Bearer hubt_…` (token pessoal). Revisões aceitas: **2026-07-28** (moderna:
  sem sessão, metadados por requisição, `server/discover`) e 2025-11-25, 2025-06-18 e
  2025-03-26 (legadas: `initialize` com `Mcp-Session-Id`, `GET` SSE, `DELETE`). Respostas
  em JSON ou SSE (`tools/call` moderno em SSE com keep-alive a cada 10 s).

**OAuth do MCP (onda 12, 2.2) — o hub é o servidor de autorização.** Ligado por padrão
(`[mcp.oauth]` no `hub.toml`; desliga com `enabled = false`).

- [ ] **`HUB_PUBLIC_URL` definido** com o endereço público exato (ex.:
  `https://hub.empresa.com`, sem barra no fim): é o **emissor** (`issuer`) e a base do
  **recurso** protegido (`https://hub.empresa.com/mcp`), aos quais os tokens ficam amarrados.
  Sem ela, a descoberta e o `/oauth/*` **só respondem em `localhost`/`127.0.0.1`** (dev) — o
  hub nunca anuncia um emissor tirado de um `Host` qualquer (envenenamento de cache) e loga
  "ERRO DE CONFIGURAÇÃO" na partida. O `Origin` de navegador no `/mcp` é conferido contra
  essa URL (e `HUB_MCP_ALLOWED_ORIGINS`, lista separada por vírgula, para clientes MCP web);
  clientes de servidor (Langdock) não mandam `Origin`.
- [ ] Descoberta respondendo: `curl https://hub.empresa.com/.well-known/oauth-protected-resource/mcp`
  (`resource` + `authorization_servers`) e
  `curl https://hub.empresa.com/.well-known/oauth-authorization-server` (endpoints, `S256`,
  `registration_endpoint`). `POST /mcp` sem token → `401` com
  `WWW-Authenticate: Bearer resource_metadata="…"`.
- [ ] **Alcance de rede (CIDR × SaaS)**: por padrão `/mcp`, `/.well-known/*` e os endpoints
  de máquina do OAuth (`/oauth/token`, `/oauth/register`, `/oauth/revoke`) seguem o plano de
  controle (`HUB_ADMIN_ALLOWED_CIDRS`); a tela de consentimento (`/oauth/authorize`) é de
  pessoa (como `/account`). Um cliente de IA **na nuvem** (Langdock SaaS) chama, dos
  servidores dele, `/.well-known/oauth-protected-resource/mcp`,
  `/.well-known/oauth-authorization-server`, `/oauth/register`, `/oauth/token`,
  `/oauth/revoke` e `/mcp` — esses precisam ser alcançáveis da internet; o navegador do usuário
  abre `/oauth/authorize` (e o login). Duas opções: (a) incluir as faixas de saída do
  fornecedor em `HUB_ADMIN_ALLOWED_CIDRS` (libera também `/api` e `/admin` para elas), ou
  (b) **`[mcp].public_network = true`** (`HUB_MCP_PUBLIC_NETWORK=1`): só `/mcp`, `/.well-known/*`
  e `/oauth/*` saem da restrição por origem; `/api/*` e `/admin*` continuam restritos. Com
  (b), a proteção desses caminhos é o próprio OAuth/Bearer + limites de taxa (registro por IP
  e global, token, revogação, consentimento por usuário). Langdock dedicado/on-premises na
  rede interna dispensa as duas.
- [ ] **Tokens depois da rotação (I2)**: renovar (refresh) emite um token de acesso novo, mas o
  anterior continua válido até vencer (`access_token_ttl_s`, 1 h). Revogar a concessão (Apps
  autorizados, administração, `/oauth/revoke` com o refresh, reuso detectado) derruba **na
  hora** todos os tokens dela. Para janela menor, reduza `access_token_ttl_s`.
- [ ] Restrições opcionais: `allowed_redirect_hosts = ["app.langdock.com"]` (só esses hosts
  de retorno, além de localhost), `allowed_scopes = ["mcp"]` (agentes só leem),
  `dynamic_registration = false` (só documentos de metadados — ou nenhum cliente novo),
  `access_token_ttl_s`/`refresh_token_ttl_days`/`code_ttl_s`.
- [ ] Com **várias réplicas**, códigos, clientes, concessões e tokens ficam no banco de
  estado (`HUB_STATE_DB_URL`) — autoriza numa réplica e troca o código em outra. Sem banco de
  estado, ficam em memória (reiniciar o hub desconecta os clientes OAuth).
- [ ] Revogar: o usuário em **Minha conta › Acessos** (`/account/access`, menu do
  usuário); a administração em **Acesso › Tokens de API › Clientes OAuth do MCP**
  (revogar concessão, bloquear/desbloquear/remover cliente). Tudo vai para a auditoria
  ("OAuth: …"), inclusive reuso de refresh token (revoga a concessão e avisa o usuário).

**Adicionar o hub no Langdock** (Integrações → *Add integration* → **MCP**):

1. **URL**: `https://hub.empresa.com/mcp`; transporte **Streamable HTTP**.
2. **Autenticação — escolha uma**:
   - **OAuth 2.0 (Dynamic Client Registration)** — recomendado: o Langdock descobre os
     metadados pelo `401`, registra-se sozinho (`/oauth/register`) e abre a janela de login do
     hub; cada usuário entra (login local ou SSO), vê a tela **"Conectar um aplicativo ao hub"**
     (nome do cliente, host de retorno, permissões) e clica **Autorizar**. Pode desmarcar
     "Executar ações que alteram algo" para dar só leitura. O Langdock recebe token de 1 h +
     refresh (rotação a cada uso). Deixe o parâmetro **resource ligado** (o hub confere).
   - **OAuth 2.0 Manual** (sem registro dinâmico): registre o cliente uma vez
     (`curl -X POST https://hub.empresa.com/oauth/register -H 'content-type: application/json'
     -d '{"client_name":"Langdock","redirect_uris":["<Redirect URL mostrada pelo Langdock>"],"token_endpoint_auth_method":"client_secret_post"}'`)
     e preencha Client ID/Secret devolvidos, Authorization URL
     `https://hub.empresa.com/oauth/authorize`, Token URL `https://hub.empresa.com/oauth/token`,
     escopos `mcp api:write` (ou só `mcp`).
   - **API Key** (cabeçalho): cada usuário cria um token pessoal com o escopo `mcp` (e
     `api:write` se quiser ações) em `POST /api/me/tokens` e o Langdock envia
     `Authorization: Bearer hubt_…` (formato "Bearer"). Token compartilhado = todos agem como
     o dono dele — prefira OAuth.
3. **Salvar** e conferir a lista de ferramentas (`apps_list`, `status_get`, … e `<app>.<ferramenta>`
   dos apps que o usuário alcança). Ações destrutivas devolvem um pedido "Aprovar ação do
   agente" na Inbox do hub: o usuário aprova no hub (com senha/SSO) e pede ao agente para
   repetir. Pode-se ligar a confirmação por ferramenta também no Langdock.
4. Validação independente antes de liberar: `cd platform/loadtest/mcp-client && npm ci &&
   HUB_URL=https://hub.empresa.com HUB_USER=… HUB_PASSWORD=… node check.mjs` (SDK oficial do
   MCP faz a dança OAuth completa e chama as ferramentas).

**Apps.** Pacote sem `openapi.json` (ou com `api.enabled = false` sem a exceção) é reprovado no
passo Contrato; com `mcp.enabled`, a troca blue/green só acontece se o MCP interno do candidato
responder (`initialize` + `tools/list`). Ver `platform/docs/CONTRATO-APP.md` §23–24.

## 15. Estado compartilhado e várias réplicas do hub-web (onda 12)

**Banco de estado.** Sessões de login, limites de taxa, sessões MCP, `confirm_token`s,
tokens de step-up, tickets/sessões de staging, jobs de Operações e a liderança ficam no banco
`hub` do hub-db (`HUB_STATE_DB_URL`; o `docker-compose.prod.yml` monta a URL com
`HUB_STATE_DB_PASSWORD` do `prod.env`). Na primeira partida o hub cria o usuário `hubstate` e
o banco pela `HUB_DB_ADMIN_URL` (com `REVOKE CONNECT … FROM PUBLIC`: nenhum app entra) e aplica
as migrações. Mesmo com **uma** réplica isso vale a pena: reiniciar o hub não derruba sessões
nem vence pedidos de aprovação pendentes.

- [ ] `HUB_STATE_DB_PASSWORD` gerado (`openssl rand -hex 24`) no `prod.env` antes da primeira
  subida (só letras/dígitos/hífen — vai numa URL).
- [ ] Log da partida: "banco de estado ligado (esquema hub, réplica …) — várias réplicas
  suportadas". Se aparecer "HUB_STATE_DB_URL não definida — réplica única", **não** suba uma
  segunda réplica.
- [ ] Backup: o banco `hub` é estado de execução (sessões, pedidos pendentes, contadores); não
  precisa entrar no DR dos apps — perdê-lo desloga todo mundo e vence os pedidos pendentes,
  nada mais.

**Várias réplicas** (ex.: duas VMs ou duas instâncias atrás do balanceador corporativo):

- [ ] **Mesmo `HUB_DATA_DIR`** em todas as réplicas: o volume compartilhado guarda `admin.json`,
  `auth.json`, `ops.json`, Inbox, conexões, cofre (`keyring.json`), auditoria e backups. Na
  mesma VM, o volume Docker `hub-data`; em VMs diferentes, um sistema de arquivos compartilhado
  com `rename` atômico (NFS v4/SMB) — as gravações são serializadas pelo banco, não por
  trava de arquivo.
- [ ] **Mesma chave mestra** (`HUB_MASTER_KEY_FILE` com o mesmo arquivo em todas).
- [ ] **`HUB_USER_SIG_KEY` igual em todas** (recomendado, no `prod.env`). Sem ela, a primeira
  réplica gera uma chave e a guarda no banco de estado cifrada pelo cofre; as outras a leem.
  Valores diferentes entre réplicas quebram o broker de armazenamento e a API dos apps quando
  a requisição entra numa réplica e o app chama outra.
- [ ] Mesmos `HUB_CONFIG_FILE`, `HUB_APPS_FILE`, `HUB_ADMIN_ALLOWED_CIDRS` e mesma versão da
  imagem (troca de versão: pare todas e suba a nova — migrações do banco de estado só
  acrescentam, mas os formatos dos arquivos podem mudar entre versões).
- [ ] `HUB_REPLICA_ID` único e legível por réplica (ex.: `hub-vm1`); aparece nos logs, na
  tabela `hub.replicas` e no lease `hub.leases`.
- [ ] `HUB_RUNTIME_ATTACH` com **todos** os containers do hub separados por vírgula (as réplicas
  precisam alcançar os apps na rede de cada um). Com o runtime Docker, as réplicas precisam do
  mesmo daemon (mesma VM) — réplicas em VMs diferentes exigem o runtime remoto, ainda não
  suportado (ver PENDENCIAS).
- [ ] **Sessão grudada não é necessária** no balanceador: round-robin simples funciona. O
  balanceador precisa preservar o `Host` (o host de staging é decidido por ele), não fazer
  buffer de SSE (`/api/me/stream`, logs ao vivo) e ter timeouts longos (uploads/exportações).
  Exemplo pronto: `platform/infra/lb/nginx.conf` (usado pelo `docker-compose.replicas.yml`).
- [ ] **`HUB_TRUSTED_PROXIES`** = o(s) IP(s) do balanceador (CIDRs). Só de uma conexão vinda
  dele o hub usa o `X-Forwarded-For` (o último endereço não confiável, da direita para a
  esquerda); de qualquer outra origem o cabeçalho é ignorado. O balanceador precisa **regravar**
  (não acrescentar) `X-Forwarded-For`/`X-Real-IP` com o IP real do cliente
  (`proxy_set_header X-Forwarded-For $remote_addr;` no nginx). Assim o plano de controle
  (`HUB_ADMIN_ALLOWED_CIDRS`) e o limitador de login por IP enxergam o **cliente**, nunca o
  balanceador: pôr a rede do balanceador na lista de CIDRs **não** libera ninguém, e
  balanceador confiável sem `X-Forwarded-For` = cliente desconhecido (negado). Sem
  `HUB_TRUSTED_PROXIES`, atrás de um balanceador todo mundo parece vir dele — não faça isso.
- [ ] `hub-db` com conexões suficientes: cada réplica usa até `HUB_STATE_DB_POOL` (12) + uma por
  implantação em curso; o usuário `hubstate` nasce com `CONNECTION LIMIT 60`, então
  `réplicas × (HUB_STATE_DB_POOL + 2) ≤ 60` (4 réplicas com o padrão; mais que isso, reduza o
  pool ou `ALTER ROLE hubstate CONNECTION LIMIT …`). O compose sobe o `max_connections` do
  hub-db para 150 (dividido com os bancos dos apps).
- [ ] **Banco de estado fora do ar:** sessões de login/MCP, step-up e staging recusam; sem uma
  recarga bem-sucedida há `HUB_STATE_STALE_S` (5 s), tokens de API e mutações do plano de
  controle respondem 503 (uma revogação feita em outra réplica nunca "some" durante a queda).
- [ ] Liderança: `HUB_LEADER_TTL_S` (15 s) é o tempo máximo sem coleta do outbox/agendador/
  supervisão quando o líder morre sem aviso; num `docker stop` (SIGTERM) a troca é imediata.
  Conferir: `docker exec hub-db psql -U hubadmin -d hub -c "select * from hub.leases"`.
- [ ] Validação: `node loadtest/e2e/w12-replicas.mjs` contra as réplicas (variáveis `HUB_A`,
  `HUB_B`, `HUB_LB`) ou `bash scripts/ci-local.sh --only replicas` numa VM gêmea.

## 16. Anti-phishing: apps sem JavaScript e frase de segurança (onda 12, item 2.1)

- **Nada a ligar:** a CSP das páginas de app (só scripts do hub, Trusted Types,
  `form-action` do próprio app, `sandbox` em `/_frag`) e o sanitizador valem sempre.
  Confira depois da subida: `curl -sI https://hub/<app> | grep -i content-security-policy`
  deve conter `require-trusted-types-for 'script'` e **não** conter `/_frag/<app>/assets/`
  em `script-src` (a não ser que haja script aprovado — aí aparece só a URL exata + hash).
- **Proxy/CDN na frente do hub não pode reescrever nem remover** `Content-Security-Policy`,
  e não pode injetar scripts (analytics, "otimizadores" de página) nas respostas — com
  Trusted Types obrigatório eles quebram as páginas de app e, pior, furam a política.
- **Apps de terceiros** precisam seguir o contrato §13/§25 (sem JavaScript; `data-hub-*`).
  Pacote com JavaScript não declarado é recusado; declarado, fica em **REVISE** no passo
  Front-end e exige **Operações › Implantações › "Aprovar JavaScript"** (quem aprova fica na
  auditoria) antes de a implantação em produção poder ser aprovada. Defina quem revisa
  (segurança/arquitetura) e o critério; revise **o arquivo** e o motivo, não só o nome.
- **Separação de funções:** quem envia um pacote com JavaScript próprio (ou é dono do
  token de CI que o envia) não pode aprovar esse JavaScript — tenha ao menos dois
  administradores. Só numa instalação com um único administrador defina
  `HUB_SCRIPTS_ALLOW_SELF_APPROVAL=1` (a autoaprovação fica marcada na auditoria).
- **Cofre obrigatório para a frase de segurança** (`HUB_MASTER_KEY_FILE`; `HUB_VAULT=0`
  impede gravar a frase). Comunique aos usuários: "o hub nunca pede sua senha dentro de uma
  aplicação; defina sua frase em Conta › Frase de segurança e confira-a nas telas de
  aprovação/autorização".
- **Nunca** defina `HUB_DEMO_E2E_HOSTILE` em produção: é a rota **só de teste** dos apps de
  demonstração (fragmento hostil e scripts do E2E), compilada só com a feature `e2e-hostile`
  da imagem de demonstração; o `docker-compose.prod.yml` não tem apps de demonstração.
- Proxy/CDN não pode remover `Cross-Origin-Opener-Policy` das telas de credencial
  (`noopener-allow-popups`).
- Continue recomendando MFA resistente a phishing (passkeys/WebAuthn) no IdP do SSO.
