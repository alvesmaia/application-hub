"""F22: public CLI package index, documents and the Developers screen."""

import re
from pathlib import Path

import pytest
from api_helper import api_hub
from fastapi.testclient import TestClient

from hub.ops import devdocs, devkit


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "hub_cli-0.2.0-py3-none-any.whl").write_bytes(b"PK-cli")
    (dist / "hub_sdk-1.0.0-py3-none-any.whl").write_bytes(b"PK-sdk")
    (dist / "outro.txt").write_text("x")
    monkeypatch.setenv("HUB_CLI_DIST", str(dist))
    return api_hub(tmp_path / "data", monkeypatch)


def test_simple_index_is_public(hub: TestClient) -> None:
    anon = TestClient(hub.app)
    root = anon.get("/dev/cli/simple/")
    assert root.status_code == 200 and 'href="hub-cli/"' in root.text and 'href="hub-sdk/"' in root.text
    proj = anon.get("/dev/cli/simple/hub-cli/")
    assert "hub_cli-0.2.0-py3-none-any.whl#sha256=" in proj.text
    assert anon.get("/dev/cli/simple/nada/").status_code == 404
    assert anon.get("/dev/cli/files/hub_cli-0.2.0-py3-none-any.whl").content == b"PK-cli"
    assert anon.get("/dev/cli/files/outro.txt").status_code == 404
    r = anon.get("/dev/docs/agente.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "hub project create <id-do-app> --stack python-fastapi --yes --json" in r.text
    assert "http://testserver/dev/cli/simple/" in r.text and devdocs.HUB_MARK not in r.text
    assert anon.get("/dev/docs/contrato.md").status_code == 200


def test_docs_are_public_without_shell(hub: TestClient) -> None:
    anon = TestClient(hub.app)
    page = anon.get("/developers", follow_redirects=False)
    assert page.status_code == 200 and "dev-public" in page.text and "/login?next=/developers" in page.text
    assert "hub-user-menu" not in page.text and "platform-rail" not in page.text
    assert (
        '/admin/tokens"' in page.text
        and "data-hub-palette" not in page.text
        and "Ver em Markdown</a></div>" not in page.text
    )
    assert "http://testserver/dev/docs/agente.md" in page.text
    assert anon.get("/developers/agente").status_code == 200
    llms = anon.get("/llms.txt")  # index for agents, public like the pages
    assert llms.status_code == 200 and "http://testserver/dev/docs/guia-tempo-real.md" in llms.text
    assert anon.get("/dev/llms.txt").text == llms.text  # same index under /dev/* (allowed in proxies/Access)
    assert anon.post("/developers", follow_redirects=False).status_code != 200
    logged = hub.get(
        "/developers"
    ).text  # signed in: same standalone layout, link back to the hub instead of "Sign in"
    assert "platform-rail" not in logged and "hub-user-menu" not in logged and "/login?next=" not in logged
    assert 'data-dev-back-hub="" href="/"' in logged
    assert 'type="text/markdown" href="/dev/docs/comecar.md"' in logged


def test_docs_private_when_disabled(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_PUBLIC_DOCS", "0")
    anon = TestClient(hub.app)
    for url in ("/developers", "/developers/cli", "/dev/docs/agente.md"):
        r = anon.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), url
    assert anon.get("/dev/cli/simple/").status_code == 200


def test_no_owner_brand_in_ui(hub: TestClient) -> None:
    import re

    bad = re.compile(r"alves\s*maia", re.I)
    cat = (Path(__file__).parents[1] / "hub/web/catalog.json").read_text(encoding="utf-8")
    assert not bad.search(cat)
    for url in ("/", "/developers", "/applications", "/manifest.webmanifest"):
        assert not bad.search(hub.get(url).text), url


def test_developers_page_and_docs(hub: TestClient) -> None:
    r = hub.get("/developers")
    assert r.status_code == 200
    assert "http://testserver/dev/cli/simple/" in r.text and "hl-" in r.text
    assert "hub-cli 0.2.0" not in r.text and "python-fastapi" in r.text
    assert (
        "data-hub-copy-prompt" in r.text
        and "Siga as instruções em http://testserver/dev/docs/agente.md" in r.text
    )
    assert "staging" in r.text and 'aria-current="page"' in r.text
    assert hub.get("/dev/docs/agents.md").text.startswith("# AGENTS.md")
    assert hub.get("/dev/docs/nada.md").status_code == 404
    raw = hub.get("/dev/docs/comecar.md").text
    assert "http://testserver/dev/cli/simple/" in raw and devdocs.HUB_MARK not in raw


@pytest.mark.parametrize("slug", ["cli", "sdk", "guias", "contrato", *[g.slug for g in devdocs.GUIDES]])
def test_developers_sections(hub: TestClient, slug: str) -> None:
    r = hub.get(f"/developers/{slug}")
    assert r.status_code == 200 and 'class="dev-doc"' in r.text and "data-hub-copy-prompt=" not in r.text
    doc = devdocs.by_slug(slug) or devdocs.GUIDES[0]
    assert hub.get(f"/dev/docs/{doc.raw}.md").status_code == 200
    assert hub.get("/developers/nada").status_code == 404


def test_developers_languages(hub: TestClient) -> None:
    en = hub.get("/developers", headers={"accept-language": "en"}).text
    assert (
        "Copy prompt" in en
        and "Follow the instructions at" in en
        and "This documentation is in Portuguese" in en
    )
    es = hub.get("/developers/cli/hub-dev", headers={"accept-language": "es"}).text  # `es` dropped: now en
    assert "Guides" in es and ">Copy<" in es


def test_render_md_anchors_code_and_no_html() -> None:
    html, toc = devdocs.render_md(
        "# T\n\n## Um\n\n## Um\n\n<script>x</script>\n\n```bash\nhub dev\n```\n\n|a|\n|-|\n|b|\n"
    )
    assert '<h1 id="t">' in html and 'id="um-1"' in html and toc == [(2, "Um", "um"), (2, "Um", "um-1")]
    assert "<script>" not in html and "data-dev-copy" in html and 'class="dev-table"' in html
    hl, _ = devdocs.render_md("```python\nimport os\n```\n\n```\nraw <b>\n```\n")
    assert (
        "hl-kn" in hl and "dev-code-lang" not in hl and "<b>" not in hl and "&lt;" in hl
    )  # no language: inferred


def test_bash_highlight_marks_commands_options_continuation() -> None:
    h = devdocs.highlight("curl -sS \\\n  -H 'a' | jq .x && hub deploy $ID\n", "bash")
    for want in ('hl-nf">curl<', 'hl-nf">jq<', 'hl-nf">hub<', 'hl-na">-sS<', 'hl-o">\\<', 'hl-nv">$ID<'):
        assert want in h, want
    assert 'hl-nf">deploy' not in h


def test_generated_references_are_current() -> None:
    for path, text in devdocs.generated().items():
        assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == text, (
            f"rode `uv run python -m hub.ops.devdocs` ({path.name})"
        )


def test_dist_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_CLI_DIST", raising=False)
    assert devkit.dist_dir().name == "cli"


def test_docs_never_show_the_rail_and_bash_flow(hub: TestClient) -> None:
    for url in ("/developers", "/developers/guias", "/developers/cli"):  # admin signed in: no hub sidebar
        html = hub.get(url).text
        assert (
            "platform-rail" not in html and "data-rail-ctx" not in html and "rail-hidden dev-public" in html
        ), url
    out = devdocs.highlight("for a in x; do echo $a; done\nif hub check; then hub dev; fi\n", "bash")
    assert '<span class="hl-nf">echo</span>' in out and '<span class="hl-nf">hub</span>' in out
    assert '<span class="hl-nf">a</span>' not in out


def test_split_docs_one_page_per_section(hub: TestClient) -> None:
    kids = [d for d in devdocs.all_docs() if d.part]
    assert {d.section for d in kids} == {"cli", "sdk", "contrato"} and len({d.slug for d in kids}) == len(
        kids
    )
    page = hub.get("/developers/contrato/2-health-and-readiness").text
    assert (
        "<h1" in page and "Health and readiness" in page and "Routes, fragments" in page
    )  # 3rd section only in the menu
    cli = hub.get("/developers/cli").text
    assert 'href="/developers/cli/hub-dev"' in cli and "Lightweight local hub" in cli  # index with summary
    assert 'href="/dev/docs/cli.md"' in cli  # full text on a single page


def test_every_code_box_and_signature_is_colored() -> None:
    for d in devdocs.all_docs():
        text = devdocs.source(d, "https://hub")
        html = devdocs.render_md(text)[0] if text else ""
        assert all("hl-" in pre for pre in re.findall(r"<pre.*?</pre>", html, re.S)), d.slug
        assert not re.search(r"<h[2-4][^>]*>(class )?<code>", html), d.slug


def test_developers_lives_in_the_user_menu_not_the_rail(hub: TestClient) -> None:
    html = hub.get("/admin/users").text
    rail = html.split('<aside class="platform-rail"', 1)[1].split('<div class="hub-drawer-account', 1)[0]
    popover = html.split('class="header-popover hub-user-popover"', 1)[1].split("</details>", 1)[0]
    assert 'href="/developers"' not in rail
    link = popover.split('href="/developers"', 1)[1].split("</a>", 1)[0]
    assert 'target="_blank" rel="noopener" data-hub-developers=""' in link and "Desenvolvedores" in link
    assert '<span class="visually-hidden"> (abre em nova guia)</span>' in link
    en = hub.get("/admin/users", headers={"accept-language": "en"}).text
    assert "(opens in a new tab)" in en


def test_developers_menu_entry_follows_the_old_rail_rule() -> None:
    from hub.ux.shell import can_open

    assert can_open("/developers", ["editar_configuracoes"])
    assert not can_open("/developers", ["ver_auditoria"]) and not can_open("/developers", [])
