"""Conformidade com ``platform/docs/CONTRATO-APP.md`` (seções entre colchetes).

Cada teste verifica um ponto do contrato só por HTTP (e, com ``CONF_DB_URL``, o banco do
app); nada aqui conhece a linguagem do app.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest

from conftest import Cfg, ident

SEEN: list[httpx.Response] = []


def keep(r: httpx.Response) -> httpx.Response:
    SEEN.append(r)
    return r


INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>", re.I)
EVENT_ATTR = re.compile(r"<[^>]+\son[a-z]+\s*=", re.I)
EVENT_ID = re.compile(r"^[\x21-\x7e]{1,80}$")


def is_fragment(r: httpx.Response) -> None:
    assert r.status_code == 200, r.status_code
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    low = body[:2000].lower()
    assert "<html" not in low and "<body" not in low and "<!doctype" not in low, "fragmento, não documento"
    assert "no-store" in r.headers.get("cache-control", ""), "fragmento dinâmico com cache-control: no-store"
    assert not INLINE_SCRIPT.search(body), "script inline (CSP do hub proíbe)"
    assert not EVENT_ATTR.search(body), "atributo on*= (CSP do hub proíbe)"


# ---- [2] health ----------------------------------------------------------------------
def test_health_is_get_app_root_200(cfg: Cfg, http: httpx.Client) -> None:
    t = time.monotonic()
    r = keep(http.get(f"/{cfg.app}"))
    assert r.status_code == 200
    assert time.monotonic() - t < 5


# ---- [3] fragmentos, skeleton, assets ------------------------------------------------------
def test_sections_are_fragments_with_the_workspace(cfg: Cfg, http: httpx.Client) -> None:
    for s in cfg.profile["sections"]:
        r = keep(http.get(f"/{cfg.app}/{s}"))
        is_fragment(r)
        assert re.search(r'class="app-sidebar[" ]', r.text) and "workspace-content" in r.text, s
        assert f'href="/apps/{cfg.app}/' in r.text, f"{s}: links do app em /apps/{cfg.app}/…"


# ---- [1] URLs públicas: /apps/{id} (onda 13) ----------------------------------------------
def test_never_emits_unprefixed_self_links(cfg: Cfg, http: httpx.Client) -> None:
    """O navegador vê o app em ``/apps/{id}/…`` (o hub traduz para ``/{id}/…`` no
    upstream). Nenhum link, formulário ou comportamento ``data-hub-*`` aponta para o
    caminho antigo ``/{id}…`` (downloads diretos pelo gateway, ``/_frag/{id}/…``, valem)."""
    old = re.compile(
        r'\s(?:href|action|formaction|src|data-hub-[a-z-]+)="/' + re.escape(cfg.app) + r'(?:[/?#"])',
        re.I,
    )
    for s in cfg.profile["sections"]:
        r = keep(http.get(f"/{cfg.app}/{s}"))
        hit = old.search(r.text)
        assert hit is None, f"{s}: link sem /apps ({hit.group(0) if hit else ''!r}) — use a base x-hub-base"


def test_unknown_section_is_404_and_other_prefixes_are_not_served(cfg: Cfg, http: httpx.Client) -> None:
    assert keep(http.get(f"/{cfg.app}/secao-que-nao-existe-{cfg.uniq}")).status_code == 404
    assert keep(http.get("/outro-app/dashboard")).status_code == 404


def test_skeleton(cfg: Cfg, http: httpx.Client) -> None:
    r = keep(http.get(f"/{cfg.app}/_skeleton"))
    assert r.status_code == 200 and "skeleton" in r.text


def test_assets_closed_list_with_types(cfg: Cfg, http: httpx.Client) -> None:
    for name, mime in (
        ("demo.css", "text/css"),
        ("app.css", "text/css"),
    ):
        r = keep(http.get(f"/{cfg.app}/assets/{name}"))
        assert r.status_code == 200, name
        assert mime in r.headers["content-type"], (name, r.headers["content-type"])
    assert keep(http.get(f"/{cfg.app}/assets/nao-existe.js")).status_code == 404
    for bad in ("..%2f..%2fetc%2fpasswd", "%2e%2e%2fmanifest.json", "..\\..\\x"):
        assert keep(http.get(f"/{cfg.app}/assets/{bad}")).status_code == 404, bad


def test_no_app_javascript_in_fragments(cfg: Cfg, http: httpx.Client) -> None:
    """Onda 12 (2.1): o app não roda JavaScript próprio. Fragmentos sem ``<script>``
    (exceto blocos de dados ``application/json``), sem atributos de evento (``on…=``) e
    sem URLs ``javascript:`` — o hub os remove de qualquer forma (sanitizador + CSP), então
    um app que depende deles quebra; comportamentos vêm dos atributos ``data-hub-*``."""
    bad = re.compile(r"<script(?![^>]*type=[\"']application/json)|\son[a-z]+\s*=|javascript:", re.I)
    for s in cfg.profile["sections"]:
        r = keep(http.get(f"/{cfg.app}/{s}"))
        hit = bad.search(r.text)
        assert hit is None, f"{s}: JavaScript no fragmento ({hit.group(0) if hit else ''!r}) — use data-hub-*"


# ---- [4] identidade -----------------------------------------------------------------------
def test_identity_is_used_and_escaped_and_invalid_is_guest(cfg: Cfg, http: httpx.Client) -> None:
    page = f"/{cfg.app}/{cfg.profile['sections'][0]}"
    r = keep(http.get(page))
    assert "Conformidade &lt;Zé&gt;" in r.text, "nome do x-hub-user exibido e escapado"
    assert "Conformidade <Zé>" not in r.text
    for bad in ("nao-e-base64!!", ident("", "sem login"), ""):
        r = keep(http.get(page, headers={"x-hub-user": bad}))
        assert r.status_code == 200 and "convidado" in r.text, bad


# ---- [5] endpoints internos ------------------------------------------------------------------
def test_internal_endpoints_require_the_token(cfg: Cfg, http: httpx.Client) -> None:
    base = f"/{cfg.app}/_hub"
    for path, method in (
        ("/outbox", "GET"),
        ("/outbox/ack", "POST"),
        ("/jobs", "GET"),
        ("/jobs/x/retry", "POST"),
        ("/telemetry", "GET"),
        ("/flags", "POST"),
        ("/search?q=x", "GET"),
        ("/approvals/x-1", "POST"),
    ):
        r = keep(http.request(method, base + path, json={"ids": []} if method == "POST" else None))
        assert r.status_code in (401, 403), (path, r.status_code)
        r = keep(
            http.request(
                method,
                base + path,
                headers={"x-hub-internal-token": "token-errado-" + "x" * 20},
                json={"ids": []} if method == "POST" else None,
            )
        )
        assert r.status_code == 401, (path, r.status_code)


# ---- [19] busca federada (onda 9) ---------------------------------------------------------------
def _ident_full(user: str, perms: list[str], locale: str = "") -> str:
    d: dict[str, Any] = {"user": user, "roles": [], "name": "Conformidade", "permissions": perms}
    if locale:
        d["locale"] = locale
    import base64
    import json

    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def test_search_contract(cfg: Cfg, internal: httpx.Client) -> None:
    s = cfg.profile.get("search")
    if not s:
        pytest.skip("perfil sem busca")
    base = f"/{cfg.app}/_hub/search"
    h = {"x-hub-user": _ident_full("conf@x", ["acessar_aplicacoes"]), "x-hub-locale": "pt-BR"}
    r = keep(internal.get(base, params={"q": s["q"], "limit": "1000"}, headers=h))
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert isinstance(res, list) and len(res) <= 20, "limit limitado a 20"
    assert len(res) >= s.get("min", 0), f"ao menos {s.get('min', 0)} resultado(s) para {s['q']!r}"
    for x in res:
        assert x["title"] and isinstance(x.get("subtitle", ""), str)
        u = x["url"]
        assert u == f"/apps/{cfg.app}" or u.startswith((f"/apps/{cfg.app}/", f"/apps/{cfg.app}?")), u
        assert 0.0 <= float(x.get("score", 0)) <= 1.0
    scores = [float(x.get("score", 0)) for x in res]
    assert scores == sorted(scores, reverse=True), "mais relevantes primeiro"
    r = keep(internal.get(base, params={"q": s["q"], "limit": "2"}, headers=h))
    assert len(r.json()["results"]) <= 2
    assert keep(internal.get(base, params={"q": ""}, headers=h)).status_code == 400
    assert keep(internal.get(base, params={"q": "x" * 101}, headers=h)).status_code == 400


# ---- [20] aprovações pela Inbox (onda 9) ---------------------------------------------------------
def test_approvals_contract(cfg: Cfg, internal: httpx.Client) -> None:
    base = f"/{cfg.app}/_hub/approvals"
    ok = {"decision": "approve", "comment": "conformidade"}
    approver = {"x-hub-user": _ident_full("aprovador@x", ["aprovar_solicitacoes"])}
    a = cfg.profile.get("approvals")
    if not a:
        r = keep(internal.post(f"{base}/x-1", json=ok, headers=approver))
        assert r.status_code == 404, "app sem aprovações responde 404"
        return
    assert keep(internal.post(f"{base}/a%20b", json=ok, headers=approver)).status_code == 400
    assert (
        keep(
            internal.post(f"{base}/{a['unknown']}", json={"decision": "talvez"}, headers=approver)
        ).status_code
        == 422
    )
    no_perm = {"x-hub-user": _ident_full("sem@x", [])}
    assert keep(internal.post(f"{base}/{a['unknown']}", json=ok, headers=no_perm)).status_code == 403
    assert keep(internal.post(f"{base}/{a['unknown']}", json=ok)).status_code == 403, "sem x-hub-user"
    assert keep(internal.post(f"{base}/{a['unknown']}", json=ok, headers=approver)).status_code == 404


# ---- [21] idioma (onda 9) ----------------------------------------------------------------------
def test_locale_from_hub_headers(cfg: Cfg, http: httpx.Client) -> None:
    loc = cfg.profile.get("locale")
    if not loc:
        pytest.skip("app sem tradução declarada no perfil")
    page = f"/{cfg.app}/{cfg.profile['sections'][0]}"
    en = keep(http.get(page, headers={"x-hub-locale": "en"}))
    assert en.status_code == 200 and loc["en"] in en.text, "x-hub-locale: en"
    ident = keep(http.get(page, headers={"x-hub-user": _ident_full("conf@x", [], "en")}))
    assert loc["en"] in ident.text, "locale do x-hub-user"
    al = keep(http.get(page, headers={"accept-language": "en-US,en;q=0.9"}))
    assert loc["en"] in al.text, "Accept-Language como último recurso"
    pt = keep(http.get(page))
    assert loc["pt"] in pt.text, "pt-BR por padrão"


# ---- [18] traces e feature flags (onda 8) ---------------------------------------------------
def _pull_spans(cfg: Cfg, internal: httpx.Client, after: int) -> tuple[list[dict[str, Any]], int]:
    r = keep(internal.get(f"/{cfg.app}/_hub/telemetry?after={after}"))
    assert r.status_code == 200
    v = r.json()
    assert isinstance(v["spans"], list) and isinstance(v["next"], int) and isinstance(v["gauges"], dict)
    return v["spans"], v["next"]


def test_trace_context_propagates_to_app_and_db(cfg: Cfg, http: httpx.Client, internal: httpx.Client) -> None:
    cursor = 0
    while True:  # anda até o fim do buffer (o que já estava lá não interessa)
        old, cursor = _pull_spans(cfg, internal, cursor)
        if not old:
            break
    trace, parent = uuid.uuid4().hex, uuid.uuid4().hex[:16]
    path = cfg.profile.get("db_span_path") or f"/{cfg.app}/{cfg.profile['sections'][0]}"
    r = keep(http.get(path, headers={"traceparent": f"00-{trace}-{parent}-01"}))
    assert r.status_code == 200
    mine: list[dict[str, Any]] = []
    for _ in range(20):
        spans, cursor = _pull_spans(cfg, internal, cursor)
        mine += [s for s in spans if s["trace_id"] == trace]
        if any(s["kind"] == "server" for s in mine):
            break
        time.sleep(0.2)
    server = [s for s in mine if s["kind"] == "server"]
    assert server, "span server do app com o trace do hub"
    assert server[0]["parent_id"] == parent, "filho do span do hub (traceparent)"
    for k in ("span_id", "name", "service", "start_us", "duration_us", "attrs"):
        assert k in server[0]
    if cfg.profile.get("db_span_path") and cfg.db_url:
        dbs = [s for s in mine if s["kind"] == "client"]
        assert dbs and all(s["parent_id"] == server[0]["span_id"] for s in dbs), (
            "span de banco filho do server"
        )


def test_flags_push_contract(cfg: Cfg, internal: httpx.Client) -> None:
    ok = {"version": 0, "env": "producao", "flags": []}
    r = keep(internal.post(f"/{cfg.app}/_hub/flags", json=ok))
    assert r.status_code == 200 and "applied" in r.json()
    r = keep(internal.post(f"/{cfg.app}/_hub/flags", json={"version": 0, "env": "outro", "flags": []}))
    assert r.status_code == 400


def test_flag_header_drives_the_ui(cfg: Cfg, http: httpx.Client) -> None:
    f = cfg.profile.get("flag")
    if not f:
        pytest.skip("perfil sem flag de demonstração")
    on = keep(http.get(f["path"], headers={"x-hub-flags": f"{f['key']}=on"}))
    off = keep(http.get(f["path"], headers={"x-hub-flags": f"{f['key']}=off"}))
    none = keep(http.get(f["path"]))
    assert f["marker"] in on.text
    assert f["marker"] not in off.text and f["marker"] not in none.text


def test_outbox_page_shape_and_limit_cap(cfg: Cfg, internal: httpx.Client) -> None:
    r = keep(internal.get(f"/{cfg.app}/_hub/outbox?after=0&limit=1000000"))
    assert r.status_code == 200
    v = r.json()
    assert isinstance(v["events"], list) and len(v["events"]) <= 500
    assert v["durable"] is cfg.profile["durable"]
    for e in v["events"]:
        assert set(e) >= {"id", "event_id", "kind", "user", "action", "target", "detail", "at"}
        assert EVENT_ID.match(e["event_id"]) and isinstance(e["id"], int) and isinstance(e["at"], int)
    ids = [e["id"] for e in v["events"]]
    assert ids == sorted(ids), "ordem crescente pelo cursor"
    r = keep(internal.post(f"/{cfg.app}/_hub/outbox/ack", json={"ids": ["evt-x"] * 1001}))
    assert r.status_code == 400
    r = keep(internal.post(f"/{cfg.app}/_hub/outbox/ack", json={"ids": ["evt-que-nao-existe"]}))
    assert r.status_code == 200 and r.json() == {"acked": 0}


def mutation(cfg: Cfg) -> dict[str, Any]:
    """Ação que grava dados (idempotência + auditoria); perfil sem ela pula esses testes."""
    m = cfg.profile.get("mutation")
    if not m:
        pytest.skip("perfil sem ação que grava dados (mutation)")
    return m


def mine(cfg: Cfg, internal: httpx.Client, needle: str) -> list[dict[str, Any]]:
    """Eventos pendentes desta execução (usuário da suíte + marca ``uniq``)."""
    out: list[dict[str, Any]] = []
    after = 0
    while True:
        v = keep(internal.get(f"/{cfg.app}/_hub/outbox?after={after}&limit=500")).json()
        ev = v["events"]
        out += [
            e
            for e in ev
            if e["user"] in (cfg.name, cfg.user)
            and needle in (e["target"] + " " + e["detail"] + " " + e["action"])
        ]
        if len(ev) < 500:
            return out
        after = ev[-1]["id"]


def send(
    cfg: Cfg,
    http: httpx.Client,
    key: str | None,
    vary: bool = False,
    header: str | None = None,
    uniq: str | None = None,
) -> httpx.Response:
    m = cfg.fill(mutation(cfg), uniq)
    h: dict[str, str] = {}
    if key:
        h["idempotency-key"] = key
    if header is not None:
        h["x-hub-user"] = header
    if "file" in m:
        f = m["file"]
        content = f["content"] + ("variante\n" if vary else "")
        return keep(
            http.post(m["path"], files={f["field"]: (f["name"], content.encode(), f["type"])}, headers=h)
        )
    if "json" in m:
        # Onda 13: mutação pela API JSON do app (ex.: coletor, POST /coletor/api/v1/counts).
        body = dict(m["json"])
        if vary:
            body[m["vary"]] = str(body.get(m["vary"], "")) + " (variante)"
        return keep(http.post(m["path"], json=body, headers=h))
    form = dict(m["form"])
    if vary:
        form[m["vary"]] = form[m["vary"]] + " (variante)"
    return keep(http.post(m["path"], data=form, headers=h))


# ---- [6] idempotência + auditoria ---------------------------------------------------------
def test_idempotency_replay_mismatch_scope_and_audit_exactly_once(
    cfg: Cfg, http: httpx.Client, internal: httpx.Client
) -> None:
    m = mutation(cfg)
    key = f"conf-{uuid.uuid4()}"
    r1 = send(cfg, http, key)
    assert r1.status_code in m["ok"], (r1.status_code, r1.text[:300])
    assert "idempotent-replay" not in r1.headers
    r2 = send(cfg, http, key)
    assert r2.status_code == r1.status_code and r2.headers.get("idempotent-replay") == "true"
    assert r2.text == r1.text, "repetição devolve a mesma resposta"
    r3 = send(cfg, http, key, vary=True)
    assert r3.status_code == 422, "mesma chave, outro pedido → 422"
    assert keep(http.post(m["path"], data={}, headers={"idempotency-key": "curta"})).status_code == 400
    # outra identidade com a mesma chave nunca recebe a resposta guardada
    r4 = send(cfg, http, key, header=ident(f"outro-{cfg.uniq.lower()}@teste.local", "Outro Usuário"))
    assert "idempotent-replay" not in r4.headers
    # auditoria: exatamente um evento desta ação (a repetição não gravou outro)
    ev = mine(cfg, internal, cfg.uniq)
    if m.get("audit_action"):
        ev = [e for e in ev if e["action"] == m["audit_action"]]
    assert len(ev) == 1, ev
    assert ev[0]["kind"] == "audit" and ev[0]["user"] in (cfg.name, cfg.user)
    # confirmação: some da lista; confirmar de novo não conta
    ids = [e["event_id"] for e in mine(cfg, internal, cfg.uniq)]
    r = keep(internal.post(f"/{cfg.app}/_hub/outbox/ack", json={"ids": ids}))
    assert r.json()["acked"] == len(ids)
    assert mine(cfg, internal, cfg.uniq) == []
    assert keep(internal.post(f"/{cfg.app}/_hub/outbox/ack", json={"ids": ids})).json()["acked"] == 0


def test_idempotency_same_key_in_parallel_executes_once(
    cfg: Cfg, http: httpx.Client, internal: httpx.Client
) -> None:
    cfg2 = Cfg()
    cfg2.app, cfg2.profile, cfg2.user, cfg2.name, cfg2.header = (
        cfg.app,
        cfg.profile,
        cfg.user,
        cfg.name,
        cfg.header,
    )
    key = f"conf-par-{uuid.uuid4()}"
    with ThreadPoolExecutor(2) as ex:
        a, b = ex.map(lambda _: send(cfg2, http, key), range(2))
    ok = cfg.profile["mutation"]["ok"]
    statuses = sorted([a.status_code, b.status_code])
    assert statuses[0] in ok, statuses
    other = b if a.status_code in ok and "idempotent-replay" not in a.headers else a
    if other.status_code == 409:
        assert other.headers.get("retry-after"), "409 em paralelo com Retry-After"
    else:
        assert other.status_code in ok
    executed = [x for x in (a, b) if x.status_code in ok and "idempotent-replay" not in x.headers]
    assert len(executed) == 1, "exatamente uma execução"
    ev = mine(cfg, internal, cfg2.uniq)
    if cfg.profile["mutation"].get("audit_action"):
        ev = [e for e in ev if e["action"] == cfg.profile["mutation"]["audit_action"]]
    assert len(ev) == 1
    keep(
        internal.post(
            f"/{cfg.app}/_hub/outbox/ack",
            json={"ids": [e["event_id"] for e in mine(cfg, internal, cfg2.uniq)]},
        )
    )


# ---- [7] tarefas duráveis ----------------------------------------------------------------------
JOB_FIELDS = {
    "id", "kind", "status", "payload", "attempts", "max_attempts", "max_duration_s", "checkpoint",
    "progress_done", "progress_total", "progress_text", "result", "error", "cancel_requested", "user",
    "created_at", "updated_at", "finished_at", "elapsed_s", "run_after",
}  # fmt: skip


def jobs(cfg: Cfg, internal: httpx.Client) -> dict[str, Any]:
    r = keep(internal.get(f"/{cfg.app}/_hub/jobs?limit=100000"))
    assert r.status_code == 200
    v: dict[str, Any] = r.json()
    return v


def test_jobs_listing_contract(cfg: Cfg, internal: httpx.Client) -> None:
    v = jobs(cfg, internal)
    assert v["enabled"] is cfg.profile["durable"]
    assert len(v["jobs"]) <= 500
    for j in v["jobs"]:
        assert set(j) >= JOB_FIELDS, JOB_FIELDS - set(j)
        assert "attempt_token" not in j and "lease_owner" not in j
        assert j["status"] in ("queued", "running", "done", "failed", "dead", "cancelled")
    for action in ("retry", "cancel"):
        r = keep(internal.post(f"/{cfg.app}/_hub/jobs/job-inexistente-{cfg.uniq}/{action}"))
        assert r.status_code == 404
    assert keep(internal.post(f"/{cfg.app}/_hub/jobs/x/explodir")).status_code == 404


def test_job_cancel_and_retry_semantics(cfg: Cfg, http: httpx.Client, internal: httpx.Client) -> None:
    t = cfg.profile.get("job_trigger")
    if not t:
        pytest.skip("app sem gatilho de tarefa no perfil")
    t = cfg.fill(t)
    r = keep(http.post(t["path"], data=t["form"], headers={"idempotency-key": f"conf-job-{uuid.uuid4()}"}))
    assert r.status_code in t["ok"], r.status_code
    found = [
        j for j in jobs(cfg, internal)["jobs"] if j["kind"] == t["kind"] and j["user"] in (cfg.name, cfg.user)
    ]
    assert found, "a tarefa aparece em /_hub/jobs"
    job = found[0]
    assert job["kind"] in cfg.profile["jobs"], "tipo declarado no manifesto"
    c = keep(internal.post(f"/{cfg.app}/_hub/jobs/{job['id']}/cancel"))
    if c.status_code == 200:
        st = c.json()["job"]
        assert st["status"] == "cancelled" or (st["status"] == "running" and st["cancel_requested"])
    else:
        assert c.status_code == 409 and c.json()["error"], "tarefa já terminal: 409 com motivo"
    deadline = time.monotonic() + 60
    while True:
        cur = next(j for j in jobs(cfg, internal)["jobs"] if j["id"] == job["id"])
        if cur["status"] in ("done", "failed", "dead", "cancelled") or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    rr = keep(internal.post(f"/{cfg.app}/_hub/jobs/{job['id']}/retry"))
    if cur["status"] == "done":
        assert rr.status_code == 409, "concluída não é reprocessada"
    else:
        assert (
            rr.status_code == 200
            and rr.json()["job"]["status"] == "queued"
            and rr.json()["job"]["attempts"] == 0
        )
        keep(internal.post(f"/{cfg.app}/_hub/jobs/{job['id']}/cancel"))


# ---- [9] operações pesadas ------------------------------------------------------------------
def test_heavy_operations_answer_429_with_retry_after(cfg: Cfg) -> None:
    h = cfg.profile.get("heavy")
    if not h:
        pytest.skip("app sem operação pesada no perfil")

    def one(_: int) -> httpx.Response:
        with (
            httpx.Client(base_url=cfg.url, headers={"x-hub-user": cfg.header}, timeout=120) as c,
            c.stream("GET", h["path"]) as r,
        ):
            if r.status_code == 200:
                time.sleep(0.5)  # segura a vaga um pouco antes de consumir
                for _ in r.iter_bytes():
                    pass
            else:
                r.read()
            return r

    with ThreadPoolExecutor(h["parallel"]) as ex:
        rs = list(ex.map(one, range(h["parallel"])))
    statuses = [r.status_code for r in rs]
    assert set(statuses) <= {200, 429}, statuses
    for r in rs:
        if r.status_code == 429:
            ra = r.headers.get("retry-after", "")
            assert ra.isdigit() and int(ra) >= 1, "429 com Retry-After em segundos"
    if h.get("expect_429"):
        assert 429 in statuses, f"{h['parallel']} simultâneas acima do limite → 429 ({statuses})"


def test_downloads_are_attachments_nosniff_no_store(cfg: Cfg) -> None:
    """Download (a rota pesada do perfil, ex.: export.csv): anexo com nome de arquivo,
    ``X-Content-Type-Options: nosniff`` e ``Cache-Control: no-store``."""
    h = cfg.profile.get("heavy")
    if not h:
        pytest.skip("app sem download no perfil")
    with httpx.Client(base_url=cfg.url, headers={"x-hub-user": cfg.header}, timeout=120) as c:
        r = c.get(h["path"])
        for _ in range(20):
            if r.status_code != 429:
                break
            time.sleep(1)
            r = c.get(h["path"])
    assert r.status_code == 200, r.status_code
    cd = r.headers.get("content-disposition", "")
    assert cd.lower().startswith("attachment") and "filename=" in cd.lower(), cd
    assert r.headers.get("x-content-type-options", "").lower() == "nosniff"
    assert "no-store" in r.headers.get("cache-control", "").lower()


# ---- [22] armazenamento de arquivos (onda 10) ---------------------------------------------------
def test_storage_roundtrip_confinement_and_failures(cfg: Cfg, http: httpx.Client) -> None:
    """Arquivos do app (`hub_sdk.storage`): envio → aparece na lista → download igual, como
    anexo, `nosniff`/`no-store` → exclusão; nomes com traversal/codificação nunca servem
    arquivo (400/404); armazenamento indisponível (ex.: broker do hub parado durante a suíte)
    responde **503** (com aviso), nunca 500."""
    st = cfg.profile.get("storage")
    if not st:
        pytest.skip("app sem armazenamento de arquivos no perfil")
    up = cfg.fill(st["upload"])
    name = up["name"]
    content = up["content"].encode()
    r = http.post(
        up["path"],
        files={up["field"]: (name, content, up["type"])},
        headers={"idempotency-key": f"conf-storage-{cfg.uniq}"},
    )
    for t in st.get("traversal", []):
        assert http.get(cfg.fill(t)).status_code in (400, 404), t
    if r.status_code == 503:
        assert st.get("accept_unavailable"), "armazenamento indisponível no perfil que exige o round-trip"
        assert "hub-notice" in r.text or "notice" in r.text, r.text[:300]
        return
    assert r.status_code in (200, 201), (r.status_code, r.text[:300])
    base = name.rsplit(".", 1)[0]
    links = [
        m.replace("/_frag", "", 1)
        for m in re.findall(r'href="([^"]+)"', r.text)
        if st["download_prefix"] in m and base in m
    ]
    assert links, f"link de download de {name} ausente"
    d = http.get(links[0])
    assert d.status_code == 200, d.status_code
    assert d.content == content
    cd = d.headers.get("content-disposition", "")
    assert cd.lower().startswith("attachment"), cd
    assert d.headers.get("x-content-type-options", "").lower() == "nosniff"
    assert "no-store" in d.headers.get("cache-control", "").lower()
    rm = http.post(links[0] + st.get("delete_suffix", "/delete"))
    assert rm.status_code == 200, rm.status_code
    assert http.get(links[0]).status_code == 404


# ---- [10] banco (opcional) -----------------------------------------------------------------------
def test_sdk_tables_and_durable_effects(cfg: Cfg, http: httpx.Client) -> None:
    if not cfg.db_url:
        pytest.skip("sem CONF_DB_URL")
    import asyncpg

    async def go() -> None:
        c = await asyncpg.connect(cfg.db_url)
        try:
            cols = {
                (r["table_name"], r["column_name"])
                for r in await c.fetch(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_name IN ('hub_jobs', 'hub_idempotency', 'hub_outbox')"
                )
            }
            for t, col in (
                ("hub_jobs", "attempt_token"), ("hub_jobs", "lease_until"), ("hub_jobs", "checkpoint"),
                ("hub_idempotency", "request_hash"), ("hub_idempotency", "state"),
                ("hub_outbox", "event_id"), ("hub_outbox", "acked_at"),
            ):  # fmt: skip
                assert (t, col) in cols, (t, col)
            key = f"conf-db-{uuid.uuid4()}"
            db_uniq = cfg.uniq + "DB"
            r = send(cfg, http, key, uniq=db_uniq)
            assert r.status_code in cfg.profile["mutation"]["ok"]
            row = await c.fetchrow("SELECT route, state, status FROM hub_idempotency WHERE key = $1", key)
            assert row is not None, "chave registrada"
            assert row["route"].startswith(cfg.header + "\n") and row["route"].endswith(
                "POST " + cfg.profile["mutation"]["path"]
            ), "escopo: identidade + método + caminho"
            assert row["state"] == "done" and row["status"] == r.status_code
            n = await c.fetchval(
                "SELECT count(*) FROM hub_outbox WHERE user_name = ANY($1::text[]) AND payload::text LIKE $2",
                [cfg.name, cfg.user],
                f"%{db_uniq}%",
            )
            assert n == 1, "um evento de auditoria na transação da ação"
        finally:
            await c.close()

    asyncio.run(go())


# ---- [23] API v1 (onda 11) ------------------------------------------------------------------
def _openapi(http: httpx.Client, cfg: Cfg) -> dict[str, Any]:
    r = keep(http.get(f"/{cfg.app}/api/openapi.json"))
    assert r.status_code == 200, "OpenAPI em /{app}/api/openapi.json (manifest.api.openapi)"
    doc: dict[str, Any] = r.json()
    return doc


def _simple_gets(cfg: Cfg, doc: dict[str, Any]) -> list[str]:
    """Operações GET sem parâmetro de caminho nem de consulta obrigatório."""
    out = []
    for path, item in doc["paths"].items():
        op = item.get("get")
        if op is None or "{" in path:
            continue
        if any(p.get("required") for p in op.get("parameters") or []):
            continue
        out.append(path)
    return out


def test_openapi_is_valid_and_only_documents_the_app_api(cfg: Cfg, http: httpx.Client) -> None:
    from openapi_spec_validator import validate

    doc = _openapi(http, cfg)
    validate(doc)
    assert doc["openapi"].startswith("3.")
    assert doc["paths"], "ao menos uma operação"
    for path, item in doc["paths"].items():
        assert path.startswith(f"/{cfg.app}/api/"), path
        for m, op in item.items():
            if m in ("get", "post", "put", "patch", "delete"):
                assert op.get("operationId"), f"{m} {path} sem operationId (vira o nome da ferramenta MCP)"


def test_declared_endpoints_are_reachable_and_auth_is_enforced(cfg: Cfg, http: httpx.Client) -> None:
    doc = _openapi(http, cfg)
    gets = _simple_gets(cfg, doc)
    assert gets, "ao menos uma leitura sem parâmetros obrigatórios"
    with httpx.Client(base_url=cfg.url, timeout=60) as anon:
        for path in gets:
            r = keep(http.get(path))
            assert 200 <= r.status_code < 300, (path, r.status_code, r.text[:200])
            assert r.headers["content-type"].startswith("application/json"), path
            # Sem a identidade do hub: 401 (problem+json).
            r = keep(anon.get(path))
            assert r.status_code == 401, (path, r.status_code)
            assert r.headers["content-type"].startswith("application/problem+json"), path
    # Erros no formato RFC 9457.
    r = keep(http.get(f"/{cfg.app}/api/v1/nao-existe-{cfg.uniq.lower()}"))
    assert r.status_code in (404, 405)


def test_ready_header_is_absent_once_the_app_is_ready(cfg: Cfg, http: httpx.Client) -> None:
    """x-hub-ready: 0 só enquanto o app aquece (migrações); pronto, o cabeçalho some — o
    blue/green do hub espera isso antes de trocar o tráfego ([2], correção de 28/09)."""
    deadline = time.monotonic() + 60
    while True:
        r = keep(http.get(f"/{cfg.app}"))
        if r.headers.get("x-hub-ready") is None:
            break
        assert r.headers["x-hub-ready"] == "0", "valor único permitido: 0"
        assert time.monotonic() < deadline, "app nunca ficou pronto (x-hub-ready: 0 por 60 s)"
        time.sleep(1)
    doc = _openapi(http, cfg)
    for path in _simple_gets(cfg, doc):
        assert keep(http.get(path)).headers.get("x-hub-ready") is None, path


def test_ready_and_health_endpoints_are_mandatory(cfg: Cfg) -> None:
    """[2] Onda 12 (2.5): ``GET /{id}/_hub/ready`` (prontidão: 200 pronto / 503 não pronto
    com JSON ``reason``) e ``GET /{id}/_hub/health`` (vivacidade) são **obrigatórios** —
    o blue/green do hub só troca o tráfego depois do 200 do ready, e um app sem a rota é
    recusado na implantação. Sem token interno; ``Cache-Control: no-store``."""
    with httpx.Client(base_url=cfg.url, timeout=10) as anon:
        deadline = time.monotonic() + 60
        while True:
            r = keep(anon.get(f"/{cfg.app}/_hub/ready"))
            assert r.status_code in (200, 503), f"/_hub/ready respondeu {r.status_code} (obrigatório)"
            assert r.headers["content-type"].startswith("application/json")
            assert "no-store" in r.headers.get("cache-control", "")
            body = r.json()
            if r.status_code == 200:
                assert body["status"] == "ready", body
                assert isinstance(body.get("checks", {}), dict)
                break
            assert body["status"] == "not_ready" and body.get("reason"), body
            assert time.monotonic() < deadline, f"app nunca ficou pronto: {body}"
            time.sleep(1)
        r = keep(anon.get(f"/{cfg.app}/_hub/health"))
        assert r.status_code == 200 and r.json()["status"] == "ok"


# ---- [24] MCP interno (onda 11) ----------------------------------------------------------------
def _mcp(c: httpx.Client, cfg: Cfg, method: str, params: dict[str, Any] | None = None) -> httpx.Response:
    return c.post(
        f"/{cfg.app}/_hub/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"accept": "application/json, text/event-stream", "x-hub-user": cfg.header},
    )


def test_mcp_initialize_list_and_call_a_safe_tool(
    cfg: Cfg, http: httpx.Client, internal: httpx.Client
) -> None:
    if cfg.profile.get("mcp") is False:
        pytest.skip("app com mcp.enabled = false")
    # Sem o token interno: recusado (só o hub chama).
    assert _mcp(http, cfg, "tools/list").status_code in (401, 403)
    r = _mcp(internal, cfg, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    assert r.status_code == 200
    init = r.json()["result"]
    assert init["protocolVersion"] in ("2025-06-18", "2025-03-26", "2025-11-25")
    assert "tools" in init["capabilities"]
    tools = _mcp(internal, cfg, "tools/list").json()["result"]["tools"]
    assert tools, "ao menos uma ferramenta"
    names = {t["name"] for t in tools}
    doc = _openapi(http, cfg)
    op_ids = {
        op["operationId"]
        for item in doc["paths"].values()
        for m, op in item.items()
        if isinstance(op, dict) and "operationId" in op
    }
    assert names <= op_ids, "ferramentas geradas das operações do OpenAPI"
    for t in tools:
        assert re.fullmatch(r"[a-z0-9_-]{1,64}", t["name"]), t["name"]
        assert t["inputSchema"]["type"] == "object"
    safe = next(
        t for t in tools if t["annotations"].get("readOnlyHint") and not t["inputSchema"].get("required")
    )
    r = _mcp(internal, cfg, "tools/call", {"name": safe["name"], "arguments": {}}).json()
    assert r["result"]["isError"] is False, r
    assert r["result"]["content"][0]["type"] == "text"
    # Ferramenta desconhecida e método desconhecido: erros JSON-RPC.
    assert _mcp(internal, cfg, "tools/call", {"name": "nao-existe"}).json()["error"]["code"] == -32602
    assert _mcp(internal, cfg, "nao/existe").json()["error"]["code"] == -32601


# ---- [11] cookies e cabeçalhos (todas as respostas desta execução) ---------------------------
def test_zz_cookies_and_headers_over_every_response(cfg: Cfg) -> None:
    assert SEEN, "nenhuma resposta coletada"
    for r in SEEN:
        for sc in r.headers.get_list("set-cookie"):
            name = sc.split("=", 1)[0].strip().lower()
            assert not name.startswith("hub_"), f"cookie reservado do hub: {sc}"
            assert "domain=" not in sc.lower(), f"cookie com Domain: {sc}"
        assert not [k for k in r.headers if k.lower().startswith("x-hub-")], "o app não emite x-hub-*"
    assert all(
        r.headers.get("x-app-version") for r in SEEN if r.status_code == 200 and "_hub" not in str(r.url)
    ), "x-app-version em toda resposta"
