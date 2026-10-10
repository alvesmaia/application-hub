# F8 - build de imagem a partir do pacote (.hubapp)

- `hub pack --dir app --build-dir ctx [--dockerfile Dockerfile]` (CLI Python): `runtime.json` = `{"build": {"dockerfile": ...}}` e o contexto vai em `build/**` (sem `.git`, `.venv`, `__pycache__`...). Sem `--build-dir` segue `--image nome@sha256:...`.
- Pipeline (`hub/ops/pipeline.py`): passo Contrato aceita `build` (Dockerfile presente, versao serve de tag); `build/**` fora das regras de front-end, mas passa pela varredura de segredos. Imagem = `hubpy-app-<id>:<versao>`.
- Implantacao (`routes.build_image`, `runtime.Engine.build`): ao aprovar (producao) ou direto (staging) o hub extrai `build/**`, constroi pelo socket Docker (so tag `hubpy-*`), com teto de memoria (`HUB_BUILD_MEMORY`, 1Gi, memswap igual), `forcerm`. Log linha a linha: job (`build_log`, mensagem, passo "Smoke test" em staging), console de logs do app e `build_log` da implantacao (API). Falha de build = implantacao "Falhou" com o motivo; ativo e trafego intocados. Depois segue o blue/green de sempre.
- Rollback: a imagem anterior e fixada com nome antes da troca (`hubpy-app-<id>:keep-<id12>`; a do compose como `baseline-<id12>`), entao nao some em `image prune`; rollback a versao construida usa a tag da versao (sem rebuild).
- Testes: `tests/test_f8_build.py` (5, Docker simulado), CLI `test_pack_build_dir`; `check.sh` verde.
- Ao vivo (`loadtest/py-build-live.mjs`): `hub pack` do ativos 2.7.0 com contexto (sdk + app, 270 KB) -> staging (build + blue/green, ok) -> producao: upload, aprovacao (build 58 linhas + blue/green) e rollback ao anterior, com carga de 581 pedidos: 0 fora de 200.

## Lacunas
- Sem limite de CPU/tempo no build (so memoria); sem `.dockerignore` no `hub pack` (so exclusoes fixas); build usa o builder classico do daemon (sem BuildKit/`--mount`).
- Imagens `keep-*`/`baseline-*`/versoes nao tem coleta automatica (sem retencao).
- Rebuild do mesmo `<id>:<versao>` reaponta a tag; versao deve subir a cada envio.
- Smoke de staging em multi-replica e UI da pagina de implantacoes nao mostram o log (so API/job/console).
