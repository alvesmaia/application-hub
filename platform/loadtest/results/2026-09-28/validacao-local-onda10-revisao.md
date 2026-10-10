# Validação local — revisão de segurança da onda 10 (28/09/2026)

O CI do GitHub Actions está bloqueado (billing), então tudo rodou nesta máquina (Windows 11,
Docker Desktop). O compose subiu com `--profile sharepoint-sim` e com
`HUB_GRAPH_URL`/`HUB_GRAPH_LOGIN_URL` apontando para o simulador.

| Checagem | Resultado |
|---|---|
| `cargo fmt --all --check` · `cargo clippy --workspace --all-targets -D warnings` | ✅ · 0 avisos |
| `cargo test --workspace` | ✅ 67 suítes, 0 falhas |
| Testes novos | `hub-web --test connections::security_review_fixes_hold_end_to_end`, `broker::tests` (CIDR fecha; autor só com assinatura), `connections::tests` (token só gerido e de um app × ambiente; política de links), `hub-admin` (nomes da plataforma reservados), `hub-gateway --test proxy` (assinatura do `x-hub-user`), `hub-storage` (validade do link conferida e link apagado; erro do Graph só no detalhe; hosts de conteúdo; SSRF S3; PFX legado recusado; RFC 3339) |
| Com banco: `hub-sdk --test db` · `hub-demo-apps --test db` · `hub-ops --lib` (ignorados) | ✅ 16 · 7 · 4 (Postgres descartável `hub-testdb`, já removido) |
| Com Docker: `hub-runtime --test docker` | ✅ 3 |
| JS `node --test js-tests/` | ✅ 18 |
| Python: `ruff check` e `ruff format --check` · `mypy --strict` · `pytest` | ✅ · ✅ · sdk 91, contratos 28 |
| `cargo audit` (container `rust:1-bookworm`, volume `hub-audit-cache`) · `cargo deny check` | ✅ 0 vulnerabilidades (473 crates) · advisories/bans/licenses/sources ok |
| Conformidade `conformance/run.sh` | ✅ 4/4 |
| Playwright `npx playwright test` | ✅ **56/56**. O E2E da onda 10 agora confere, pelo caminho real (gateway → contratos → broker), que o autor do upload é o usuário verificado e não `app:contratos`. |
| Regressão visual (hub nativo `scripts/demo.sh` com dados novos) | ✅ **24/24** sem atualizar referências. O contador do sino só é mostrado quando há alerta não lido (`hub-notif-count hidden` quando não há). Com as referências passando sem mudança, a nova cor não alterou nenhuma tela de referência, e nenhuma referência precisou ser atualizada. |

O stack foi derrubado ao final (`docker compose --profile sharepoint-sim down`) e o hub nativo
foi encerrado pelo PID.
