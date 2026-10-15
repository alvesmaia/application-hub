"""Units without HTTP or DB: model, report, sessions, nav, search, CLI."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from servicos import __main__ as cli
from servicos import report, views
from servicos.model import CATEGORIES, pick, sla_for, sla_text, status_class
from servicos.search import fold, generic_term
from servicos.seed import seed_rows
from servicos.session import Sessions
from servicos.state import to_i64
from servicos.ui import notice, query, thousands, wrap


def test_pick_sla_and_classes() -> None:
    assert pick("Alta", ("Baixa", "Alta")) == "Alta" and pick("x", ("Baixa",)) == ""
    assert sla_for("Crítica") == timedelta(hours=4) and sla_for("Baixa") == timedelta(hours=72)
    assert status_class("Aguardando") == "waiting" and status_class("Aberto") == ""
    now = datetime.now(UTC)
    assert sla_text("Resolvido", now) == "—"
    assert sla_text("Aberto", now - timedelta(hours=1)) == "Vencido"
    assert sla_text("Aberto", now + timedelta(hours=5, minutes=30, seconds=30)) == "05:30"


def test_int64_parsing() -> None:
    assert to_i64("42") == 42 and to_i64("-3") == -3 and to_i64("") is None
    assert to_i64("9" * 30) is None and to_i64("1e3") is None and to_i64("٣") is None


def test_params_are_sanitized_and_bounded() -> None:
    p = report.Params.from_query(
        {"de": "2020-01-01", "ate": "2026-09-01", "agrupar": "hora; DROP", "categoria": "Nada"}
    )
    assert p.gran == "mes" and p.category == "" and (p.to - p.from_).days == report.MAX_DAYS
    inv = report.Params.from_query({"de": "2026-09-10", "ate": "2026-09-01"})
    assert inv.from_ == inv.to and inv.trunc() == "month"
    assert report.Params.from_query({"agrupar": "semana"}).trunc() == "week"
    assert report.Params.from_query({"agrupar": "dia"}).trunc() == "day"
    d = report.Params.from_query({"ate": "2026-09-01", "categoria": CATEGORIES[0]})
    assert (d.to - d.from_).days == 180 and "Tecnologia" in d.describe() and "categoria=Tecnologia" in d.qs()
    assert d.key().startswith("2026-03-05|2026-09-01|mes|")


def _report() -> report.Report:
    return report.Report([report.Row("Acessos", date(2026, 9, 1), 10, 8, 3.25, 7)], 1)


def test_csv_and_render() -> None:
    c = report.csv(_report())
    assert c.startswith("﻿periodo;") and "2026-09-01;Acessos;10;8;3,25;7\r\n" in c
    p = report.Params.from_query({"de": "2026-09-01", "ate": "2026-09-30", "agrupar": "semana"})
    h = report.render(_report(), p, True)
    assert "1 linhas" in h and "(cache da sessão)" in h and "sem. de 01/09/2026" in h and "80,0%" in h
    assert "Sem chamados" in report.render(report.Report([], 0), p, False)
    assert report.pct(1, 0) == "—" and report.pct(1, 3) == "33,3%"
    assert report.period_label(p, date(2026, 9, 1)) != report.period_label(
        report.Params.from_query({"agrupar": "dia"}), date(2026, 9, 1)
    )


def test_sessions_lru_is_bounded_and_keeps_recent_reports() -> None:
    s = Sessions(2, 2)
    s.touch("a", lambda _: None)
    s.touch("b", lambda _: None)
    s.touch("a", lambda _: None)  # "a" is the most recent
    s.touch("c", lambda _: None)  # descarta "b"
    assert s.stats().sessions == 2 and s.get_report("b", "x") is None
    r = _report()
    for k in ("p1", "p2", "p3"):
        s.put_report("a", k, r)
    assert s.get_report("a", "p3") is r and s.get_report("a", "p1") is None  # only 2 per session
    assert s.stats().approx_bytes > 0 and s.get_report("a", "nada") is None


def test_sessions_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_SESSION_CAPACITY", "7")
    monkeypatch.setenv("HUB_SESSION_REPORTS", "x")
    s = Sessions.from_env()
    assert s.capacity == 7 and s.per_session == 3


def test_nav_injection_and_helpers() -> None:
    body = (
        '<nav><a class="nav-item active" href="/apps/servicos/requests">S</a>'
        '<a class="nav-item " href="/apps/servicos/settings">C</a></nav>'
    )
    out = views.inject_nav(body, "requests", "/apps/servicos")
    assert out.index("Relatórios") < out.index("settings")
    assert 'class="nav-item active" href="/apps/servicos/requests"' in out
    rep = views.inject_nav(body, "reports", "/apps/servicos")
    assert 'class="nav-item active" href="/apps/servicos/reports"' in rep and 'class="nav-item "' in rep
    assert views.inject_nav("<p>sem sidebar</p>", "requests", "/apps/servicos") == "<p>sem sidebar</p>"
    assert "selected>Alta" in views.options(("Baixa", "Alta"), "Alta")
    assert "Todos" in views.options((), "", "Todos")
    assert thousands(12345678) == "12.345.678" and thousands(-5) == "-5"
    assert query([("a", "x y"), ("b", "")]) == "?a=x%20y" and query([]) == ""
    assert "hub-notice-ok" in notice("ok", "t", "<x>") and "&lt;x&gt;" in notice("ok", "t", "<x>")
    assert wrap("<p>x</p>", "novo") == "novo"


def test_search_terms_and_seed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    assert fold("  Solicitações ") == "solicitacoes" and generic_term("Chamados") and generic_term("tick")
    assert not generic_term("vpn") and not generic_term("abcdefg")
    monkeypatch.delenv("SERVICOS_SEED_ROWS", raising=False)
    monkeypatch.setenv("HUB_SEED_SCALE", "0.5")
    assert seed_rows() == 1_000_000
    monkeypatch.setenv("HUB_SEED_SCALE", "abc")
    assert seed_rows() == 2_000_000
    monkeypatch.setenv("SERVICOS_SEED_ROWS", "77")
    assert seed_rows() == 77
    monkeypatch.setenv("SERVICOS_SEED_ROWS", "x")
    assert seed_rows() == 0


def test_cli_openapi_and_usage(env: object, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["openapi"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["info"]["title"] == "Central de Serviços — API"
    assert cli.main(["nada"]) == 2
    assert cli.main(["--health"]) in (0, 1)
