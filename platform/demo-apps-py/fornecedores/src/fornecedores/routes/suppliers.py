"""Supplier actions: registry lookup, attachments (upload, download, delete) and the PDF record."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from hub_sdk import storage
from hub_sdk.render import busy, download_headers, html

from fornecedores import uploads
from fornecedores.ficha import ficha_pdf
from fornecedores.state import State, StateDep, base_of, user_of
from fornecedores.suppliers import Supplier, supplier, valid_object_name
from fornecedores.ui import notice
from fornecedores.views import (
    attachments_html,
    kb,
    lookup_box,
    not_found,
    outcome_html,
    shown_name,
    storage_status,
    with_retry,
)

router = APIRouter()


@router.get("/fornecedores/suppliers/{code}/lookup")
@router.get("/fornecedores/suppliers/{code}/attachments")
@router.get("/fornecedores/suppliers/{code}/attachments/{name}/delete")
async def only_post(code: str, req: Request) -> Response:
    return not_found(base_of(req))


@router.post("/fornecedores/suppliers/{code}/lookup")
async def do_lookup(code: str, req: Request, st: StateDep) -> Response:
    sup = supplier(code)
    if sup is None:
        return not_found(base_of(req))
    o = await st.lookup.lookup(sup.cnpj)
    det = (
        f"score {o.info.score} ({o.info.risco}) · {o.attempts} tentativa(s) · {o.ms} ms"
        if o.info is not None
        else f"contingência: {o.reason}"
    )
    st.audit(req, "Consulta cadastral de fornecedor", sup.code, det)
    return html(lookup_box(sup, outcome_html(o), base_of(req)))


@router.post("/fornecedores/suppliers/{code}/attachments")
async def upload(code: str, req: Request, st: StateDep) -> Response:
    sup = supplier(code)
    if sup is None:
        return not_found(base_of(req))
    try:
        name, ct, size = await uploads.receive(st, sup, req)
    except uploads.UploadRefused as e:
        body = await attachments_html(st, sup, base_of(req), notice("error", e.title, e.text))
        return with_retry(html(body, e.status), e.retry)
    st.audit(req, "Anexo de fornecedor enviado", sup.code, f"{name} · {ct} · {size} bytes")
    msg = notice("ok", "Anexo enviado", f"{name} ({kb(size)} KB)")
    return html(await attachments_html(st, sup, base_of(req), msg), 201)


@router.get("/fornecedores/suppliers/{code}/attachments/{name}")
async def download(code: str, name: str, req: Request, st: StateDep) -> Response:
    sup = supplier(code)
    store = st.user_store(req)
    if sup is None or not valid_object_name(name) or store is None:
        return not_found(base_of(req))
    try:
        d = await store.download(f"{sup.code}/{name}")
    except storage.StorageError as e:
        status, retry = storage_status(e)
        return with_retry(html(notice("error", "Anexo indisponível", e.message), status), retry)
    headers = download_headers(d.content_type, shown_name(name))
    if d.length is not None:
        headers["content-length"] = str(d.length)
    return StreamingResponse(d.chunks(), headers=headers)


@router.post("/fornecedores/suppliers/{code}/attachments/{name}/delete")
async def remove(code: str, name: str, req: Request, st: StateDep) -> Response:
    sup = supplier(code)
    store = st.user_store(req)
    if sup is None or not valid_object_name(name) or store is None:
        return not_found(base_of(req))
    try:
        await store.delete(f"{sup.code}/{name}")
    except storage.StorageError as e:
        status, retry = storage_status(e)
        body = await attachments_html(
            st, sup, base_of(req), notice("error", "Não foi possível excluir", e.message)
        )
        return with_retry(html(body, status), retry)
    st.audit(req, "Anexo de fornecedor excluído", sup.code, name)
    return html(await attachments_html(st, sup, base_of(req), notice("ok", "Anexo excluído", name)))


async def _pdf_attachments(st: State, sup: Supplier, req: Request) -> tuple[list[storage.Item], str | None]:
    """Attachments listed in the PDF, or the error to print in their place."""
    store = st.user_store(req)
    if store is None:
        return [], "Object store não configurado."
    try:
        return await store.list(sup.code), None
    except storage.StorageError as e:
        return [], None if e.status == 404 else e.message


@router.get("/fornecedores/suppliers/{code}/ficha.pdf")
async def ficha(code: str, req: Request, st: StateDep) -> Response:
    sup = supplier(code)
    if sup is None:
        return not_found(base_of(req))
    permit = st.sdk.heavy.try_acquire()
    if permit is None:
        return busy("A geração do PDF", "PDF em fila…")
    with permit:
        anexos, err = await _pdf_attachments(st, sup, req)
        data = ficha_pdf(sup, user_of(req), st.lookup.last_good(sup.cnpj), anexos, err)
    st.audit(req, "Ficha de fornecedor gerada (PDF)", sup.code, f"{len(data)} bytes")
    headers = download_headers("application/pdf", f"ficha-{sup.code}.pdf")
    headers["content-length"] = str(len(data))
    return Response(data, headers=headers)
