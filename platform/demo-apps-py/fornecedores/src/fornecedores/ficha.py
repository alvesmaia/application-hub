"""Supplier record as PDF."""

from __future__ import annotations

from datetime import UTC, datetime

from hub_sdk.storage import Item

from fornecedores.lookup import CnpjInfo
from fornecedores.pdf import A4_W, Pdf
from fornecedores.suppliers import Supplier

INK, DARK, MUTED = (0.66, 0.31, 0.21), (0.11, 0.14, 0.2), (0.45, 0.5, 0.56)


def _fields(p: Pdf, sup: Supplier, y: float) -> float:
    for k, v in (
        ("Código", sup.code),
        ("CNPJ", sup.cnpj),
        ("Categoria", sup.category),
        ("Localização", sup.city),
        ("Situação na rede", sup.status),
    ):
        p.text(40.0, y, 9.0, False, MUTED, k)
        p.text(180.0, y, 11.0, True, DARK, v)
        p.line(40.0, y + 8.0, A4_W - 40.0, y + 8.0, (0.9, 0.91, 0.93))
        y += 26.0
    return y


def _lookup(p: Pdf, lookup: tuple[CnpjInfo, int] | None, y: float) -> float:
    p.text(40.0, y, 13.0, True, DARK, "Consulta cadastral")
    y += 22.0
    if lookup is None:
        p.text(40.0, y, 10.0, False, MUTED, "Nenhuma consulta realizada nesta sessão do app.")
        return y + 16.0
    i, age = lookup
    for line in (
        f"Razão social: {i.razao_social}",
        f"Situação: {i.situacao} · Score de crédito: {i.score} (risco {i.risco})",
        f"Consultado em {i.consultado_em[:19].replace('T', ' ')} (há {age} s)",
    ):
        p.text(40.0, y, 10.0, False, DARK, line)
        y += 16.0
    return y


def _attachments(p: Pdf, anexos: list[Item], anexos_err: str | None, y: float) -> None:
    p.text(40.0, y, 13.0, True, DARK, "Anexos")
    y += 22.0
    if anexos_err is not None:
        p.text(40.0, y, 10.0, False, MUTED, anexos_err)
        y += 16.0
    elif not anexos:
        p.text(40.0, y, 10.0, False, MUTED, "Nenhum anexo.")
        y += 16.0
    for o in [o for o in anexos if not o.folder][:40]:
        shown = o.name.partition("-")[2] if "-" in o.name else o.name
        p.text(40.0, y, 10.0, False, DARK, f"• {shown} — {-(-o.size // 1024)} KB")
        y += 15.0
        if y > 780.0:
            p.new_page()
            y = 50.0


def ficha_pdf(
    sup: Supplier,
    user: str,
    lookup: tuple[CnpjInfo, int] | None,
    anexos: list[Item],
    anexos_err: str | None,
) -> bytes:
    p = Pdf()
    p.rect(0.0, 0.0, A4_W, 92.0, (0.965, 0.894, 0.863))
    p.text(40.0, 38.0, 9.0, True, INK, "REDE DE FORNECEDORES · FICHA DO FORNECEDOR")
    p.text(40.0, 66.0, 22.0, True, DARK, sup.name)
    y = _fields(p, sup, 130.0) + 14.0
    y = _lookup(p, lookup, y) + 16.0
    _attachments(p, anexos, anexos_err, y)
    stamp = datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC")
    p.text(40.0, 815.0, 8.0, False, MUTED, f"Gerado em {stamp} por {user}. Documento de demonstração.")
    return p.finish()
