# OOM + backoff de reinício — correções da onda 5 (2026-09-27T10:50:14.392Z)

`ativos` com 256 MiB aplicados pelo **caminho da UI** (Containers › Limites + "Aplicar", admission control com a
capacidade do engine: 16 vCPU · 23.3 GiB), carga `_load?mem_mb=200&hold_ms=2000` × 6 em paralelo,
supervisão com os padrões (OOM → degradado na 1ª queda; espera 5 s → 10 s → 20 s … teto 5 min; estável 60 s → recuperado).
Depois, **com o app ainda degradado**, limite de volta a 1 GiB + "Aplicar" (container recriado): a supervisão devolve a
policy `no` ao container novo e só declara recuperado (alerta no sino) depois de 60 s estável.

Resultado: **OK** — 4 OOM(s), restart policy `no` durante o laço, `no` no container
recriado enquanto degradado e `unless-stopped` depois da recuperação; alerta de recuperação: "Gestão de Ativos recuperado";
intervalos entre reinícios: 1.4 s, 11.4 s, 12.0 s, 17.8 s.

```
2.2 s — capacidade do host no admission control: 16 vCPU · 23.3 GiB (CPU do engine · memória do engine)
2.2 s — variável HUB_DEMO_LOAD=1 no ativos: HTTP 201
4.2 s — limite 256 MiB pela API (admission control): HTTP 200 salvo; aplicado ao container hub-app-ativos
5.3 s — Aplicar (recria com variável e limite): HTTP 200 {"action":"apply","container":"hub-app-ativos","detail":"Gestão de Ativos · Produção: ok · hub-app-ativos recriado(s) com a mesma imagem, sem indisponibilidade 
5.4 s — container hub-app-ativos (recriado pelo Aplicar): memória 256 MiB
8.5 s — Containers: Degradado — sem memória (OOM); ação: Aumente a memória do container em Containers › Limites (ou reduza o consumo do app).
8.6 s — restart policy durante o laço: no
8.6 s — Saúde: Degradado — sem memória (OOM) — Aumente a memória do container em Containers › Limites (ou reduza o consumo do app).
8.6 s — sino: 1 alerta(s) novo(s): Gestão de Ativos degradado: sem memória (OOM)
44.7 s — carga parada; respostas 502 amigáveis (data-hub-unavailable) vistas pelo cliente: 161
46.9 s — limite 1 GiB pela API: HTTP 200
47.5 s — Aplicar (redeploy com o app degradado): HTTP 200 {"action":"apply","container":"hub-app-ativos","detail":"Gestão de Ativos · Produção: ok · hub-app-ativos recriado(s) com a mesma imagem, sem indisponibilidade 
47.6 s — container recriado: 25d511338cfd → ec2fa115b671
51.8 s — depois do redeploy: restart policy no · supervisão: Degradado
111.6 s — recuperado: true · alerta de recuperação: Gestão de Ativos recuperado · policy unless-stopped · ativos/dashboard HTTP 200
111.6 s — eventos: 4 oom, 5 starts; intervalos entre starts (s): 1.4, 11.4, 12.0, 17.8
112.5 s — desfeito (variável removida, 1 GiB, Aplicar HTTP 200)
```
