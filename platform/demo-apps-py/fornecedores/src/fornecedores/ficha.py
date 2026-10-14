"""Supplier record as PDF."""

from __future__ import annotations

from datetime import UTC, datetime

from hub_sdk.storage import Item

from fornecedores.lookup import CnpjInfo
from fornecedores.pdf import A4_W, Pdf
from fornecedores.suppliers import Supplier


def ficha_pdf(  # noqa: PLR0915
    sup: Supplier,
    user: str,
    lookup: tuple[CnpjInfo, int] | None,
    anexos: list[Item],
    anexos_err: str | None,
) -> bytes:
    ink, dark, muted = (0.66, 0.31, 0.21), (0.11, 0.14, 0.2), (0.45, 0.5, 0.56)
    p = Pdf()
    p.rect(0.0, 0.0, A4_W, 92.0, (0.965, 0.894, 0.863))
    p.text(40.0, 38.0, 9.0, True, ink, "REDE DE FORNECEDORES · FICHA DO FORNECEDOR")
    p.text(40.0, 66.0, 22.0, True, dark, sup.name)
    y = 130.0
    for k, v in (
        ("Código", sup.code),
        ("CNPJ", sup.cnpj),
        ("Categoria", sup.category),
        ("Localização", sup.city),
        ("Situação na rede", sup.status),
    ):
        p.text(40.0, y, 9.0, False, muted, k)
        p.text(180.0, y, 11.0, True, dark, v)
        p.line(40.0, y + 8.0, A4_W - 40.0, y + 8.0, (0.9, 0.91, 0.93))
        y += 26.0
    y += 14.0
    p.text(40.0, y, 13.0, True, dark, "Consulta cadastral")
    y += 22.0
    if lookup is not None:
        i, age = lookup
        for line in (
            f"Razão social: {i.razao_social}",
            f"Situação: {i.situacao} · Score de crédito: {i.score} (risco {i.risco})",
            f"Consultado em {i.consultado_em[:19].replace('T', ' ')} (há {age} s)",
        ):
            p.text(40.0, y, 10.0, False, dark, line)
            y += 16.0
    else:
        p.text(40.0, y, 10.0, False, muted, "Nenhuma consulta realizada nesta sessão do app.")
        y += 16.0
    y += 16.0
    p.text(40.0, y, 13.0, True, dark, "Anexos")
    y += 22.0
    if anexos_err is not None:
        p.text(40.0, y, 10.0, False, muted, anexos_err)
        y += 16.0
    elif not anexos:
        p.text(40.0, y, 10.0, False, muted, "Nenhum anexo.")
        y += 16.0
    for o in [o for o in anexos if not o.folder][:40]:
        shown = o.name.partition("-")[2] if "-" in o.name else o.name
        p.text(40.0, y, 10.0, False, dark, f"• {shown} — {-(-o.size // 1024)} KB")
        y += 15.0
        if y > 780.0:
            p.new_page()
            y = 50.0
    stamp = datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC")
    p.text(40.0, 815.0, 8.0, False, muted, f"Gerado em {stamp} por {user}. Documento de demonstração.")
    return p.finish()
