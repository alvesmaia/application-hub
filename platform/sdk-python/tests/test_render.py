from pathlib import Path

from starlette.requests import Request

from hub_sdk.identity import Identity
from hub_sdk.render import (
    AppInfo,
    NavItem,
    Renderer,
    busy,
    csv_field,
    csv_line,
    download_headers,
    money_cents,
    public_base,
    thousands,
)

APP = AppInfo(id="demo", name="Demo <App>", nav=(NavItem("dashboard", "Dashboard", "dashboard"),))


def req(user: str | None) -> Request:
    headers = [] if user is None else [(b"x-hub-user", Identity(user=user, roles=()).encode().encode())]
    return Request(
        {"type": "http", "method": "GET", "path": "/demo", "headers": headers, "query_string": b""}
    )


def test_workspace_uses_shell_classes_and_escapes(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text(
        '{% extends "workspace.html" %}{% block content %}<p>{{ msg }}</p>{% endblock %}', encoding="utf-8"
    )
    r = Renderer(APP, tmp_path)
    resp = r.page(req("<b>ana</b>"), "page.html", "dashboard", msg="<script>x</script>")
    body = bytes(resp.body).decode()
    for cls in (
        'class="app-sidebar"',
        'class="workspace-content demo-content"',
        "mobile-menu-button",
        "nav-item active",
    ):
        assert cls in body
    assert "<script>" not in body and "&lt;script&gt;" in body
    assert "&lt;b&gt;ana&lt;/b&gt;" in body
    assert "Demo &lt;App&gt;" in body
    assert resp.headers["cache-control"] == "no-store"
    guest = bytes(r.page(req(None), "page.html", "dashboard", msg="").body).decode()
    assert "convidado" in guest


def test_busy_is_429_with_retry_after() -> None:
    r = busy("A exportação", "Exportação em fila…")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "10"
    assert 'data-hub-queued="Exportação em fila…"' in bytes(r.body).decode()


def test_formats_and_csv() -> None:
    assert thousands(2_000_000) == "2.000.000"
    assert money_cents(123_456) == "R$ 1.234,56"
    assert csv_field("a;b") == '"a;b"'
    assert csv_field('diz "oi"') == '"diz ""oi"""'
    assert csv_field("=HYPERLINK()") == "'=HYPERLINK()"
    assert csv_field("-12.5") == "-12.5"
    assert csv_field("\t=1+1") == "'\t=1+1"
    assert csv_field("\r+CMD()") == '"\'\r+CMD()"'
    assert csv_line(["a", "b"]) == "a;b\r\n"
    h = download_headers("text/csv", "contratos 2026.csv")
    assert h["content-disposition"] == 'attachment; filename="contratos_2026.csv"'
    assert h["x-content-type-options"] == "nosniff"


def with_base(value: str | None) -> Request:
    headers = [] if value is None else [(b"x-hub-base", value.encode())]
    return Request(
        {"type": "http", "method": "GET", "path": "/demo", "headers": headers, "query_string": b""}
    )


def test_public_base_follows_the_hub_header_and_refuses_odd_values() -> None:
    assert public_base(with_base("/apps/demo-alt"), "demo") == "/apps/demo-alt"
    assert public_base(with_base(None), "demo") == "/apps/demo"
    for odd in ("/elsewhere", '/apps/x"><script>', "/apps/a b", "//evil.example/apps"):
        assert public_base(with_base(odd), "demo") == "/apps/demo", odd


def test_page_links_use_the_request_base(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text('<a href="{{ base }}/x">x</a>', encoding="utf-8")
    r = Renderer(APP, tmp_path)
    assert (
        'href="/apps/demo-alt/x"'
        in bytes(r.page(with_base("/apps/demo-alt"), "page.html", "d").body).decode()
    )
    assert 'href="/apps/demo/x"' in bytes(r.page(with_base(None), "page.html", "d").body).decode()
