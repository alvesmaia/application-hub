# F-graphsim - Simulador do SharePoint e certificado de cliente

Fecha as duas primeiras lacunas da F3a.

## Simulador `platform-py/graphsim/`
Porte do `hub-graphsim` do Rust (Starlette, so memoria, nunca em producao): token por segredo ou por asserção RS256 do certificado configurado (`x5t`, `aud`, `exp`, `nbf`), site/bibliotecas, item, exclusao, mover/renomear, download (302 para URL pre-autenticada), upload simples (<= 4 MiB) e em sessao (pedacos de 320 KiB, sem `Authorization`), listagem paginada, pasta, `createLink` (com `link_expiry` ignore/extend) e exclusao de permissao, `403` com `deny_site`, limitacao (`throttle_every/next` + `Retry-After`), `fail_rate`, latencia, `/_sim/{config,stats,reset,health}` e `/_health`. Variaveis `GRAPHSIM_*` iguais as do Rust. `python -m graphsim [--health]`.

Compose: `platform-py/docker-compose.graphsim.yml` (arquivo novo), perfil `sharepoint-sim`, container `hubpy-graphsim` (128m, ~25 MB em uso), porta `127.0.0.1:18080`; ao subir junto com o hub, aponta `HUB_GRAPH_URL`/`HUB_GRAPH_LOGIN_URL` do `hubpy-web` para ele:
`docker compose -p hubpy -f docker-compose.yml -f docker-compose.graphsim.yml --profile sharepoint-sim up -d --build`.
Para certificado: `POST /_sim/config {"cert_pem": "<PEM do certificado>"}` ou `GRAPHSIM_CERT_PEM_FILE` (com `GRAPHSIM_CERT_DIR` montado em `/certs`).

## Certificado de cliente (hub)
- `hub/storage/cert.py`: `ClientCert` (PEM com chave PKCS#8/PKCS#1 sem senha, ou PFX com senha; algoritmo legado de PFX recusado), confere chave x certificado, RSA 2048-8192, impressao digital SHA-1, validade, CN; forma guardada `pkcs8-b64.cert-b64`; asserção JWT RS256 (RFC 7523, `x5t`/`x5t#S256`, 10 min) e `verify_assertion`.
- `GraphClient` usa a asserção no lugar do segredo quando a conexao tem certificado; mensagem de erro de credencial cobre "segredo ou certificado".
- `connections/store.py`: `auth=certificate` com `certificate_b64` + `certificate_password` (limite 64 KB, certificado vencido recusado), troca segredo<->certificado (exige o arquivo ao trocar), rotacao do certificado (`POST .../rotate` com `certificate_b64`), `make_backend` monta o cliente com o certificado. A visao da API/tela continua so com impressao digital, assunto e validade.
- Chave e certificado cifrados no cofre (AAD `connection:<nome>:certificate`, ja existia); testado: nunca em HTML, API, auditoria nem em claro no disco; auditoria da rotacao diz "certificado".

## Testes
`tests/test_graphsim.py` (17): CRUD/cache de token, upload em sessao em 3 pedacos, paginacao, link com validade ignorada pelo tenant, passos do "Testar", erros de token, limitacao + 401 com renovacao, controle `/_sim`, config por ambiente, falha aleatoria/arvore, parse PEM/PKCS#1/PFX/ida e volta, entradas invalidas, asserção adulterada, certificado correto/errado/ausente no simulador, conexoes pela API do hub contra um simulador HTTP real (segredo e certificado, vazamentos, rotacao, troca de modo). `tests/test_storage.py`: o `FakeGraph` foi removido (substituido pelo simulador); ficou so o mapeamento de erros do token com `MockTransport`. `check.sh` agora cobre `graphsim` (cobertura e `ty`).

## Verificado ao vivo
Contra o container `hubpy-graphsim`: conexao com segredo e conexao com certificado, ambas "Testar" com as 7 etapas ok (token, site, drive, folder, write, read, delete); `/_sim/stats` mostrou 1 asserção e 2 tokens emitidos.

## Lacunas
- PFX com algoritmo legado (3DES/RC2) e chave com senha no PEM: recusados (o Rust aceita o PFX legado).
- Sem E2E Playwright nem ui-sweep para o formulario de certificado (o JS/HTML do formulario e o mesmo do Rust, que ja envia `certificate_b64`/`certificate_password`; nao verificado no navegador aqui).
- `hubpy-web` do compose principal nao foi recriado com `HUB_GRAPH_*` (o hub em uso por outros agentes); a verificacao usou o hub em processo contra o container.
