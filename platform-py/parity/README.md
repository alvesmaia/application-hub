# Parity harness (Rust x Python)

Compara cada rota do hub Rust (:8080) com o Python (:8090): DOM normalizado (apos JS e HTML cru do servidor) + pixels.

    MSYS_NO_PATHCONV=1 node platform-py/parity/compare.mjs / /login /applications
    node platform-py/parity/compare.mjs --routes platform-py/parity/routes.json   # sem args usa routes.json

- Login `admin` com `HUB_DEMO_PASSWORD` (env ou `platform-py/.dev.env`). `/login` roda sem sessao.
- Playwright vem de `application-hub/platform/tests-e2e/node_modules` (sem instalar nada). Diff de pixel via canvas do Chromium (sem pixelmatch).
- URLs: `RUST_URL`, `PY_URL`. Regras de normalizacao/ignore: `ignore.json` (nonce, csrf, datas, horas, hashes, `[data-parity-ignore]`).
- Saida: `platform-py/dist/parity/<rota>/` = `diff.json`, `{rust,py}-{1440,390}.png`, `side-by-side-*.png` (rust | py | diff). Exit 1 se falhar.

Aceite por rota: 0 diffs estruturais (js e cru), mesmo status HTTP, <= 0.5% pixels em 1440x900 e 390x844 (claro).
Pixel = soma |dRGB| > 48 por pixel; viewport (nao full page).

Dado variavel do backend: seletores de `ignore.json` (e `ignoreText`) saem do DOM **e** dos pixels (`display:none` nos dois lados, layout igual). Screenshots com `animations: disabled` (animacao de entrada gerava ruido de ~0.5%). Cada exclusao tem justificativa em `_why`.
