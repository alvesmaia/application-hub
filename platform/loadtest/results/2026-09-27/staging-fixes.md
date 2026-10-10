# Staging em origem separada — correções da onda 5 (2026-09-27T10:50:41.210Z)

Compose no ar (`HUB_STAGING_HOST=staging.localhost:8080`), navegadores simulados com cookies por host.

Resultado: **OK**

| Verificação | Resultado |
|---|---|
| link repassado a um navegador anônimo não abre o staging | ✅ passos 303 → 303 (confirmação manda ao login) · /ativos no staging → 401 |
| link repassado a outra sessão (mesmo usuário, outro navegador) não abre o staging | ✅ passos 303 → 403 · /ativos no staging → 401 |
| ticket usado em outro navegador fica invalidado (nem o dono o usa depois) | ✅ passos 403 |
| dono abre o staging (nonce + confirmação pela sessão emissora) | ✅ passos 303 → 303 → 303 · /ativos no staging → 200 |
| logout no host principal encerra a sessão de staging | ✅ logout → 303 · /ativos no staging → 401 |

```
0.1 s — ticket emitido: http://staging.localhost:8080/_staging/enter?ticket=…
0.1 s — OK  link repassado a um navegador anônimo não abre o staging — passos 303 → 303 (confirmação manda ao login) · /ativos no staging → 401
0.1 s — OK  link repassado a outra sessão (mesmo usuário, outro navegador) não abre o staging — passos 303 → 403 · /ativos no staging → 401
0.1 s — OK  ticket usado em outro navegador fica invalidado (nem o dono o usa depois) — passos 403
0.1 s — OK  dono abre o staging (nonce + confirmação pela sessão emissora) — passos 303 → 303 → 303 · /ativos no staging → 200
0.1 s — OK  logout no host principal encerra a sessão de staging — logout → 303 · /ativos no staging → 401
```
