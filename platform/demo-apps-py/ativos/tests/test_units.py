"""Units without I/O."""

from __future__ import annotations

import datetime as dt
import json
import re
import zipfile
from pathlib import Path

import pytest
from hub_sdk.jobs import JobFail

from ativos import i18n, importer, model, xlsx_guard
from ativos.model import COL, COLS, MAX_PAGE, Invalid, display, map_headers, normalize, validate
from ativos.query import Filter
from ativos.ui import money_cents, query, thousands, wrap
from ativos.views import field_html


def test_thirty_columns_with_unique_headers() -> None:
    assert len(COLS) == 30
    assert len({c.header.lower() for c in COLS}) == 30


def test_headers_mapped_case_insensitively_and_required_checked() -> None:
    hs = ["patrimônio", "NOME", "Categoria", "Localização", "Status", "Extra"]
    m = map_headers(hs)
    assert m[0] == 0 and m[5] is None
    with pytest.raises(Invalid, match="Patrimônio"):
        map_headers(hs[1:])
    with pytest.raises(Invalid, match="repetida"):
        map_headers(["Nome", "nome"])


def test_required_and_types_are_validated() -> None:
    with pytest.raises(Invalid):
        normalize(COL["name"], None)
    assert normalize(COL["brand"], None) is None
    assert normalize(COL["category"], "tecnologia") == "Tecnologia"
    with pytest.raises(Invalid):
        normalize(COL["category"], "Brinquedos")
    assert normalize(COL["acquisition_date"], "25/09/2024") == dt.date(2024, 9, 25)
    assert normalize(COL["acquisition_date"], 45560.0) == dt.date(2024, 9, 25)
    assert normalize(COL["acquisition_date"], dt.datetime(2024, 9, 25, 10)) == dt.date(2024, 9, 25)
    with pytest.raises(Invalid):
        normalize(COL["acquisition_date"], "31/02/2024")
    assert normalize(COL["acquisition_value_cents"], "R$ 1.234,56") == 123456
    assert normalize(COL["acquisition_value_cents"], 99.9) == 9990
    with pytest.raises(Invalid):
        normalize(COL["acquisition_value_cents"], "-5")
    with pytest.raises(Invalid):
        normalize(COL["ram_gb"], 1.5)
    with pytest.raises(Invalid):
        normalize(COL["useful_life_months"], 0.0)
    assert normalize(COL["tag"], 1234.0) == "1234"
    with pytest.raises(Invalid, match="acima de 40"):
        normalize(COL["tag"], "x" * 41)
    assert normalize(COL["notes"], "linha 1\nlinha 2") == "linha 1\nlinha 2"
    with pytest.raises(Invalid, match="controle"):
        normalize(COL["name"], "a\x00b")


def test_validate_joins_errors_like_the_screen() -> None:
    _, errs = validate({"tag": "", "name": "x", "category": "?", "location": "Recife", "status": "Em uso"})
    assert (
        errs
        == "Patrimônio: campo obrigatório; Categoria: valor inválido (use: "
        + ", ".join(COL["category"].options)
        + ")"
    )


def test_display_formats_dates_and_money() -> None:
    assert display(COL["acquisition_date"], "2024-09-25") == "25/09/2024"
    assert display(COL["acquisition_date"], dt.date(2024, 9, 25)) == "25/09/2024"
    assert display(COL["acquisition_value_cents"], "123456") == "1234,56"
    assert display(COL["brand"], None) == ""


def test_filter_whitelists_and_parametrizes() -> None:
    f = Filter.from_query(
        {
            "q": "50%_x",
            "categoria": "Tecnologia",
            "status": "'; DROP TABLE assets; --",
            "sort": "valor",
            "dir": "desc",
            "page": "999999",
        }
    )
    assert f.status == "" and f.page == MAX_PAGE
    wh, p = f.where()
    assert wh == "WHERE (name ILIKE $1 OR tag ILIKE $1) AND category = $2"
    assert p == ["%50\\%\\_x%", "Tecnologia"]
    assert f.order() == "assets.acquisition_value_cents DESC NULLS LAST, assets.id DESC"
    assert Filter.from_query({"sort": "1;drop"}).order() == "assets.name ASC NULLS FIRST, assets.id ASC"


def test_qs_keeps_filters_and_overrides() -> None:
    f = Filter.from_query({"q": "dell", "localizacao": "Recife", "page": "3"})
    assert f.qs(page="4") == "?q=dell&localizacao=Recife&page=4"
    assert f.qs(page="") == "?q=dell&localizacao=Recife"
    assert f.qs(edit="7") == "?q=dell&localizacao=Recife&page=3&edit=7"
    assert f.describe() == 'busca "dell", localização Recife'


def test_ui_formats_and_wrap() -> None:
    assert thousands(2_000_000) == "2.000.000" and thousands(999) == "999"
    assert money_cents(123_456) == "R$ 1.234,56"
    assert query([("q", "a b"), ("x", "")]) == "?q=a%20b" and query([("x", "")]) == ""
    t = (
        '<div class="workspace" id="workspace"><aside class="app-sidebar">S</aside><section class="workspace-content x">'
        '<button class="mobile-menu-button" data-m="">M</button><p>velho</p></section></div><script>1</script>'
    )
    w = wrap(t, "<p>novo</p>")
    assert '<aside class="app-sidebar">S</aside>' in w and "M</button><p>novo</p></section></div>" in w
    assert "velho" not in w and "script" not in w


def test_form_fields_markup() -> None:
    assert "readonly required" in field_html("tag", "PT-1", True)
    assert "<textarea" in field_html("notes", "x", False)
    sel = field_html("category", "Veículos", False)
    assert '<option value="Veículos" selected>' in sel and '<option value="">' not in sel
    assert 'for="f-name"' in field_html("name", "", False) and "Nome *" in field_html("name", "", False)


# ---- i18n ----------------------------------------------------------


def test_catalog_rows_have_two_languages() -> None:
    rows = json.loads((Path(i18n.HERE) / "i18n.json").read_text("utf-8"))
    assert all(len(r) == 2 and all(x.strip() for x in r) for r in rows)


def test_html_translation_keeps_data_and_scripts() -> None:
    html = '<nav><span>Visão geral</span> <b>Notebook Dell</b><input placeholder="Buscar"><style>.x{content:"Visão geral"}</style></nav>'
    en = i18n.tr_html("en", html)
    assert "<span>Overview</span>" in en and "<b>Notebook Dell</b>" in en
    assert 'content:"Visão geral"' in en
    assert i18n.tr_html("pt-BR", html) == html


@pytest.mark.parametrize(
    ("src", "out"),
    [
        ("2.055 abertos", "2,055 abertos"),
        ("3,6h", "3,6h"),
        ("3,6 h", "3.6 h"),
        ("R$ 1.234,56", "R$ 1,234.56"),
        ("05/01/2017", "01/05/2017"),
        ("SD-2026-1991842", "SD-2026-1991842"),
        ("PT-2016-0000010", "PT-2016-0000010"),
        ("v1.7.9 e 10.231.0.1", "v1.7.9 e 10.231.0.1"),
        ("300.000 ativos.", "300,000 ativos."),
        ("86,1% do total", "86.1% do total"),
        ("12 itens", "12 itens"),
    ],
)
def test_en_numbers_follow_locale(src: str, out: str) -> None:
    assert i18n.en_numbers(src) == out


def test_personalize_uses_identity() -> None:
    out = i18n.personalize(
        "{{HUB_FIRST}}|{{HUB_INITIALS}}|{{HUB_APP_NAME}}|{{HUB_USER}}", "Ana Administradora", "pt-BR"
    )
    assert out == "Ana|AA|Gestão de Ativos|Ana Administradora"
    assert i18n.personalize("{{HUB_USER}}", "<b>x</b>", "pt-BR") == "&lt;b&gt;x&lt;/b&gt;"
    assert re.match(r"^[A-ZÀ-Ú-]+, \d{1,2} [A-Z]{3} \d{4}$", i18n.today_label("pt-BR"))


# ---- import: validator and zip ---------------------------------------------------------


def _header() -> list[str]:
    return [c.header for c in COLS]


def test_validator_flags_bad_rows_and_duplicates() -> None:
    v = importer.Validator(["Patrimônio", "Nome", "Categoria", "Localização", "Status", "Extra"])
    ok = v.check(2, ["A-1", "Notebook", "Tecnologia", "Recife", "Em uso", "x"])
    assert ok is not None and ok[0] is not None and ok[1] == [] and ok[0][0] == "A-1"
    assert v.check(3, [None, "", "", "", "", ""]) is None  # blank row
    bad = v.check(4, ["A-2", "Mesa", "Brinquedos", "Recife", "Em uso", ""])
    assert bad is not None and bad[0] is None and bad[1][0][1] == "Categoria"
    dup = v.check(5, ["A-1", "Outra", "Tecnologia", "Recife", "Em uso", ""])
    assert dup is not None and dup[1][0][3] == "patrimônio duplicado na planilha"
    with pytest.raises(JobFail, match="Colunas obrigatórias ausentes: Patrimônio"):
        importer.Validator(["Nome", "Categoria", "Localização", "Status"])


def test_reader_roundtrip_with_generated_sheet(tmp_path: Path) -> None:
    p = tmp_path / "a.xlsx"
    importer.generate_xlsx(p, 200, 50, "ZZ-")
    r = importer.count_xlsx(p)
    assert r["total"] == 200 and r["valid"] + r["rejected"] == 200 and 0 < r["rejected"] < 40
    # F17: the spreadsheet carries all statuses (the E2E exports only the "Available" ones).
    from openpyxl import load_workbook

    ws = load_workbook(p, read_only=True).active
    assert ws is not None
    seen = {row[4] for row in ws.iter_rows(min_row=2, values_only=True)}
    assert set(model.STATUSES) <= seen


def test_template_has_headers_and_three_rows(tmp_path: Path) -> None:
    p = tmp_path / "m.xlsx"
    importer.template_xlsx(p)
    assert importer.count_xlsx(p) == {"total": 3, "valid": 3, "rejected": 0}


def test_zip_with_too_many_entries_and_not_xlsx_are_rejected(tmp_path: Path) -> None:
    p = tmp_path / "z.xlsx"
    with zipfile.ZipFile(p, "w") as z:
        for i in range(xlsx_guard.MAX_ZIP_ENTRIES + 1):
            z.writestr(f"f{i}.txt", "x")
    with pytest.raises(JobFail, match="itens no arquivo compactado"):
        xlsx_guard.check_zip(p)
    q = tmp_path / "n.xlsx"
    q.write_bytes(b"isto nao e um zip")
    with pytest.raises(JobFail, match="não é uma planilha"):
        xlsx_guard.check_zip(q)


def test_zip_bomb_is_rejected_before_calamine(tmp_path: Path) -> None:
    p = tmp_path / "b.xlsx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"0" * (xlsx_guard.MAX_ZIP_ENTRY_UNCOMPRESSED // 4))
    with pytest.raises(JobFail, match=r"compressão suspeita|descomprime"):
        xlsx_guard.check_zip(p)
