# Política de segurança

## Como reportar uma vulnerabilidade

**Não abra issue pública.** Use o reporte privado do GitHub: aba **Security › Report a vulnerability**
(GitHub private vulnerability reporting) deste repositório. Inclua passos para reproduzir, versão/commit
afetado e impacto esperado. Respondemos assim que possível; a correção e a divulgação são coordenadas
com quem reportou.

## Versões suportadas

| Branch | Suporte |
|---|---|
| `stack/rust-topcoat` (código em `platform/`) | sim — recebe correções |
| `main` (mockup estático) | não — só documentação e protótipo |
| tags/releases antigas | não |

## Escopo

- Dentro: o hub (`platform-py/hub`), o SDK (`platform/sdk-python`), a `hub-cli` (`platform-py/cli`),
  o gateway, a autenticação/RBAC, o MCP/OAuth, o cofre e os scripts de implantação.
- Fora: as credenciais e os apps **de demonstração** (`docker-compose.yml`, `HUB_DEMO_USERS=1`,
  senhas `*-demo-*` — são públicas de propósito), dependências de terceiros (reporte ao projeto
  original) e limitações já listadas em `docs/PENDENCIAS.md` ("Limitações conhecidas").

Não há programa de recompensa (bug bounty).

---

## Security policy (English)

Please **do not open public issues** for vulnerabilities. Report privately through GitHub's
**Security › Report a vulnerability** (private vulnerability reporting) on this repository, with
reproduction steps, affected commit and impact. Supported branch: `stack/rust-topcoat` only
(`main` is a static mockup). Demo credentials and demo apps are public by design and out of scope,
as are third-party dependencies and the known limitations in `docs/PENDENCIAS.md`. There is no bug bounty.
