"""Both project templates serve the app's own fonts and images (`assets/files/<name>`, through the SDK's
`asset_file`): adapting an existing app keeps its fonts and icons instead of trading them for the hub's."""

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "hub_cli" / "templates"


@pytest.mark.parametrize("stack", ["python-fastapi", "python-fastapi.dashboard"])
def test_template_serves_the_apps_own_files(stack: str) -> None:
    app_py = (TEMPLATES / stack / "src" / "__PKG__" / "app.py").read_text(encoding="utf-8")
    assert '@app.get(f"/{APP_ID}/assets/files/{{name}}")' in app_py
    assert 'asset_file(HERE / "assets" / "files", name)' in app_py
    # the dashboard stack overlays the base template: the folder comes from there
    assert (TEMPLATES / "python-fastapi" / "src" / "__PKG__" / "assets" / "files" / ".gitkeep").exists()
