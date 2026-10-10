# F3a - Administracao: Usuarios, Funcoes, Variaveis, Conexoes

Codigo em `platform-py/hub/`: `admin/config.py` (regras de variaveis e funcoes, `AdminStore.cfg`), `admin/routes.py` (paginas `/admin/users|roles|env` e APIs `/api/admin/{users,roles,env}`, `POST /api/ops/rotate/{app}/token`), `connections/{store,routes,broker,provision}.py`, `storage/{__init__,s3,graph}.py`, templates `admin_*.html`. JS (`users.js`, `admin.js`, `connections.js`) e CSS inalterados.

- **Usuarios**: lista/cartoes mobile, criar, editar, desativar/reativar, redefinir senha, desbloquear, excluir (so desativado). Regras do Rust: nunca a propria conta, so o Administrador mexe em conta/funcao de administrador, escalonamento (funcao com permissoes que o chamador nao tem = 403), ultimo administrador ativo protegido. `UserView` sem hash. Bloqueio por tentativas vem do limitador do login (memoria ou `rate_windows`, `StateDb.rate_like`).
- **Funcoes**: CRUD, permissao por indice (salva sozinha), aplicacoes por funcao; so o Administrador altera (`superuser_only`); `app.state.roles` recarrega na hora. Sementes do mockup (4 funcoes) quando o `admin.json` ainda nao as tem.
- **Variaveis**: `admin.json` (formato do Rust: `inject_as`, `managed`, `pending_restart`); segredos e connection strings cifrados no cofre (AAD `admin.env:<nome>`), mascaramento `••••••••` + 4 finais, reveal auditado, vinculos app x ambiente, conflitos de nome no container, nomes reservados (`HUB_INTERNAL_TOKEN`, `HUB_STORAGE_*`, `HUB_CONNECTIONS`) so para variaveis geridas, geridas somente leitura (rotacionar e auditado), reinicio pendente. Sementes do mockup + `HUB_APP_ENV_FILE` (compose). **Token interno de cada app** = variavel gerida `HUB_INTERNAL_TOKEN_<APP>` no cofre (`AdminStore.tokens`; busca federada e Inbox leem de la; `HUB_INTERNAL_TOKEN_<APP>` do ambiente so semeia na partida e e o ultimo recurso).
- **Injecao**: o gateway Rust nao injeta variaveis por requisicao (so `x-hub-env: producao|staging`, que o Python ja faz); as variaveis entram no container na implantacao/reinicio (F5). Os botoes "Provisionar banco", "Aplicar (reiniciar)" e "Rotacionar senha do banco" chamam `/api/ops/*` da F5; "Rotacionar token" ja funciona.
- **Conexoes**: `connections.json` (segredo cifrado `connection:<nome>:secret`), S3/MinIO (SigV4, URL pre-assinada com os vetores do Rust, guarda SSRF) e SharePoint pelo Microsoft Graph (segredo do cliente; token em cache, site/biblioteca, upload simples e em sessao, mover, link de compartilhamento com validade conferida, teste em etapas). Vinculos viram variaveis geridas (`HUB_CONNECTIONS_<APP>_<AMB>`, `HUB_STORAGE_TOKEN_<APP>_<AMB>`, `HUB_STORAGE_URL`). **Broker** (`HUB_BROKER_ADDR`, segundo uvicorn no mesmo laco, so `/_hub/storage/*`): token gerido por app x ambiente (comparacao em tempo constante, rotacao e desvinculo valem na hora), vinculo e operacao por conexao, limite de operacoes simultaneas por app (429), `HUB_BROKER_APP_CIDRS`, usuario so como autor com a assinatura `x-hub-user-sig` valida, toda operacao auditada (detalhe do provedor so na trilha). **Provisionar armazenamento**: bucket + usuario MinIO com politica restrita + conexao, via `mc` (`HUB_S3_ADMIN_URL`, `HUB_MC_BIN`), idempotente, desfaz em falha, desprovisionar com confirmacao e (com objetos) force + senha.
- **Compose**: `hubpy-objstore` (MinIO `hub-minio:0.10.0`, 256m) + `mc` estatico copiado de `hub-mc:0.10.0` para a imagem do hub; `HUB_BROKER_*`, `HUB_S3_ADMIN_URL`, `HUB_APP_ENV_FILE`. Verificado ao vivo: provisionar, testar conexao (MinIO real, 4 etapas ok) e desprovisionar.
- **Auditoria**: toda escrita passa por `AdminStore._push_audit` + `chain.append_shared` (cadeia verifica ok nos testes). Segredos nunca em HTML, API, log, auditoria nem em claro no disco (testado).

## Paridade (`compare.mjs`, Rust :8080 x Py, claro, pt-BR; Py = hub local com `apps.compose.json`)
| rota | DOM js/cru | pixels 1440/390 |
|---|---|---|
| `/admin/users` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/admin/roles` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/admin/env` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/admin/connections` | 0/0 e 0/0 | 0.00 / 0.00 |

Com modal/menu aberto (`--click`, novo no harness; `;;` encadeia cliques): Novo usuario, Nova funcao, Nova variavel, menu Nova conexao, SharePoint e S3: todos PASS (0 estruturais, 0.00%).
Exclusoes novas em `ignore.json` (cada uma com `_why`): ids/datas dos usuarios (`data-user*`), contagens (usuarios, membros por funcao), "Ultimo acesso" (o Rust mostra sempre "agora mesmo": `relative()` chamada com argumentos trocados), contas `publicador-*` e variaveis `E2E_*` do E2E do Rust, valor mascarado do token interno, cartoes "Por aplicacao" (variaveis/reinicio pendente/botoes dependem do historico de acoes), senha provisoria sorteada no modal.
Fluxos reais no navegador (`node platform-py/parity/flows-f3a.mjs`, so hub Python): criar/editar/redefinir/desativar usuario, cartoes mobile, funcao + permissao + apps, variavel secreta (mascara, revelar), conexao SharePoint, provisionar + testar + desprovisionar MinIO; sem erros de console. 20/20.

## Qualidade
`check.sh` com Postgres descartavel: ruff, format, ty, audit ok; 164 passed; cobertura 90,2% (`check.sh` agora cobre tambem `hub.connections` e `hub.storage`). Novos: `test_admin_f3a.py` (CRUD, RBAC, escalonamento, segredos em disco/log/HTML, tokens do cofre), `test_connections.py` (CRUD, segredos, vinculos/variaveis, broker, isolamento entre apps/ambientes, rotacao, 429, CIDR, provisionamento com `mc` simulado), `test_storage.py` (vetores de caminho e presign do Rust, S3/Graph com transporte simulado), `rate_like` no banco.

## Lacunas
- SharePoint com **certificado** (JWT) nao portado: 400 com mensagem clara; so segredo do cliente.
- Simulador `hub-graphsim` nao portado (testes usam Graph falso em processo; `HUB_GRAPH_URL`/`HUB_GRAPH_LOGIN_URL` ja valem). Sem E2E Playwright dedicado nem ui-sweep completo (so os fluxos acima).
- Guarda SSRF do S3 confere IP literal e, no teste, a resolucao do nome; nao refaz a checagem a cada requisicao (o Rust usa resolvedor proprio).
- Botoes `/api/ops/*` (provisionar banco, aplicar, senha do banco) e injecao nos containers: F5. Telas de Admin ainda so em pt-BR (a pagina de Conexoes ja usa as chaves `conn.*` nos 3 idiomas).
- `docker-compose.apps.yml`/`apps.json` (F6d) sem `color/ink/icon` no `display`: as telas usam o nome como abreviacao e cor vazia ate o registro trazer o catalogo.
