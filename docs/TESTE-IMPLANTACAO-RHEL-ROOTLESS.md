# Teste de implantação do zero — RHEL-like, usuário rootless (2026-10-10)

Cenário simulado: servidor corporativo Rocky Linux 9.3 (clone do RHEL) com systemd, Podman 5.8.2 da distro
(`podman-docker` instalado), usuário de serviço `hubsvc` **sem root e sem sudo**, com linger. Fonte: clone do
repositório público `alvesmaia/application-hub` e `bash platform-py/scripts/install.sh` (o caminho documentado).
Acesso só por localhost. Depois da implantação, um agente Haiku com contexto mínimo (URL do hub + token de
desenvolvedor) tentou criar e publicar um app "Lista de compras".

| | Cenário A — 4 GB / 2 CPU | Cenário B — 8 GB / 2 CPU |
|---|---|---|
| Delegação de cgroup do usuário | padrão RHEL 9: `memory pids` | admin aplicou `Delegate=cpu cpuset io memory pids` |
| `install.sh` | 3 execuções (2 falhas, contornadas) | 1 execução (contornos já aplicados), 5 min 37 s |
| Memória da pilha no ar (4 apps demo) | 2,1 GB | 1,9 GB |
| Build de app pelo hub (`RUN`) | **falha** | ok |
| Publicação do app pelo Haiku | **falhou** (tmpfs `uid=`) | **falhou** (tmpfs `uid=`) |
| Haiku: tempo / custo | 16 min / US$ 0,57 | 5,5 min / US$ 0,05 |

Nenhum app foi publicado nos dois cenários: o bloqueio final é do produto com Podman (item P1).

## Rodada 2 — depois das correções (P1–P5)

Mesmos dois cenários recriados do zero, só com os passos do guia (docs/IMPLANTACAO.md › Podman rootless), sem editar
o compose: `install.sh` verde na primeira execução nos dois (~6,5 min, em paralelo); no A avisou da CPU não delegada.
O Haiku publicou o app em staging **na primeira tentativa** nos dois cenários (A: 3,8 min / US$ 0,03; B: 3,7 min /
US$ 0,03). Conferido no servidor: contêiner `healthy`, `/scratch` do usuário 10001 com escrita, `/_hub/ready` 200,
página `/apps/lista-compras/itens` 200 pelo hub; CPU sem limite no A (sem delegação) e 0,5 CPU no B.

Ficam abertos P6 (sem compose no RHEL base, contornado e documentado), P7, P8 e P9. Novo: a doc servida pelo hub
cita a porta de `HUB_PUBLIC_URL` (8090); atrás de um NAT de porta o leitor precisa trocar — só afeta este teste.

## Rodada 3 — itens P6–P9 e instalador interativo

Terceiro servidor limpo (`rhel-c`, 4 GB / 2 CPU, sem compose instalado), `install.sh` sem opções:
perguntou cada valor, mostrou o resumo, pediu confirmação, ofereceu baixar o `docker-compose` (sha256 conferido)
e avisou da CPU não delegada. Instalou escolhendo **não** subir os apps de exemplo — e aí apareceu o P10.

- **P10 (bloqueante, corrigido) — hub não subia sem os apps de exemplo.** A carga inicial
  (`HUB_APP_ENV_FILE=app-env.prod.json`) tem variáveis vinculadas aos apps demo; com `apps.empty.json` o catálogo
  não os tem e o boot morria em `AdminError: Aplicação desconhecida: ativos.` Agora a carga descarta vínculo de app
  fora do catálogo (arquivo da instalação, não entrada de usuário); a validação pela API segue estrita.
  Conferido: `/readyz` pronto e `/login` 200 com `HUB_APPS_JSON=./apps.empty.json`.
- **P6 resolvido:** `install.sh --get-compose` (ou a pergunta no modo interativo) baixa o binário oficial em versão
  fixa com sha256 conferido em `~/.local/bin`, sem root.
- **P7 resolvido:** o hub responde o tamanho e o limite (conferido num hub real: `O pacote tem 22.0 MB; o limite é
  20 MB…`), o `hub deploy` repassa o motivo (lia só `message`, a razão vinha em `error`) e o `hub pack` recusa antes
  de enviar apontando os maiores arquivos. Limite documentado no guia de publicação.
- **P8 resolvido:** a falha `§3 página`/`§3 fragmento` traz o status e a última exceção de `.hub/check-app.log`,
  com a dica de apagar `.hub/*.db` quando é `no such column`/`no such table`.
- **P9 resolvido:** seção «Testar sem navegador» no guia de telas, com os cabeçalhos e a chave de idempotência.

Novo recurso do instalador: modo interativo (padrões entre colchetes, resumo, confirmação); `--yes` ou qualquer
opção de instância mantém o modo silencioso do CI.
## Dificuldades — produto (rodada 1)

- **P1 (bloqueante) — tmpfs `/scratch` com `uid=10001`.** O runtime cria o contêiner do app com
  `--tmpfs /scratch:uid=10001,gid=10001`; o Podman recusa: `container create: unknown mount option "uid=10001":
  invalid mount option`. Nenhum app sobe com Podman. Caminho: detectar Podman (`/version` → "Podman Engine") e usar
  `tmpfs-mode`/`U` ou um volume anônimo com `chown` na imagem.
- **P2 (bloqueante sem admin) — build com `cpushares`/`cpusetcpus`.** `build_stream` passa limites de CPU; sem o
  controlador `cpu`/`cpuset` delegado (padrão do RHEL 9 rootless) **todo `RUN` falha** com
  `while running runtime: exit status 1` (reproduzido: `podman build --cpu-shares 512` falha, `--memory` passa).
  Caminho: documentar o `Delegate=` como pré-requisito e/ou repetir o build sem limites de CPU quando o daemon é
  rootless sem delegação, com mensagem clara.
- **P3 — socket fixo `/var/run/docker.sock` no compose.** Rootless não tem esse socket:
  `statfs /var/run/docker.sock: permission denied` na etapa 3 do `install.sh`. Contorno usado: editar o compose para
  `${XDG_RUNTIME_DIR}/podman/podman.sock`. Caminho: variável `HUB_CONTAINER_SOCKET_HOST` (padrão `/var/run/docker.sock`)
  e o `install.sh` detectar Podman rootless.
- **P4 — porta `[::1]` fixa.** Com `--bind 0.0.0.0` o `rootlessport` do Podman já ocupa o IPv6:
  `rootlessport listen tcp [::1]:8090: bind: address already in use`. Contorno: remover a linha `"[::1]:…"`.
  Caminho: só publicar `[::1]` quando `HUB_BIND=127.0.0.1`.
- **P5 — `install.sh` aponta para o repositório privado** (`--clone` usa `application-hub.git`); deve ser o público.
- **P6 — `install.sh` exige `docker compose`.** RHEL/Rocky não traz provedor de compose; `podman-compose` vem do
  EPEL (precisa de root). Contorno sem root: binário oficial do `docker-compose` em `~/.local/bin` (o
  `podman compose` o usa). Documentar em IMPLANTACAO.md uma seção "Podman rootless (RHEL)".
- **P7 — pacote acima de ~20 MB recusado com `400` sem motivo.** O limite não está documentado e a mensagem não diz
  qual é. Com dependências vendoradas o Haiku do cenário A passou disso trocando `uvicorn[standard]` por `uvicorn`.
- **P8 — `hub check` reprova conformidade "§3 fragmento com o workspace" sem causa.** A causa real (banco
  `.hub/*.db` antigo após editar a migração 0001: `no such column`) só aparece em `.hub/check-app.log`. Mostrar a
  última exceção do log ou apontar o arquivo.
- **P9 — rotas de fragmento difíceis de testar por `curl`** (405 em `/apps/…`, 403 sem cabeçalhos, 400 por chave de
  idempotência curta). As mensagens são boas; falta um exemplo de `curl` no guia.

## Dificuldades — ambiente (documentar para a TI do cliente)

- RHEL 9 rootless delega só `memory pids` → pedir ao admin
  `/etc/systemd/system/user@.service.d/delegate.conf` com `[Service]\nDelegate=cpu cpuset io memory pids` (ver P2).
- `loginctl enable-linger <usuário>` (sem isso o hub cai no logout).
- Primeira subida compila MinIO/mc do código-fonte: ~5 min com 2 CPU, CPU em 200%.
- Imagem `rockylinux:9`: `curl-minimal` conflita com `curl` no `dnf install` (não instalar `curl`).

## Artefatos do teste

Fora do repositório (scratchpad da sessão): logs `rhel/a-install*.log`, `rhel/b-install.log`, relatórios do Haiku
`haiku-rhel-a/RELATORIO.md` e `haiku-rhel-b/RELATORIO.md`. O cenário A foi removido; o B (`rhel-b`, porta
127.0.0.1:28090) ficou no ar para conferência.
