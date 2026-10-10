# F5c - OTLP, modo dev e `hub dev`

- **OTLP** (`hub/insight/otlp.py`): `OTEL_EXPORTER_OTLP_ENDPOINT` (http/https) + `HUB_TELEMETRY!=0`; OTLP/HTTP JSON sem SDK, spans a cada 5 s em `/v1/traces` (fila de 5000, devolve ao falhar), metricas cumulativas a cada 30 s em `/v1/metrics` (`hub.http.server.requests|errors|duration`); `OTEL_SERVICE_NAME`. Ganchos `listener` em `TraceStore.record`/`Metrics.observe`; iniciado/parado em `Insight.start/stop`. Pagina Observabilidade: "OTLP ligado: exportando traces e metricas para ...".
- **Perfil observability**: `docker-compose.observability.yml` (`hubpy-otel-collector` 192m, `hubpy-jaeger` 256m, UI `127.0.0.1:16696`, config `infra/otel/collector.yaml`); `scripts/dev.sh obs-up|obs-down`.
- **Modo dev** (`hub/devmode.py`, `docker-compose.dev.yml`): `HUB_DEV_MODE=1` (recusa junto de config de producao), `uvicorn --reload`, assets do disco `no-store`, SSE `/_dev/reload` (so com `HUB_DEV_RELOAD=1`; vigia `HUB_DEV_WATCH` com atraso `@ms`), `<meta name="hub-dev-reload">` no shell (mesmo JS do hub Rust), `HUB_PUBLIC_URL=${HUB_DEV_PUBLIC_URL:-http://localhost:8090}`. `scripts/dev.sh up|down|stop|status|logs|reload-on|reload-off` (`down` = pilha normal; `stop` = derruba hubpy).
- **`hub dev`** (`cli/hub_cli/dev.py` + `hub/devapp.py`): `create_app` em processo com registro de um app, dados temporarios, identidade simulada (`hub_facade`, papel `--role`), fragmentos para `--upstream`, so as variaveis de `.hub/dev.env` declaradas em `manifest.env` (`x-hub-env-*`), comando do app encerrado junto. Requer o pacote `hub` no ambiente (rodar com o venv de platform-py).

## Verificado
- `check.sh` verde: 330 passed, cobertura 89,5%; `tests/test_f5c.py` (12) + CLI 10 testes, cobertura 91%.
- Ao vivo `:8090`: obs-up -> Jaeger lista os 5 apps com traces, pagina mostra "OTLP ligado"; `dev.sh up` com reload-on -> meta + SSE `hello`, `HUB_PUBLIC_URL` do tunel; `down` volta ao normal.

## Lacunas
- `hub dev`: sem `--limits` (aviso), sem `.hub/dev.json` (marca/display), sem WebSocket do app, usa o hub completo (mais pesado que o minimo do Rust); nao rodado ao vivo contra um app real (so por teste).
- Recarga SSE ao editar arquivo nao exercitada ao vivo (montagens :ro); coberta por teste do vigia.
- Metricas OTLP agregadas por replica; sem exportar metricas de apps nem gauges.
- ui-sweep nao rodado.
