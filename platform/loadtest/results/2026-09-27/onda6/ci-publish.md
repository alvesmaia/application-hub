# CI → hub (publicação com token de API) — 2026-09-27T11:51:08Z

Resultado: **OK**

| Passo | Resultado |
|---|---|
| Token de API (escopo servicos, staging+produção, upload+status) | criado pela API de administração |
| Imagem | hub-demo-apps:ci-3.2.9859 (`sha256:fbe34fcc3bc0656e8b6182939de92ebf64bfbb42bf8f30d5240cfb3cfa2e10a2`) |
| `hub pack` | servicos-3.2.9859.hubapp (imagem por digest) |
| `POST /api/ops/packages?env=producao&origin=ci&wait=true` (Bearer) | HTTP 201, implantação #23 |
| Aprovação (produção) | Implantado |
| Container `hub-app-servicos` (label hub.version) | 3.2.9859 |
| Gateway `/_frag/servicos/dashboard` → `x-app-version` | 3.2.9859 |
| Commit registrado | e25c697 |
