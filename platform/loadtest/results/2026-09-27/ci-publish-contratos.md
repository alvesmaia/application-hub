# CI → hub (publicação com token de API) — 2026-09-27T19:41:19Z

Resultado: **OK**

| Passo | Resultado |
|---|---|
| Token de API (escopo contratos, staging+produção, upload+status) | criado pela API de administração |
| Imagem | hub-contratos:ci-1.1.701 (`sha256:75141e076fdd7a19ee6c9b18954008a38bdbfc604cb667f88c13de62e1e1c7f9`) |
| `hub pack` | contratos-1.1.701.hubapp (imagem por digest) |
| `POST /api/ops/packages?env=producao&origin=ci&wait=true` (Bearer) | HTTP 201, implantação #38 |
| Aprovação (produção) | Implantado — Container hub-app-contratos substituído sem indisponibilidade (blue/green) · imagem sha256:75141e076fdd · health check ok · upstream http://hub-app-contratos:8080 · anterior drenado em 0 ms |
| Container `hub-app-contratos` (label hub.version) | 1.1.701 |
| Gateway `/_frag/contratos/dashboard` → `x-app-version` | 1.1.701 |
| Commit registrado | a6eaa71 |
