# F17 — Validação pendente (2026-10-08)

Tudo rodado contra uma **instância isolada nova** (`platform-py/scripts/iso-instance.sh`), nunca contra produção.

## Instância isolada (`scripts/iso-instance.sh up|down|run <cmd>`)

- Contêineres `app-hub-iso-*` numa rede própria (`app-hub-iso-net`): hub (imagem deste checkout), banco de estado,
  banco dos apps (um banco por app), object store (bucket `fornecedores` criado) e os 4 apps com imagens deste
  checkout (`app-hub-iso-<id>`; as `app-hub-<id>` de produção nunca são reetiquetadas). Na rede eles têm os apelidos
  da pilha (`app-hub-db`, `app-hub-app-db`, `app-hub-objstore`, `app-hub-app-<id>`, `app-hub-web`), então
  `apps.json`/`app-env.json` valem sem mudança. Estado vazio, senhas de dev, usuários demo, rota hostil do E2E ligada,
  `HUB_PUBLIC_URL=http://127.0.0.1:8093` (OAuth/MCP), `HUB_CONTAINER_PREFIX=app-hub-iso-app-` (blue/green, backups e
  restaurações só tocam a instância).
- `run` exporta o ambiente do E2E (`E2E_CONTAINER_PREFIX`, `E2E_DATA_NETWORK`, `E2E_IMAGE_<APP>`, `HUB_BIN`,
  `HUB_MC_IMAGE`, `ZD_IMAGE_NAME`, `HUB_URL`…) e roda o comando em `platform/tests-e2e`.
- `tmp-instance.sh` (cópia do estado de produção, sem Docker) continua para varreduras rápidas.

## Resultados (antes → depois)

| Suíte | Antes | Depois |
|---|---|---|
| ui-sweep completo (3 papéis × claro/escuro/SO-escuro/auto, 1440/1280/390) | 11 achados fora da allowlist | **0** (`loadtest/results/2026-10-08/ui-sweep-191911`) |
| Playwright completo (sem visual) | 117 passed, 8 failed, 15 não rodaram | **140 passed, 0 failed**, 3 skipped (Rust-only 07 e 2× simulador SharePoint fora da instância) |
| `node --test platform/js-tests/` | 1 falha (onda9: `document is not defined`) | verde |
| `gen-dark-theme.mjs --check` | tokens.css desatualizado | verde |
| Conformidade (4 apps) | — | 4/4 ✅, SIGTERM saída 0 em ≈1 s (`conformance-iso/`) |
| mcp-client (SDK oficial, OAuth+PKCE, refresh, aprovação) | — | todas passaram (`mcp-client-iso.md`) |
| Zero downtime (`py-resilience.mjs zd`) | — | 2843 req, **0 não-2xx** em Aplicar ×2, importar+aprovar e rollback (`py-zd-iso.md`) |
| `uv run hub-check --all` | — | ver abaixo |

## Correções

Bugs do porte (com teste):
- `hub-ux.js`: o último bloco (abas/gaveta/multisseleção) rodava sem `document` no Node → onda9 caía. Guarda igual
  aos demais blocos.
- Imagens etiquetadas/podadas/construídas em `app-hub-app-<id>` fixo: com `HUB_CONTAINER_PREFIX` outra instância
  mexia nas fixações `keep-*` e nas tags de build de produção. `build_tag`, `Engine.pin` e `prune_pins` seguem o
  prefixo (`test_image_repos_follow_container_prefix`).
- Faixa "Você está em staging" sumia nas telas de app (o `hub.js` troca o conteúdo do `#app-root`) e ficava sob o
  workspace: a faixa é preservada e, em tela de app, vira selo flutuante no rodapé (`theme.css`; teste F19 ampliado;
  E2E 07 novo para o hub Python).
- Ativos `gen-xlsx`: gerava só "Em uso" (o Rust sorteava os 4 status) → a exportação filtrada por "Disponível" do
  E2E 03 vinha vazia. Agora cicla os status (teste em `test_units.py`).

Testes desatualizados (justificativa):
- `27-catalogo-stack`: padrão agora é Python (a pilha Rust foi arquivada); `E2E_ATIVOS_STACK=rust` para a referência.
- Título do container (`app-hub-…`) e rota hostil: eram da instância temporária antiga (prefixo `hub-` e apps de
  produção sem `HUB_DEMO_E2E_HOSTILE`); a instância isolada passa o prefixo e liga a rota → 24/14 verdes sem mudança.
- `03`: planilha grande gerada pelo próprio app ativos na pilha Python (a imagem Rust `hub-demo-apps` não existe).
- `06`: importar virou aba (`/admin/deploys?tab=import`); `09`: SLOs são aba da Saúde (`?tab=slos`); checkpoint da
  auditoria só existe com ≥ 100 eventos (instância nova); `14`: o botão "Aprovar JavaScript" fica nas ações da
  versão, fora do quadro de revisão.
- `07`: o hub Python faz staging por cookie no mesmo host (F5b) → teste novo para esse fluxo; o de host separado
  só roda na pilha Rust.
- ui-sweep: documento não-HTML (`/dev/docs/*.md`) só confere o status (as verificações num `<pre>` enorme travavam o
  renderizador e derrubavam as 4–5 rotas seguintes); allowlist do falso positivo `cor-igual-fundo` nesses `.md`.
- `conformance.sh` e `py-resilience.mjs`: nomes parametrizados (`CONF_PREFIX`/`CONF_NET`/`CONF_DB_NET`;
  `E2E_CONTAINER_PREFIX`/`E2E_APP_DB`) — antes o `conformance.sh` **parava o `app-hub-web` de produção**.

## Produção (sem mudar dados)

- **Backup real pela API: impossível sem o login do dono.** O único administrador ativo é o do dono, com troca de
  senha obrigatória pendente; o hub recusa tokens de dono nessa condição (`app.py`, `resolve` exige
  `not must_change_password`) e não há outro admin (contas demo desativadas, 0 tokens ativos). Criar um admin ou
  escrever no `auth.json` seria mudar dados — não feito. Depois da troca de senha: criar token `api:write`, `POST
  /api/v1/backups/{app}/run` por app, revogar o token.
- **"Reinício pendente"** (ativos, fornecedores, servicos, coletor): marcado pela rotação F19. Não é necessário
  funcionalmente — os contêineres já foram recriados pelo compose com as senhas novas (senha antiga recusada no
  Postgres/MinIO e apps saudáveis); "Aplicar" pelo hub só refaria o blue/green com os mesmos valores. Limpar o aviso
  também exige sessão de administrador (mesma pendência acima).

## Lacunas

- Simulador SharePoint (`sharepoint-sim`) não sobe na instância isolada → 2 testes do 11 pulados.
- `conformance.sh` avisa "não ficou saudável" ao religar o hub: o contêiner do hub não tem HEALTHCHECK (o hub volta
  normal; aviso cosmético, igual em produção).
- ui-sweep depois do E2E completo pega resíduos dos testes (ex.: 502 transitório no servicos logo após um deploy);
  rodar a varredura numa instância nova.
