"""Regras puras do Coletor: catálogo determinístico, EAN-13, normalização de leitura,
situação das linhas, divergências e SVG dos códigos."""

from __future__ import annotations

import re

import pytest

from coletor import barcodes
from coletor.model import (
    catalog,
    ean13_check,
    line_status,
    locations,
    normalize_code,
    orders,
    valid_ean13,
)


def test_catalog_has_200_valid_unique_codes() -> None:
    items = catalog()
    assert len(items) == 200
    assert all(valid_ean13(i.code) for i in items)
    assert len({i.code for i in items}) == 200 and len({i.code128 for i in items}) == 200
    assert items[0].code == "7890000000017" and items[0].code128 == "COL-0001"
    assert catalog() == items, "determinístico"


def test_ean13_check_known_values() -> None:
    assert ean13_check("789100000001") == "4"
    assert ean13_check("400638133393") == "1"
    assert not valid_ean13("7890000000012")


def test_locations_and_orders_refer_to_catalog() -> None:
    codes = {i.code for i in catalog()}
    locs = {loc.code for loc in locations()}
    assert len(locs) == 12 and "LOC-A01" in locs
    os_ = orders()
    assert [o.code for o in os_] == ["REC-1001", "REC-1002", "REC-1003"]
    assert {o.offset_days for o in os_} == {-1, 0, 1}
    assert all(item in codes for o in os_ for item, _ in o.lines)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (" col-0001\n", "COL-0001"),
        ("036000291452", "0036000291452"),
        ("7890000000017", "7890000000017"),
        ("\x1dLOC-a01", "LOC-A01"),
    ],
)
def test_normalize_code(raw: str, want: str) -> None:
    assert normalize_code(raw) == want


def test_line_status() -> None:
    assert [line_status(5, r) for r in (0, 3, 5, 6)] == ["Pendente", "Parcial", "Completo", "Excedente"]


@pytest.mark.parametrize(
    ("fmt", "value", "full"),
    [
        ("ean_13", "789000000001", "7890000000017"),
        ("ean_8", "9638507", "96385074"),
        ("upc_a", "03600029145", "036000291452"),
        ("code_128", "COL-0001", "COL-0001"),
        ("code_39", "LOC-A01", "LOC-A01"),
        ("qr_code", "REC-1001", "REC-1001"),
        ("data_matrix", "COL-0003", "COL-0003"),
    ],
)
def test_svg_every_format(fmt: str, value: str, full: str) -> None:
    s = str(barcodes.svg(fmt, value))
    assert s.startswith("<svg") and s.endswith("</svg>")
    assert f'data-code-value="{full}"' in s and 'role="img"' in s
    # Só elementos aceitos pelo sanitizador do hub, nada de script/estilo.
    assert set(re.findall(r"<(\w+)", s)) <= {"svg", "rect", "path"}
    assert "<script" not in s and "style=" not in s
    d = re.search(r' d="([^"]*)"', s)
    assert d and d.group(1).count("M") > 10


def test_linear_has_quiet_zone_and_ean_guards() -> None:
    e = barcodes.encode("ean_13", "789000000001")
    row = e.modules[0]
    assert len(row) == 95 and row.startswith("101") and row.endswith("101")
    s = str(barcodes.svg("ean_13", "789000000001"))
    assert 'viewBox="0 0 115 40"' in s, "95 módulos + 2 × 10 de zona de silêncio"


def test_svg_label_is_escaped() -> None:
    s = str(barcodes.svg("qr_code", "x", label='<b onload="x">'))
    assert "<b " not in s and "&lt;b" in s
