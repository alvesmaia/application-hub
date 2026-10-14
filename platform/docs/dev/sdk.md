<!-- gerado por `uv run python -m hub.ops.devdocs` — não edite à mão -->

# Referência do SDK (`hub_sdk`)

Gerada das assinaturas e docstrings do pacote `hub_sdk`. Só nomes públicos (sem `_`).

## `hub_sdk`

SDK Python (FastAPI) dos apps da Application Hub.

## `hub_sdk.api`

API do app (onda 11): OpenAPI 3.1 **nativo do FastAPI** servido no caminho do
contrato (``GET /{app}/api/openapi.json``), só com as rotas da API (``/{app}/api/...``).

### `problem(status: int, detail: str) -> JSONResponse`

Erro RFC 9457 (``application/problem+json``).

### `require_user(request: Request) -> Identity | JSONResponse`

Identidade do hub obrigatória nas rotas da API (sem ``x-hub-user`` → 401).

### `openapi_of(app: FastAPI, app_id: str, title: str, version: str) -> dict[str, Any]`

Documento OpenAPI só com as rotas ``/{app_id}/api/``.

### `install_api(app: FastAPI, app_id: str, *, title: str, version: str) -> None`

Rota ``GET /{app_id}/api/openapi.json`` (e guarda a declaração para o MCP).

## `hub_sdk.app`

Tudo ligado a um app FastAPI: :class:`HubSdk`.

### `scratch_dir(app_id: str) -> Path`

Diretório de trabalho (uploads, relatórios): ``HUB_SCRATCH_DIR`` (``/scratch`` nos
containers, volume próprio do app) ou o temporário do sistema.

### class `HubSdk`

- `from_env(app_id: str | None=None, app_sql: str='', handlers: Sequence[Handler]=(), after_migrate: Callable[[HubSdk], Awaitable[None]] | None=None, cleanup_hooks: Sequence[Callable[[HubSdk], Awaitable[None]]]=()) -> HubSdk`
- `pool() -> Any`
- `db_ready() -> bool`
- `readiness() -> _ready.Readiness` — Prontidão (``GET /{app}/_hub/ready``): encerrando = não pronto; com banco, migrações aplicadas **e** ``SELECT 1``; depois as verificações de ``ready_checks``.
- `wake() -> None`
- `async emit(user: str, action: str, target: str='', detail: str='') -> None` — Auditoria fora de transação (exportações, consultas).
- `event(user: str, action: str, target: str='', detail: str='') -> Event`
- `async lifespan(_app: FastAPI) -> AsyncGenerator[None]`
- `async run_worker_process() -> None` — Processo de tarefas (``HUB_JOBS_MODE=process``): só o worker de tarefas e a limpeza periódica, até SIGTERM/SIGINT; drena como o web (8 s).
- `async run_cleanup() -> None`
- `install(app: FastAPI) -> None` — Middleware de idempotência (todas as rotas que alteram dados), endpoints internos ``/{id}/_hub/*`` e o cabeçalho ``x-app-version``.

## `hub_sdk.approvals`

Aprovações pela Inbox do hub (onda 9).

### `valid_id(s: str) -> bool`

### class `ApprovalDecision`

- `status() -> str`

### class `NotFound`

### class `AlreadyDecided`

### class `Forbidden`

## `hub_sdk.dashboards`

Kit de dashboards (BI leve) dos apps: datasets SQL, filtros, KPIs, gráficos SVG no servidor e exportação.

### class `Param`

Filtro do dashboard. ``kind``: ``select`` (``options`` = [(valor, rótulo)]), ``text``, ``date``,
``number``. O valor vem da query string, validado aqui (nunca SQL cru: vai como parâmetro ``:nome``).

- `parse(raw: str | None) -> str | int | float`

### class `Dataset`

Consulta nomeada: ``sql`` com parâmetros ``:nome`` (só os listados em ``params``), contra uma conexão
SQL vinculada (``source`` = nome), uma :class:`hub_sdk.sql.SqlConnection` ou uma função que a devolve.
``ttl`` = segundos de cache por combinação de parâmetros (0 = sem cache).

- `connection() -> SqlConnection`

### class `Dashboard`

- `values(query: Mapping[str, str]) -> dict[str, Any]` — Valores validados dos filtros a partir da query string.
- `dataset(name: str) -> Dataset`
- `async load(name: str, values: Mapping[str, Any] | None=None, conn: SqlConnection | None=None) -> SqlResult` — Executa (ou devolve do cache) o dataset ``name`` com os valores dos seus parâmetros.
- `clear() -> None`

### `fmt(v: Any, unit: str='', places: int | None=None) -> str`

### `compact(v: float) -> str`

### `kpi(label: str, value: Any, unit: str='', hint: str='', delta: float | None=None) -> Markup`

Cartão de indicador. ``delta`` (fração, ex. 0.12 = +12%) vira seta e cor (subiu/caiu).

### `kpis(items: Iterable[Markup]) -> Markup`

### `chart(kind: str, labels: Sequence[Any], series: Series, title: str='', unit: str='') -> Markup`

Gráfico SVG: ``kind`` = ``line`` | ``area`` | ``bar`` | ``pie`` | ``donut``. ``series`` = [(nome, valores)].

### `line_chart(r: SqlResult, x: str, y: Sequence[str] | str, title: str='', unit: str='') -> Markup`

### `area_chart(r: SqlResult, x: str, y: Sequence[str] | str, title: str='', unit: str='') -> Markup`

### `bar_chart(r: SqlResult, x: str, y: Sequence[str] | str, title: str='', unit: str='') -> Markup`

### `pie_chart(r: SqlResult, label: str, value: str, title: str='', unit: str='') -> Markup`

### `donut_chart(r: SqlResult, label: str, value: str, title: str='', unit: str='') -> Markup`

### `table(r: SqlResult, columns: Sequence[tuple[str, str]] | None=None, table_id: str='hub-dash-table', page_size: int=20, filter_label: str='Filtrar linhas', units: Mapping[str, str] | None=None) -> Markup`

Tabela com filtro e paginação do hub (``data-hub-filter``/``data-hub-table``). ``columns`` =
[(coluna, rótulo)] (padrão: todas).

### `filters_form(params: Sequence[Param], values: Mapping[str, Any], action: str, submit: str='Aplicar') -> Markup`

Filtros como ``<form method="get">`` (sem JS): cada mudança é aplicada pelo botão.

### `export_links(href: str, values: Mapping[str, Any]) -> Markup`

Links «Exportar CSV/XLSX» para a rota de exportação do app (``?format=csv|xlsx`` + filtros).

### `to_csv(r: SqlResult) -> bytes`

### `to_xlsx(r: SqlResult, sheet: str='Dados') -> bytes`

XLSX em modo write-only (``openpyxl``; extra ``hub-sdk[dashboards]``). Texto com cara de fórmula é
neutralizado como no CSV.

### `export_response(r: SqlResult, fmt_: str, filename: str) -> Response`

Resposta de download (``csv`` ou ``xlsx``) com ``Content-Disposition`` e ``no-store``.

## `hub_sdk.db`

Pool do Postgres (asyncpg) com os codecs que o SDK espera e migração em segundo plano.

### `app_env(app_id: str, name: str) -> str | None`

``NOME_<ID>`` com fallback para ``NOME`` (vazio = ausente).

### `async create_pool(url: str, size: int) -> Any`

Pool preguiçoso (``min_size=0``): o app sobe mesmo com o banco fora do ar.

### `pool_size(var: str, default: int, per_web_process: bool=False) -> int`

``var`` do ambiente; senão ``default`` — dividido pelos processos web
(``UVICORN_WORKERS``) quando ``per_web_process``.

### class `Database`

Pools + estado "pronto" (migrações aplicadas).

- `ready() -> bool`
- `async open(role: str='web', jobs_inline: bool=True) -> None` — ``role="web"``: handlers + idempotência + tarefas (2 se as tarefas rodam em outro processo); ``role="worker"``: só handlers (4) e tarefas (5).
- `spawn_migrations(app_id: str, app_sql: str, after: Any=None) -> None` — Migra em segundo plano até conseguir (não segura a partida nem o health check); ``after`` (corrotina opcional) roda depois — ex.: carga inicial.
- `async close() -> None`

### `database_url(default: str='sqlite:///.hub/dev.db') -> str`

URL SQLAlchemy do banco do app: ``DATABASE_URL`` > ``HUB_DB_URL`` > ``default``
(``postgres(ql)://`` vira ``postgresql+psycopg://``, o driver do extra ``sql``).

### `engine(url: str | None=None) -> Any`

Engine síncrona do SQLAlchemy Core. Em SQLite cria a pasta do arquivo.

### `upgrade(migrations_dir: str | os.PathLike[str], url: str | None=None, revision: str='head') -> None`

Aplica as migrações Alembic de ``migrations_dir`` (pasta com ``env.py`` e ``versions/``).

## `hub_sdk.flags`

Feature flags (onda 8) — mesmo algoritmo do crate ``hub-flags`` (vetores compartilhados
em ``tests/vectors/flags-vectors.json``).

### `valid_token(s: str) -> bool`

### `user_digest(key: str, user: str) -> str`

Resumo do usuário nas regras empurradas pelo hub (o app não recebe e-mails).

### `bucket(key: str, user: str) -> int`

Balde estável 0–9999 de ``chave:usuário`` (sha256, 4 primeiros bytes big-endian).

### `parse_header(v: str) -> dict[str, str]`

### class `Flags`

- `is_on(key: str) -> bool`
- `variant(key: str) -> str | None`

### `from_headers(headers: Mapping[str, str]) -> Flags`

### `evaluate(flag: Mapping[str, Any], env: str, user: str, roles: Sequence[str]) -> str`

Valor de ``flag`` no ambiente para o usuário (ordem: kill switch →
regras → percentual → padrão).

### class `FlagStore`

Definições empurradas pelo hub (cache local; vazio até o primeiro envio).

- `replace(version: int, env: str, flags: list[dict[str, Any]]) -> bool`
- `evaluate(key: str, user: str, roles: Sequence[str]) -> str`
- `is_on(key: str, user: str, roles: Sequence[str]) -> bool`

### `validate_push(body: Any) -> tuple[int, str, list[dict[str, Any]]] | None`

Valida o corpo de ``POST /_hub/flags`` (``None`` = inválido).

## `hub_sdk.heavy`

Limite de operações pesadas simultâneas por app (importação, exportação, relatório).

### class `Permit`

Vaga ocupada; ``release()`` (ou sair do ``with``) devolve. Liberar duas vezes é
inócuo.

- `release() -> None`

### class `Heavy`

- `from_env() -> Heavy`
- `try_acquire() -> Permit | None` — Vaga sem esperar; ``None`` = limite atingido (responda 429).
- `in_use() -> int`

### class `PermitStreamingResponse`

``StreamingResponse`` que **sempre** devolve a vaga de operação pesada:

## `hub_sdk.idempotency`

``Idempotency-Key`` para rotas que alteram dados (POST/PUT/PATCH/DELETE) — middleware
ASGI que usa a tabela ``hub_idempotency`` (formato estável: não mude sem migração).

### `max_body_from_env() -> int`

Teto do corpo de pedidos com ``Idempotency-Key`` (``HUB_MAX_BODY_MB``, padrão 64).

### `valid_key(k: str) -> bool`

8 a 128 caracteres visíveis ASCII.

### `split_content_type(ct: str) -> tuple[str, str | None]`

### class `RequestHasher`

Hash incremental do pedido.

- `update(data: bytes) -> None`
- `finish() -> str`

### `request_hash(method: str, path: str, query: str, content_type: str, body: bytes) -> str`

### `storable(status: int) -> bool`

2xx/3xx/4xx (determinísticas), exceto 408/429; nunca 5xx.

### class `IdemCtx`

Chave em curso (``request.state.hub_idem``): o handler grava a marca de
efetivação **na própria transação** de negócio.

- `async mark_committed(conn: Any) -> None`

### `idem_ctx(conn: HTTPConnection) -> IdemCtx | None`

Contexto da chave da requisição (``None`` sem ``Idempotency-Key``).

### class `Idempotency`

Backend: Postgres (``pool`` dedicado) ou memória (``pool=None``).

- `postgres(pool: Any) -> Idempotency`
- `memory() -> Idempotency`

### class `IdempotencyMiddleware`

``app.add_middleware(IdempotencyMiddleware, idem=Idempotency.postgres(pool))``.

- `idem() -> Idempotency`

## `hub_sdk.identity`

Identidade injetada pelo hub no cabeçalho ``x-hub-user``.

### class `Identity`

- `display_name() -> str` — Nome para exibir (`name`, senão o login).
- `is_guest() -> bool`
- `has_permission(p: str) -> bool`
- `encode() -> str` — Valor do cabeçalho (para testes e para o `hub dev`).

### `parse_identity(value: str | None) -> Identity | None`

Decodifica o cabeçalho; ``None`` se ausente ou inválido.

### `identity_of(conn: HTTPConnection) -> Identity`

Identidade da requisição (convidado se ausente/inválida). Use como dependência
do FastAPI: ``ident: Identity = Depends(identity_of)``.

### `user_key(headers_value: str | None) -> str`

Valor cru do cabeçalho, aparado — é o escopo das chaves de idempotência (a chave de outro usuário nunca devolve a resposta deste).

### `env_of(conn: HTTPConnection) -> str`

Ambiente que o hub declarou (`producao`/`staging`); padrão produção.

## `hub_sdk.jobs`

Tarefas duráveis na tabela ``hub_jobs`` (tabela, SQL e estados têm formato estável: não mude sem migração).

### `label(status: str) -> str`

### class `JobSpec`

Contrato de um tipo de tarefa (espelha ``manifest.jobs``).

### `backoff(attempt: int, base: float, max_: float, jitter: float) -> float`

Espera antes da tentativa ``attempt + 1``: ``base · 2^(attempt-1)`` limitada a
``max_``, metade fixa + metade aleatória (``jitter`` ∈ [0, 1)).

### class `Job`

- `to_json() -> dict[str, Any]` — JSON do ``GET /_hub/jobs`` (sem o ``attempt_token``, que nunca sai do app).
- `percent() -> int`

### `job_of(r: Any) -> Job`

### class `JobError`

Resultado de uma tentativa que não concluiu.

### class `JobRetry`

Falha transitória: nova tentativa com backoff (ou ``dead`` se esgotou).

### class `JobFail`

Falha definitiva (entrada inválida etc.): ``failed``, sem novas tentativas.

### class `JobCancelled`

Cancelamento pedido.

### class `JobLostLease`

Outro worker assumiu (lease vencido): não mexe em mais nada.

### class `JobShutdown`

Desligamento gracioso: devolve à fila sem gastar a tentativa.

### class `Handler`

Implementação de um tipo de tarefa.

- `async run(ctx: JobCtx) -> Any`
- `final_event(job: Job, status: str, detail: str) -> outbox.Event | None`

### `extra_events(handler: Handler | None, job: Job, status: str, detail: str) -> list[outbox.Event]`

Eventos extras do estado final (onda 9: ex. notificação para a Inbox do hub), se o
handler definir ``final_events(job, status, detail)``; gravados na mesma transação.

### `async enqueue_with(conn: Any, spec: JobSpec, payload: Any, user: str, idempotency_key: str | None=None) -> tuple[Job, bool]`

Enfileira com ``conn`` (ex.: dentro da transação que também grava a auditoria).
Com ``idempotency_key``, a mesma chave (e tipo) devolve a existente (``False``).

### `async after_final(handler: Handler | None, job: Job, status: str) -> None`

Gancho opcional ``handler.after_final(job, status)`` (síncrono ou ``async``),
chamado **depois** que o estado terminal foi confirmado (ex.: apagar o upload). Erros
só vão para o log.

### class `Jobs`

- `handler_for(kind: str) -> Handler | None`
- `take_markers(killed_pid: int | None) -> builtins.list[str]` — Lê e apaga as marcas de fase de memória deixadas por processos anteriores; devolve os ids das tarefas cuja marca é do processo ``killed_pid`` (morto por SIGKILL — ex.: OOM do cgroup). Marcas de outros PIDs (container reiniciado, caos) só são apagadas: essas tarefas retomam normalmente do checkpoint.
- `async enqueue(spec: JobSpec, payload: Any, user: str, idempotency_key: str | None=None) -> tuple[Job, bool]`
- `async get(job_id: str) -> Job | None`
- `async list(limit: int) -> builtins.list[Job]`
- `async claim(specs: Sequence[JobSpec]) -> Job | None`
- `async reap(kinds: Sequence[str]) -> builtins.list[Job]` — ``running`` com lease vencido que não serão retomadas: cancelamento pedido → ``cancelled``; sem tentativas → ``dead``. O evento final de cada uma (do handler registrado em ``handlers``) é gravado no outbox **na mesma transação** da mudança de estado. Devolve as que mudaram (o chamador roda :func:`after_final`).
- `async fail_killed(job_ids: Sequence[str], owner_prefix: str, reason: str) -> builtins.list[Job]` — ``failed`` (sem novas tentativas) para as tarefas ``running`` de ``job_ids`` cujo lease ainda é do processo morto (``lease_owner`` começa com ``owner_prefix``, ex. ``"1234-"``), com o evento final na mesma transação. Para quando o processo de tarefas foi morto (SIGKILL/OOM) no meio de uma fase de memória: repetir a tentativa só mataria o processo de novo.
- `async cancel(job_id: str) -> Job`
- `async retry(job_id: str) -> Job`
- `async heartbeat(job_id: str, token: str, lease: float) -> None`
- `async finish(spec: JobSpec, handler: Handler, job: Job, outcome: Any, error: JobError | None) -> None` — Grava o resultado da tentativa (estado + evento final na mesma transação); num estado terminal, chama :func:`after_final` depois de confirmar.

### class `JobCtx`

Contexto de uma tentativa em execução.

- `pool() -> Any`
- `checkpoint() -> Any` — Checkpoint gravado na última confirmação (retomada).
- `memory_phase() -> Generator[None]` — Marca (arquivo em ``Jobs.marker_dir``) enquanto a tentativa segura muita memória (ex.: a aba inteira carregada pelo python-calamine). Se o processo de tarefas for morto por SIGKILL (OOM) com a marca presente, o próximo processo marca a tarefa ``failed`` em vez de repeti-la (:meth:`Jobs.fail_killed`).
- `should_stop() -> JobError | None` — Motivo para parar agora (lease perdido, cancelamento, desligamento).
- `raise_if_stopping() -> None`
- `async save_checkpoint(conn: Any, checkpoint: Any, done: int, total: int, text: str='') -> None` — Grava checkpoint + progresso (e renova o lease) com ``conn`` — a transação do lote. Levanta :class:`JobLostLease`/:class:`JobCancelled` (a transação deve ser descartada — sair do ``async with conn.transaction()`` com a exceção faz isso).
- `async progress(done: int, total: int, text: str='') -> None` — Progresso sem checkpoint (fora de transação).

### `async run_claimed(jobs: Jobs, handler: Handler, job: Job, shutdown: Shutdown) -> None`

Executa uma tentativa já assumida: heartbeat em paralelo, tempo máximo, resultado.

### class `Worker`

Assume tarefas dos tipos dos ``handlers`` (uma vaga de :class:`Heavy` por
tarefa), executa em paralelo, retoma leases vencidos e marca ``dead`` o que esgotou.

- `wake() -> None`
- `spawn() -> asyncio.Task[None]`
- `async run() -> None`

## `hub_sdk.locale`

Idioma e formatação regional dos fragmentos (onda 9).

### `parse(s: str | None) -> str | None`

Código suportado para um código/prefixo (``pt``, ``pt-PT``, ``en-US``…) ou ``None``.

### `from_accept_language(v: str | None) -> str | None`

Melhor idioma suportado de um ``Accept-Language`` (respeita os pesos ``q``).

### `from_headers(headers: Mapping[str, str]) -> str`

``x-hub-locale`` → ``locale`` do ``x-hub-user`` → ``Accept-Language`` → pt-BR.

### `current() -> str`

Idioma da requisição em curso.

### `use(code: str) -> contextvars.Token[str]`

Fixa o idioma (testes, tarefas); devolve o token para ``reset``.

### `register(catalog: Mapping[str, Mapping[str, str]]) -> None`

Acrescenta traduções: ``{"texto pt-BR": {"en": "..."}}``.

### `t(text: str, **kw: Any) -> str`

Tradução de ``text`` (pt-BR) no idioma atual, com ``{marcadores}`` preenchidos.

### `number(v: float, places: int=0, code: str | None=None) -> str`

### `thousands(n: int) -> str`

### `money_cents(cents: int | None) -> str`

Centavos → ``R$ 1.234,50`` (pt-BR) ou ``BRL 1,234.50`` (en).

### `date(d: dt.date | None) -> str`

Data curta: ``28/09/2026`` (pt-BR) ou ``09/28/2026`` (en).

### class `LocaleMiddleware`

Middleware ASGI: fixa o idioma de cada requisição (ContextVar) a partir dos
cabeçalhos do hub.

## `hub_sdk.mcp`

MCP interno do app (onda 11): ferramentas **geradas do OpenAPI** do app em
``POST /{app}/_hub/mcp`` (JSON-RPC 2.0, MCP 2025-06-18) — só para o hub, pela rede
interna, com ``x-hub-internal-token`` (o gateway recusa ``/_hub/*`` do navegador).

### class `ToolDef`

### `tools_from_openapi(doc: dict[str, Any], app_id: str) -> list[ToolDef]`

### `async asgi_call(app: Any, method: str, path: str, query: str, headers: list[tuple[bytes, bytes]], body: bytes) -> tuple[int, bytes]`

Requisição HTTP em processo contra uma aplicação ASGI (sem rede, sem httpx).

### `router(app_id: str, token: str | None, app: Callable[[], Any], doc: Callable[[], dict[str, Any]], version: str='1') -> APIRouter`

``POST /{app_id}/_hub/mcp``. ``app``/``doc``: o app ASGI completo e o OpenAPI
(resolvidos na primeira chamada).

## `hub_sdk.outbox`

Outbox transacional (``hub_outbox``).

### class `Event`

### class `Stored`

Evento como o hub o recebe (JSON do ``GET /_hub/outbox``).

- `to_json() -> dict[str, Any]`

### `audit(user: str, action: str, target: str='', detail: str='') -> Event`

Evento de auditoria (usuário ≤ 120, ação ≤ 80, alvo ≤ 200, detalhe ≤ 500; sem
caracteres de controle).

### `notification(user: str, title: str, body: str='', link: str='', to_users: list[str] | tuple[str, ...]=(), to_roles: list[str] | tuple[str, ...]=(), level: str='info') -> Event`

Notificação para a Inbox do hub. ``link`` = caminho do próprio app (``/{id}/…``);
``to_users`` (logins) / ``to_roles`` (funções) vazios → só quem causou (``user``).

### `approval_request(user: str, approval_id: str, title: str, body: str='', link: str='', to_users: list[str] | tuple[str, ...]=(), to_roles: list[str] | tuple[str, ...]=(), level: str='warning') -> Event`

Pedido de aprovação: a decisão volta em ``POST /{app}/_hub/approvals/{approval_id}``
(ver :mod:`hub_sdk.approvals`).

### `async write(conn: Any, ev: Event) -> str`

Grava ``ev`` com ``conn`` — normalmente a transação da ação. Devolve o ``event_id``.

### class `Outbox`

Outbox no Postgres (``pool``) ou em memória (``pool=None``).

- `postgres(pool: Any) -> Outbox`
- `memory() -> Outbox`
- `durable() -> bool`
- `async emit(ev: Event) -> str | None` — Publica fora de uma transação (ações sem banco: exportação, consulta). Falha só vai para o log.
- `async pending(after: int, limit: int) -> list[Stored]` — Eventos não confirmados com ``id > after``, em ordem.
- `async ack(event_ids: list[str]) -> int` — Confirma; devolve quantos passaram a confirmados (repetir é inócuo).

## `hub_sdk.ready`

Prontidão e vivacidade (onda 12, item 2.5 — docs/CONTRATO-APP.md §2).

### class `NotReady`

Motivo legível de "não pronto".

### class `Readiness`

- `check(name: str, fn: Check) -> Readiness`
- `flag(name: str, reason: str, fn: Callable[[], bool]) -> Readiness`
- `async evaluate() -> tuple[bool, dict[str, Any]]`

### `router(app_id: str, readiness: Readiness | Callable[[], Readiness]) -> APIRouter`

## `hub_sdk.realtime`

Tempo real (SSE ou WebSocket): acorda as conexões abertas quando os dados mudam.

### class `Canal`

- `async iniciar() -> None` — No lifespan do app. Com Postgres, escuta o aviso de qualquer réplica.
- `async fechar() -> None`
- `avisar(engine: Any=None) -> None` — Chame depois do commit (vale em rotas ``def`` e ``async def``). ``engine``: o SQLAlchemy do app; no Postgres o aviso vai por ``pg_notify`` (todas as réplicas).
- `async mensagens(mensagem: Callable[[], str], ping: float=20.0) -> AsyncGenerator[str | None, None]` — O estado atual na conexão e depois a cada aviso; ``None`` = nada novo em ``ping`` segundos.

### `sse(mensagens: AsyncIterator[str | None]) -> StreamingResponse`

Resposta ``text/event-stream``: cada mensagem vira ``data:``; ``None`` vira ping (mantém a conexão).

## `hub_sdk.render`

Fragmentos HTML com o visual do shell (Jinja2) e respostas comuns.

### `icon(name: str) -> Markup`

### class `NavItem`

### class `AppInfo`

- `has_section(s: str) -> bool`

### `html(body: str, status: int=200, headers: dict[str, str] | None=None) -> HTMLResponse`

### class `Renderer`

Ambiente Jinja2 do app: templates do app (``templates_dir``) + os do SDK
(``workspace.html``, ``skeleton.html``, ``notice.html``).

- `render(template: str, **ctx: Any) -> str`
- `page(conn: HTTPConnection, template: str, active: str, status: int=200, **ctx: Any) -> HTMLResponse` — Página do app (workspace completo).
- `fragment(template: str, status: int=200, **ctx: Any) -> HTMLResponse` — Pedaço de página (painel que substitui ``data-hub-target``).
- `notice(kind: str, title: str, text: str) -> Markup`
- `skeleton() -> HTMLResponse`
- `not_found() -> HTMLResponse`

### `busy(what: str, queued: str, retry_after: int=10) -> Response`

429 de operação pesada (limite do app atingido) com ``Retry-After``: o ``hub.js``
mostra ``queued`` ("Relatório em fila…") e tenta de novo sozinho.

### `download_headers(content_type: str, filename: str) -> dict[str, str]`

``Content-Disposition: attachment`` + ``nosniff`` + ``no-store``.

### `thousands(n: int) -> str`

12345678 → "12.345.678".

### `money_cents(cents: int | None) -> str`

### `csv_field(s: str) -> str`

Campo CSV (``;``, Excel pt-BR) com neutralização de fórmula (OWASP): valores que
começam com ``= + - @`` (mesmo atrás de TAB/CR) ganham ``'``.

### `csv_line(fields: Iterable[str]) -> str`

## `hub_sdk.saga`

Sagas (onda 12, item 2.9).

### `new_state(names: Sequence[str]) -> dict[str, Any]`

### `next_action(s: dict[str, Any]) -> str`

Próxima ação a partir do estado salvo: ``run:i``, ``compensate:i``, ``complete``,
``compensated`` ou ``failed``.

### `state_of(checkpoint: Any, names: Sequence[str]) -> dict[str, Any]`

Estado da saga no checkpoint (ou um novo). Recusa definição alterada em andamento.

### `summary(checkpoint: Any) -> dict[str, Any] | None`

Resumo para o hub: ``{state, steps: [{name, state}], reason}``.

### class `StepCtx`

Contexto de um passo.

- `async once(fn: Callable[[Any], Awaitable[Any]]) -> Any` — Efeito no banco **exatamente uma vez**: ``fn(conn)`` roda numa transação que também grava ``hub_saga_once(key)``; numa retomada devolve a saída gravada.

### class `Step`

### class `Saga`

Definição de uma saga (um tipo de tarefa). Registre como qualquer handler.

- `step(name: str, action: StepFn, compensate: StepFn | None=None) -> Saga`
- `final_event(job: Any, status: str, detail: str) -> outbox.Event | None`
- `async run(ctx: JobCtx) -> Any`

## `hub_sdk.schema`

Tabelas do SDK, ids e utilidades de banco.

### `base(app_id: str) -> str`

Base pública do app (``/apps/{id}``): prefixo de todo link do app no HTML.

### `new_id(prefix: str) -> str`

Identificador aleatório de 128 bits em hex com prefixo (`evt-…`, `job-…`).

### `ct_eq(a: str | bytes, b: str | bytes) -> bool`

Comparação em tempo constante (tokens).

### `async migrate(conn: Any, app_sql: str='') -> None`

Aplica as migrações do app + :data:`SCHEMA_SQL` sob advisory lock.

### `async cleanup(pool: Any) -> tuple[int, int]`

Apaga idempotência vencida e outbox confirmado há mais de 7 dias.

## `hub_sdk.scripts`

Onda 12 (2.1) · JavaScript próprio do app — **exceção** declarada no manifesto.

### `declare(file: str, reason: str) -> dict[str, str]`

Entrada de ``manifest.scripts`` para ``ui/assets/<file>`` (mesmas regras do hub).

### `integrity(data: bytes) -> str`

Integridade SRI/CSP (``sha384-<base64>``) — a mesma que o hub calcula no import.

## `hub_sdk.search`

Busca federada (onda 9): ``GET /{app}/_hub/search?q=&limit=``.

### class `SearchQuery`

### class `SearchHit`

- `to_json() -> dict[str, Any]`

### `url_in_app(app: str, url: str) -> bool`

### `sanitize(app: str, hits: list[SearchHit], limit: int) -> list[SearchHit]`

### `fold(s: str) -> str`

Minúsculas sem acentos.

### `score(text: str, q: str) -> float`

Igual 1, começa 0,8, palavra começa 0,6, contém 0,4, senão 0.

### `like_pattern(q: str) -> str`

Padrão ``ILIKE`` seguro (escapa ``%``, ``_`` e ``\``).

## `hub_sdk.shutdown`

Desligamento gracioso (SIGTERM do ``docker stop``/redeploy, Ctrl+C).

### `on_process_shutdown(fn: Callable[[], None]) -> None`

### class `InFlight`

- `done() -> None`

### class `Shutdown`

- `trigger() -> None`
- `triggered() -> bool`
- `async wait() -> None` — Resolve quando o desligamento é disparado.
- `track() -> InFlight`
- `inflight() -> int`
- `async drain(timeout: float) -> bool` — Espera o trabalho em curso terminar (até ``timeout`` s); ``True`` se drenou.
- `chain_signals() -> None` — Encadeia SIGTERM/SIGINT: dispara este desligamento e chama o tratador anterior (o do uvicorn). Chamar no lifespan de entrada (depois que o uvicorn instalou os dele). Fora da thread principal não faz nada.

## `hub_sdk.sql`

Consultas SQL a conexões da plataforma (PostgreSQL, MySQL/MariaDB, SQL Server) — mesma API no hub e no dev.

### class `SqlResult`

- `dicts() -> list[dict[str, Any]]`
- `column(name: str) -> list[Any]`

### class `SqlConnection`

Uma conexão SQL (use :func:`connection` ou :func:`from_engine`).

- `for_user(x_hub_user: str | None, x_hub_user_sig: str | None=None) -> SqlConnection` — Registra no hub o usuário da requisição como autor da consulta (com a assinatura do gateway).
- `async query(sql: str, params: dict[str, Any] | None=None, max_rows: int | None=None) -> SqlResult`

### class `BrokerSql`

- `async query(sql: str, params: dict[str, Any] | None=None, max_rows: int | None=None) -> SqlResult`

### class `EngineSql`

SQLAlchemy (banco local do dev ou o banco do próprio app).

- `async query(sql: str, params: dict[str, Any] | None=None, max_rows: int | None=None) -> SqlResult`

### `from_engine(engine: Any, name: str='app', readonly: bool=True, max_rows: int=5000) -> SqlConnection`

O banco do próprio app (``hub_sdk.db.engine()``) com a interface de consultas dos dashboards.

### `local_connections(path: str | os.PathLike[str] | None=None) -> dict[str, dict[str, Any]]`

``[connections.<nome>]`` de ``.hub/connections.toml`` (``HUB_SQL_LOCAL_FILE``).

### `connection(name: str) -> SqlConnection | None`

Conexão SQL ``name``: local (``HUB_SQL_LOCAL_FILE``) no dev; pelo broker se vinculada no hub.

## `hub_sdk.stack`

Onda 13 · stack do app no manifesto (``manifest.stack``, opcional e informativo).

### `declare(language: str, *, framework: str | None=None, sdk: str | None=None, db: str | None=None, libs: Sequence[str]=()) -> dict[str, Any]`

Entrada de ``manifest.stack`` (levanta ``ValueError`` fora das regras do hub).

## `hub_sdk.storage`

Armazenamento de arquivos do app (onda 10, contrato item 22): conexões da plataforma pelo **broker do hub** (SharePoint, S3) ou o
bucket próprio do app (**S3 direto**, ``HUB_S3_*``). Trocar de backend é configuração
(vínculo em Administração › Conexões), não código.

### class `StorageError`

Falha de armazenamento: ``status`` HTTP (400 caminho, 403 operação não liberada,
404 inexistente ou conexão não vinculada, 409, 413, 429/503 transitório, 502 provedor).

- `transient() -> bool`

### class `Item`

- `of(d: dict[str, Any]) -> Item`

### class `ShareLink`

### `clean_path(p: str, file: bool=True) -> str`

Normaliza um caminho relativo à pasta-base (mesmas regras do hub): sem ``..``/``.``,
``\``, ``%``, ``:*?"<>|#``, controle, sósias Unicode de ``.``/``/``; segmentos sem
espaço no início nem espaço/ponto no fim; ≤ 255 bytes por segmento, ≤ 32 níveis,
≤ 1024 bytes no total.

### class `Download`

Corpo em streaming: ``async for chunk in d.chunks()``; ``aclose()`` libera a conexão.

- `async chunks() -> AsyncIterator[bytes]`
- `async read() -> bytes`
- `async aclose() -> None`

### class `Connection`

Uma conexão de armazenamento (use :func:`connection` ou :func:`s3_direct`).

- `via_broker() -> bool`
- `for_user(x_hub_user: str | None, x_hub_user_sig: str | None=None) -> Connection` — Mesma conexão, registrando no hub o usuário da requisição.
- `async upload(path: str, data: Body, content_type: str, length: int | None=None) -> Item`
- `async download(path: str) -> Download`
- `async list(prefix: str='') -> builtins.list[Item]`
- `async meta(path: str) -> Item`
- `async delete(path: str) -> None`
- `async move(src: str, dst: str) -> Item`
- `async share_link(path: str, kind: str='view', expires_hours: int=24) -> ShareLink`

### class `BrokerConnection`

Pelo broker do hub (rede interna do app, token por app × ambiente).

- `via_broker() -> bool`
- `async upload(path: str, data: Body, content_type: str, length: int | None=None) -> Item`
- `async download(path: str) -> Download`
- `async list(prefix: str='') -> builtins.list[Item]`
- `async meta(path: str) -> Item`
- `async delete(path: str) -> None`
- `async move(src: str, dst: str) -> Item`
- `async share_link(path: str, kind: str='view', expires_hours: int=24) -> ShareLink`

### class `S3Config`

### class `S3Connection`

Bucket do app (S3/MinIO) direto, SigV4 com ``UNSIGNED-PAYLOAD``.

- `async upload(path: str, data: Body, content_type: str, length: int | None=None) -> Item`
- `async download(path: str) -> Download`
- `async meta(path: str) -> Item`
- `async list(prefix: str='') -> builtins.list[Item]`
- `async delete(path: str) -> None`
- `async move(src: str, dst: str) -> Item`
- `async share_link(path: str, kind: str='view', expires_hours: int=24) -> ShareLink`
- `presign_get(key: str, secs: int, now: dt.datetime) -> str` — URL pré-assinada de leitura.
- `presign_put_path(path: str, secs: int, now: dt.datetime | None=None) -> str` — URL pré-assinada de envio (``PUT``) para ``path`` (relativo à pasta-base) — envios em duas fases (onda 12, 2.9).
- `presign(method: str, key: str, secs: int, now: dt.datetime) -> str` — Assinatura SigV4 por query string (``GET``/``PUT``).

### `bound() -> list[tuple[str, str]]`

Conexões vinculadas a este app × ambiente (``HUB_CONNECTIONS`` = ``nome=tipo,…``).

### class `LocalConnection`

Pasta local com a mesma API (``hub dev``: ``HUB_STORAGE_LOCAL_DIR``). Sem links de compartilhamento.

- `async upload(path: str, data: Body, content_type: str, length: int | None=None) -> Item`
- `async download(path: str) -> Download`
- `async list(prefix: str='') -> builtins.list[Item]`
- `async meta(path: str) -> Item`
- `async delete(path: str) -> None`
- `async move(src: str, dst: str) -> Item`

### `connection(name: str) -> Connection | None`

Conexão ``name`` pelo broker do hub, se vinculada a este app × ambiente
(no ``hub dev``, ``HUB_STORAGE_LOCAL_DIR`` = pasta local com a mesma API).

### `s3_direct(name: str, cfg: S3Config) -> Connection`

Bucket próprio do app (S3/MinIO direto).

## `hub_sdk.telemetry`

Traces do app (onda 8).

### class `TraceContext`

- `parse(v: str | None) -> TraceContext | None`
- `root() -> TraceContext`
- `child() -> TraceContext`
- `header() -> str`

### `now_us() -> int`

### class `Telemetry`

- `set_gauges(f: Callable[[], dict[str, float]]) -> None`
- `gauges() -> dict[str, float]`
- `record(span: dict[str, Any]) -> None`
- `pending(after: int, limit: int) -> tuple[list[dict[str, Any]], int]`
- `span_dict(ctx: TraceContext, parent: str | None, name: str, kind: str, start: int, dur: int, error: bool, attrs: dict[str, str]) -> dict[str, Any]`

### `current() -> TraceContext | None`

### `sql_comment() -> str`

### `async span(name: str, kind: str='internal', attrs: dict[str, str] | None=None) -> AsyncGenerator[None]`

### `db(op: str) -> contextlib.AbstractAsyncContextManager[None]`

Span ``client`` de banco (``db.system=postgresql``).

### `redact_target(path: str) -> str`

``http.target`` seguro (mesmo algoritmo de ``hub_telemetry::redact::target``):
sem query/fragmento; ids, e-mails, tokens e segmentos após palavras sensíveis
trocados por marcadores.

### class `TraceMiddleware`

Span ``server`` por requisição (filho do ``traceparent`` do hub).

## `hub_sdk.uploads`

Envio de arquivos em **duas fases** (onda 12, item 2.9), com tabelas (``hub_uploads``) e tipo de tarefa de
promoção (``hub.upload.promote``).

### `valid_name(n: str) -> bool`

### `clean_final(p: str) -> str`

Caminho final normalizado e fora do prefixo de staging (sem diferenciar caixa).

### `promote_spec() -> JobSpec`

### class `UploadSession`

### class `Committed`

### class `CleanupReport`

### class `Uploads`

- `async begin(file_name: str, user: str) -> UploadSession` — Fase 1: registra o envio **em nome de** ``user`` e devolve a chave de staging. A URL pré-assinada vale no máximo :data:`PUT_URL_TTL` (e nunca além da validade do envio).
- `async put(s: UploadSession, data: bytes, content_type: str) -> None` — Envia o conteúdo para a chave de staging (pelo broker/app).
- `async commit(conn: Any, upload_id: str, final_path: str, user: str) -> Committed` — Fase 2 (na transação ``conn`` do app): confirma o envio **de** ``user`` (o do ``begin``, nesta conexão), registra tamanho e ETag do staging (a promoção só aceita essa versão) e enfileira a promoção. Falha se o envio não existe, é de outro usuário/conexão, venceu, já foi confirmado ou passa do tamanho máximo.
- `async location(upload_id: str) -> str | None` — Onde o arquivo confirmado está agora (final depois da promoção; staging antes).
- `async cancel(upload_id: str) -> None` — Desfaz um envio (compensação) sob a trava da linha — serializado com a promoção: apaga o staging e, se já promovido, o destino, e marca ``cancelled``. Idempotente.
- `async promote(upload_id: str) -> None` — Promove (idempotente) sob a trava da linha: confere a versão confirmada (tamanho/ETag), não sobrescreve destino existente, move staging → final e confere.
- `promote_handler() -> PromoteHandler`
- `async cleanup() -> CleanupReport` — Apaga envios vencidos (objeto + ``expired``) e órfãos do prefixo de staging.
- `spawn_cleanup(every: float, shutdown: Shutdown) -> asyncio.Task[None]`

### class `PromoteHandler`

Tarefa ``hub.upload.promote`` (registre junto com os outros handlers).

- `async run(ctx: JobCtx) -> Any`
- `final_event(job: Any, status: str, detail: str) -> None`
