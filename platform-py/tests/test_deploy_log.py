"""admin/deploys: each deployment keeps its log (pipeline steps, build, rollout and the result) in its record, shown in
the version's side panel; a later action on the same deployment (approval) appends to it, so the history stays."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from test_ops import JSON, hub, package, upload  # noqa: F401 (fixture)


def test_import_log_is_kept_in_the_deployment_and_shown_in_the_side_panel(hub: TestClient) -> None:  # noqa: F811
    dep = upload(hub, package()).json()["result"]["deployment"]
    rec = next(d for d in hub.get("/api/ops/deployments").json() if d["id"] == dep["id"])
    log = rec["log"]
    assert any("Manifest" in ln for ln in log) and any("Importação" in ln for ln in log)
    assert all(
        re.match(r"^\[\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\]: ", ln) for ln in log
    )  # "[date time]: log"
    page = hub.get("/admin/deploys").text
    assert (
        f'data-ops-ver-log="{dep["id"]}"' in page
        and "Manifest" in page.split(f'data-ops-ver-log="{dep["id"]}"', 1)[1]
    )


def test_approval_appends_to_the_same_log(hub: TestClient) -> None:  # noqa: F811
    dep = upload(hub, package()).json()["result"]["deployment"]
    # no engine in the test: the rollout fails (422), the log still records it
    assert hub.post(f"/api/ops/deployments/{dep['id']}/approve?wait=true", headers=JSON).status_code in (
        200,
        422,
    )
    rec = next(d for d in hub.get("/api/ops/deployments").json() if d["id"] == dep["id"])
    heads = [ln for ln in rec["log"] if "]: Início:" in ln]
    assert len(heads) == 2 and "Importação" in heads[0] and "Aprovação" in heads[1]


def test_the_validation_pipeline_stays_in_the_side_panel_after_the_import(hub: TestClient) -> None:  # noqa: F811
    dep = upload(hub, package()).json()["result"]["deployment"]
    rec = next(d for d in hub.get("/api/ops/deployments").json() if d["id"] == dep["id"])
    assert [s["name"] for s in rec["pipeline"]][:2] == ["Manifest", "Assinatura"]
    panel = (
        hub.get("/admin/deploys")
        .text.split(f'data-ops-ver-pipeline="{dep["id"]}"', 1)[1]
        .split("</ol>", 1)[0]
    )
    assert '<li class="done">' in panel and "Manifest" in panel and "Aprovação" in panel
