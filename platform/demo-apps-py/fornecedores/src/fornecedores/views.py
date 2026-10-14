"""HTML pieces of the supplier record (lookup box, attachments) and response helpers."""

from __future__ import annotations

from fastapi.responses import Response
from hub_sdk import storage
from hub_sdk.render import html

from fornecedores.lookup import CnpjInfo, Outcome
from fornecedores.state import State
from fornecedores.suppliers import Supplier, attach_max_bytes
from fornecedores.ui import esc, notice, thousands

FRAG = "/_frag"
NO_ATTACHMENTS = '<p class="hub-muted">Nenhum anexo ainda.</p>'


def not_found() -> Response:
    return html(
        '<section class="empty-state"><h1>Página não encontrada</h1><p>Esta seção não existe. '
        '<a href="/apps/fornecedores">Voltar ao início do app</a>.</p></section>',
        404,
    )


def with_retry(resp: Response, retry: int | None) -> Response:
    if retry is not None:
        resp.headers["retry-after"] = str(min(retry, 3600))
    return resp


def storage_status(e: storage.StorageError) -> tuple[int, int | None]:
    """Storage failure → status: transient 503 + ``Retry-After`` (hub.js retries
    on its own); missing 404; over the limit 413; the rest 502."""
    if e.transient:
        return 503, e.retry_after if e.retry_after is not None else 2
    if e.status == 404:
        return 404, None
    if e.status == 413:
        return 413, None
    return 502, None


def shown_name(name: str) -> str:
    """Object name without the date/time prefix (``20260926120000-contrato.pdf``)."""
    _, sep, rest = name.partition("-")
    return rest if sep else name


def kb(n: int) -> int:
    return -(-n // 1024)


def info_html(i: CnpjInfo, extra: str) -> str:
    when = esc(i.consultado_em[:19].replace("T", " "))
    return (
        '<div class="app2-metrics hub-metrics">'
        f"<article><span>Score de crédito</span><strong>{i.score}</strong><small>risco {esc(i.risco)}</small></article>"
        f'<article><span>Situação</span><strong class="hub-small">{esc(i.situacao)}</strong><small>{esc(i.razao_social)}</small></article>'
        f'<article><span>Consulta</span><strong class="hub-small">{when}</strong><small>{esc(extra)}</small></article></div>'
    )


def outcome_html(o: Outcome) -> str:
    if o.info is not None:
        return info_html(o.info, f"{o.attempts} tentativa(s) · {o.ms} ms")
    out = notice("warn", "Consulta indisponível no momento", o.reason)
    if o.cached is not None:
        info, age = o.cached
        out += info_html(info, f"último resultado conhecido, de {age} s atrás")
    return out


def lookup_box(sup: Supplier, body: str) -> str:
    return (
        '<section class="app2-panel hub-panel" id="lookup-box"><div class="app2-panel-head"><div>'
        f'<span class="app2-kicker">CONSULTA CADASTRAL</span><h2>CNPJ {esc(sup.cnpj)}</h2></div>'
        f'<form method="post" action="/apps/fornecedores/suppliers/{esc(sup.code)}/lookup" data-hub-target="#lookup-box">'
        f'<button class="app2-outline" type="submit">Consultar agora</button></form></div>{body}</section>'
    )


def storage_label(st: storage.Connection | None) -> str:
    if st is not None and st.via_broker and st.kind == "sharepoint":
        return f"Guardados no SharePoint (conexão {esc(st.name)})."
    if st is not None and st.via_broker:
        return f"Guardados na conexão {esc(st.name)}."
    return "Guardados no object store do app." if st is not None else ""


def attachment_row(sup: Supplier, o: storage.Item) -> str:
    return (
        f"<tr><td><b>{esc(shown_name(o.name))}</b></td><td>{thousands(kb(o.size))} KB</td>"
        f"<td>{esc(o.modified[:16].replace('T', ' '))}</td>"
        f'<td class="hub-row-actions"><a class="app2-inline-link" download '
        f'href="{FRAG}/fornecedores/suppliers/{esc(sup.code)}/attachments/{esc(o.name)}">Baixar</a>'
        f'<form class="hub-inline" method="post" '
        f'action="/apps/fornecedores/suppliers/{esc(sup.code)}/attachments/{esc(o.name)}/delete" '
        f'data-hub-target="#attachments"><button type="submit" class="app2-inline-link hub-danger">Excluir</button></form></td></tr>'
    )


async def attachments_listing(conn: storage.Connection | None, sup: Supplier) -> str:
    if conn is None:
        return notice(
            "info",
            "Object store não configurado",
            "Defina HUB_S3_ENDPOINT, HUB_S3_BUCKET e as chaves (ou vincule a conexão fornecedores-anexos) para guardar anexos.",
        )
    try:
        objs = [o for o in await conn.list(sup.code) if not o.folder]
    except storage.StorageError as e:
        if e.status == 404:
            return NO_ATTACHMENTS
        return notice("error", "Não foi possível listar os anexos", e.message)
    if not objs:
        return NO_ATTACHMENTS
    rows = "".join(attachment_row(sup, o) for o in objs)
    return (
        '<div class="app2-table-wrap"><table class="app2-table"><thead><tr><th>ARQUIVO</th>'
        "<th>TAMANHO</th><th>ENVIADO EM</th><th></th></tr></thead><tbody>"
        f"{rows}</tbody></table></div>"
    )


async def attachments_html(st: State, sup: Supplier, msg: str = "") -> str:
    listing = await attachments_listing(st.store, sup)
    where_kind = "" if st.store is None else (st.store.kind if st.store.via_broker else "s3-app")
    return (
        '<section class="app2-panel hub-panel" id="attachments"><div class="app2-panel-head"><div>'
        f'<span class="app2-kicker">DOCUMENTOS</span><h2>Anexos</h2><p class="hub-muted" data-storage="{where_kind}">'
        f"{storage_label(st.store)}</p></div></div>{msg}{listing}"
        f'<form class="hub-form hub-upload" method="post" action="/apps/fornecedores/suppliers/{esc(sup.code)}/attachments" '
        'enctype="multipart/form-data" data-hub-target="#attachments"><div class="form-field">'
        f'<label for="f-anexo">Novo anexo (PDF ou imagem, até {attach_max_bytes() // 1024 // 1024} MB)</label>'
        '<input id="f-anexo" type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,.webp" required></div>'
        '<div class="modal-actions"><span class="hub-muted" data-hub-progress></span>'
        '<button class="app2-primary" type="submit">Enviar anexo</button></div></form></section>'
    )


async def detail(st: State, sup: Supplier) -> str:
    last = st.lookup.last_good(sup.cnpj)
    body = (
        info_html(last[0], f"consultado há {last[1]} s")
        if last
        else '<p class="hub-muted">Consulte a situação cadastral e o score de crédito na base externa.</p>'
    )
    return (
        '<section class="app2-panel hub-panel hub-supplier"><div class="app2-panel-head"><div>'
        f'<span class="app2-kicker">FICHA DO FORNECEDOR · {esc(sup.code)}</span><h2>{esc(sup.name)}</h2>'
        f'<p class="hub-muted">{esc(sup.category)} · {esc(sup.city)} · CNPJ {esc(sup.cnpj)} · situação: {esc(sup.status)}</p></div>'
        f'<div class="hub-actions"><a class="app2-outline" download href="{FRAG}/fornecedores/suppliers/{esc(sup.code)}/ficha.pdf">Gerar ficha PDF</a>'
        '<a class="app2-outline" href="/apps/fornecedores/indicators">Fechar</a></div></div></section>'
        f"{lookup_box(sup, body)}{await attachments_html(st, sup)}"
    )
