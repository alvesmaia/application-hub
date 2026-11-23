"""Pure rules: run state machine, field specs and parameter validation, CPF/CNPJ check digits,
next schedule fire, friendly formatting."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from hub_sdk import locale
from pydantic import ValidationError

from automations import model, present
from automations.fields import FieldSpec, validate
from automations.schedule_rules import ScheduleIn, describe, next_run, parse_schedule
from tests.samples import manifest, sample_module

documents = sample_module("validar-documentos", "documentos")

# ---- run state machine ------------------------------------------------------------------


def test_terminal_statuses_never_move_again() -> None:
    for s in model.TERMINAL:
        for d in model.STATUSES:
            assert not model.can_move(s, d), (s, d)


def test_allowed_transitions() -> None:
    assert model.can_move("pendente", "processando")
    assert model.can_move("pendente", "cancelado")
    assert model.can_move("processando", "concluido")
    assert model.can_move("processando", "erro")
    assert not model.can_move("pendente", "concluido"), "a run only finishes after it started"
    assert not model.can_move("processando", "pendente")
    assert set(model.sources("concluido")) == {"processando"}
    assert set(model.sources("erro")) == {"pendente", "processando"}


# ---- field specs (self-check at import) ---------------------------------------------------


def test_every_sample_manifest_has_valid_fields() -> None:
    for name in ("consultar-cep", "feriados-nacionais", "validar-documentos"):
        m = manifest(name)
        assert m.fields and len({f.name for f in m.fields}) == len(m.fields)


@pytest.mark.parametrize(
    "spec",
    [
        {"name": "x", "label": "X", "type": "select"},  # select without options
        {"name": "x", "label": "X", "type": "text", "options": [["a", "A"]]},
        {"name": "x", "label": "X", "type": "number", "min": 5, "max": 1},
        {"name": "x", "label": "X", "type": "text", "pattern": "("},
        {"name": "x", "label": "X", "type": "text", "max_lines": 3},
        {"name": "X-1", "label": "X", "type": "text"},
        {"name": "x", "label": "X", "type": "color"},
        {"name": "x", "label": "X", "type": "text", "extra": 1},
    ],
)
def test_invalid_field_specs_are_refused(spec: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FieldSpec.model_validate(spec)


# ---- parameter validation ----------------------------------------------------------------

FIELDS = [
    FieldSpec(name="nome", label="Nome", type="text", required=True, pattern=r"[a-z]+"),
    FieldSpec(name="qtd", label="Qtd", type="number", min=1, max=10),
    FieldSpec(name="cor", label="Cor", type="select", options=(("a", "Azul"), ("v", "Verde"))),
    FieldSpec(name="ok", label="Ok", type="checkbox"),
    FieldSpec(name="texto", label="Texto", type="textarea", max_lines=2),
    FieldSpec(name="dia", label="Dia", type="date"),
]


def test_valid_params_are_coerced() -> None:
    values, errors = validate(
        FIELDS, {"nome": " abc ", "qtd": "3", "cor": "v", "ok": "1", "texto": "a\n\nb", "dia": "2026-11-23"}
    )
    assert errors == {}
    assert values == {"nome": "abc", "qtd": 3, "cor": "v", "ok": True, "texto": "a\n\nb", "dia": "2026-11-23"}


def test_errors_are_per_field() -> None:
    _, errors = validate(
        FIELDS, {"nome": "ABC", "qtd": "99", "cor": "x", "texto": "a\nb\nc", "dia": "23/11/2026"}
    )
    assert set(errors) == {"nome", "qtd", "cor", "texto", "dia"}
    assert errors["qtd"] == "Informe um valor entre 1 e 10."
    _, errors = validate(FIELDS, {"qtd": "abc"})
    assert errors == {"nome": "Campo obrigatório.", "qtd": "Informe um número."}


def test_optional_empty_values_and_unchecked_checkbox() -> None:
    values, errors = validate(FIELDS, {"nome": "a", "qtd": "", "cor": ""})
    assert errors == {}
    assert values["qtd"] is None and values["cor"] is None and values["ok"] is False


def test_decimal_comma_and_defaults_for_missing_keys() -> None:
    f = [
        FieldSpec(name="v", label="V", type="number", default=7),
        FieldSpec(name="w", label="W", type="number"),
    ]
    values, _ = validate(f, {"w": "2,5"})
    assert values == {"v": 7, "w": 2.5}


def test_messages_follow_the_request_language() -> None:
    tok = locale.use("en")
    try:
        _, errors = validate(FIELDS, {})
    finally:
        locale._current.reset(tok)
    assert errors["nome"] == "Required field."


def test_cep_field_accepts_both_shapes() -> None:
    fields = manifest("consultar-cep").fields
    assert validate(fields, {"cep": "01001-000"})[1] == {}
    assert validate(fields, {"cep": "01001000"})[1] == {}
    assert "cep" in validate(fields, {"cep": "0100-1000"})[1]


def test_documentos_field_line_limit_ignores_blank_lines() -> None:
    fields = manifest("validar-documentos").fields
    ok = "\n".join(["52998224725", ""] * 500)
    assert validate(fields, {"documentos": ok, "tipo": "auto"})[1] == {}
    too_many = "\n".join(["52998224725"] * 501)
    assert "documentos" in validate(fields, {"documentos": too_many, "tipo": "auto"})[1]


def test_feriados_year_range() -> None:
    fields = manifest("feriados-nacionais").fields
    assert validate(fields, {"ano": "1899"})[1] == {"ano": "Informe um valor entre 1900 e 2199."}
    assert validate(fields, {})[0]["ano"] == 2026


# ---- CPF / CNPJ --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc", "kind", "ok"),
    [
        ("529.982.247-25", "cpf", True),
        ("52998224724", "cpf", False),
        ("111.111.111-11", "cpf", False),
        ("11.222.333/0001-81", "cnpj", True),
        ("11222333000182", "cnpj", False),
        ("00000000000000", "cnpj", False),
        ("12.ABC.345/01DE-35", "cnpj", True),  # alphanumeric CNPJ (2026 format)
        ("123", None, False),
    ],
)
def test_check_digits(doc: str, kind: str | None, ok: bool) -> None:
    r = documents.check(doc, "auto")
    assert (r.kind, r.valid) == (kind, ok)


def test_forced_kind_and_summary() -> None:
    assert documents.check("52998224725", "cnpj").valid is False
    out = documents.validate_lines("529.982.247-25\n\n11222333000182\n", "auto")
    assert out["total"] == 2 and out["validos"] == 1 and out["invalidos"] == 1
    assert out["resultados"][1] == {
        "linha": 3,
        "documento": "11222333000182",
        "tipo": "CNPJ",
        "valido": False,
    }


# ---- schedules ---------------------------------------------------------------------------

BRT = timedelta(hours=-3)


def test_interval_next_run() -> None:
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    rule = ScheduleIn(kind="intervalo", every_minutes=15)
    assert next_run(rule, now) == now + timedelta(minutes=15)


def test_daily_next_run_same_day_and_rollover() -> None:
    rule = ScheduleIn(kind="diario", at_time="08:00")
    morning = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)  # 07:00 in São Paulo
    assert next_run(rule, morning) == datetime(2026, 11, 23, 11, 0, tzinfo=UTC)
    after = datetime(2026, 11, 23, 11, 0, tzinfo=UTC)  # exactly 08:00 → tomorrow
    assert next_run(rule, after) == datetime(2026, 11, 24, 11, 0, tzinfo=UTC)
    late = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)  # 20:30 local, year rollover
    assert next_run(rule, late) == datetime(2027, 1, 1, 11, 0, tzinfo=UTC)


def test_daily_next_run_has_no_dst_shift() -> None:
    # São Paulo has no DST: 08:00 local is always 11:00 UTC, January and July alike.
    rule = ScheduleIn(kind="diario", at_time="08:00")
    for month in (1, 7):
        nxt = next_run(rule, datetime(2027, month, 10, 0, 0, tzinfo=UTC))
        assert nxt.astimezone(UTC).hour == 11


def test_schedule_form_validation() -> None:
    rule, errors = parse_schedule({"tipo": "intervalo", "intervalo": "4"})
    assert rule is None and errors == {"intervalo": "Informe de 5 a 1440 minutos."}
    rule, errors = parse_schedule({"tipo": "diario", "horario": "25:00"})
    assert rule is None and errors == {"horario": "Informe o horário no formato HH:MM."}
    rule, errors = parse_schedule({"tipo": "semanal"})
    assert rule is None and set(errors) == {"tipo"}
    rule, errors = parse_schedule({"tipo": "diario", "horario": "8:05", "intervalo": "x"})
    assert errors == {} and rule is not None and rule.at_time == "08:05" and rule.every_minutes is None


def test_describe() -> None:
    assert describe(ScheduleIn(kind="intervalo", every_minutes=15)) == "a cada 15 min"
    assert describe(ScheduleIn(kind="diario", at_time="08:00")) == "diário às 08:00"


# ---- friendly formatting -----------------------------------------------------------------


def test_humanize_and_display() -> None:
    assert present.humanize("data_de_inicio") == "Data de inicio"
    assert present.display(True) == "Sim" and present.display(False) == "Não"
    assert present.display(None) == "—" and present.display("") == "—"
    assert present.display("2026-11-02") == "02/11/2026"
    assert present.display(["a", "b"]) == "a, b"
    assert present.display(2.5) == "2,5"


def test_rows_turn_lists_of_objects_into_tables() -> None:
    rows = present.rows(
        {"ano": 2026, "feriados": [{"data": "2026-01-01", "nome": "Ano novo"}]}, present.humanize
    )
    assert rows[0].label == "Ano" and rows[0].text == "2026"
    table = rows[1]
    assert table.columns == ("Data", "Nome") and table.cells == (("01/01/2026", "Ano novo"),)


def test_duration_and_when() -> None:
    assert present.duration(None) == "—"
    assert present.duration(850) == "850 ms"
    assert present.duration(2300) == "2,3 s"
    assert present.duration(65000) == "1 min 5 s"
    assert present.when(datetime(2026, 11, 23, 11, 5, tzinfo=UTC)) == "23/11/2026 08:05"
    assert present.when(None) == "—"
    assert present.display(date(2026, 1, 2)) == "02/01/2026"


def test_new_process_form_preselects_a_package_and_its_newest_version() -> None:
    """The version select depends on the package one: empty until a package was chosen (ui-sweep "select-vazio")."""
    from automations.routes.admin_processes import preselect

    versions = {"consultar-cep": ["1.1.0", "1.0.0"], "feriados": ["2.0.0"]}
    assert preselect({"tempo": "300"}, versions) == {
        "tempo": "300",
        "pacote": "consultar-cep",
        "versao": "1.1.0",
    }
    chosen = {"pacote": "feriados", "versao": "2.0.0"}
    assert preselect(chosen, versions) == chosen  # a submitted form keeps what the person chose
    assert preselect({}, {}) == {}  # no package uploaded yet
