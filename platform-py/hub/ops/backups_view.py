"""View rows of the Backups page (`/admin/backups`): pure functions of the Operations store."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from hub.admin.routes import relative
from hub.ops import model as m
from hub.ops.model import OpsError
from hub.ops.opsctx import br, tone
from hub.ops.schedule import local_label, normalize, timezone_of

Row = dict[str, Any]
NameOf = Callable[[str], str]


def _run_label(p: Row, env: str, st: Row | None, manual: bool, now: int) -> str:
    lbl = "staging" if env == "staging" else "produção"
    last = (
        f" · último: {st['last_status']} ({relative(st['last_at'], now)})"
        if st and st.get("last_status")
        else ""
    )
    if manual:
        return f"{lbl}: manual"
    if st and st.get("error"):
        return f"{lbl}: agenda inválida ({st['error']})"
    if st and st.get("next_due"):
        return f"{lbl}: próximo {local_label(st['next_due'], timezone_of(p.get('tz', '')))}{last}"
    return f"{lbl}: aguardando o agendador"


def policy_row(p: Row, schedules: Row, name_of: NameOf, now: int) -> Row:
    try:
        manual = normalize(p["schedule"]) is None
    except OpsError:
        manual = False  # invalid schedule: the error appears on the environment's row
    runs = [_run_label(p, env, schedules.get(f"{p['app']}|{env}"), manual, now) for env in p["envs"]]
    return {"app": p["app"], "name": name_of(p["app"]), "schedule": p["schedule"], "retention": f"últimos {p['keep']}",
            "volumes": p["volumes"], "hooks": p["hooks"], "destination": p["destination"], "item": json.dumps(p, ensure_ascii=False),
            "staging": False, "runs": runs}  # fmt: skip


def history_row(b: Row, name_of: NameOf, now: int) -> Row:
    sfx = " (staging)" if (b.get("env") or "producao") == "staging" else ""
    detail = b.get("detail") or (f"sha256 {b['sha256']} · {b['files']} arquivos" if b.get("sha256") else "")
    return {"id": b["id"], "app": name_of(b["app"]) + sfx, "kind": b["kind"], "size": m.human(b["size"]) if b["size"] else "—",
            "status": b["status"], "status_class": tone(b["status"]), "when": relative(b["at"], now), "detail": detail,
            "usable": b["status"] == "Concluído" and bool(b.get("file"))}  # fmt: skip


def restore_test_metric(backups: list[Row], name_of: NameOf, now: int) -> tuple[str, str, str]:
    """(value, change, css class) of the "last restore test" card."""
    last = next((b for b in backups if b["kind"] == "Teste de restore"), None)
    if not last:
        return "Nunca", "Faça um teste de restauração a partir do histórico", "metric-change"
    ok = last["status"] == "Concluído"
    change = f"{name_of(last['app'])} · {'OK' if ok else 'falhou'} em {br(last['duration_ms'] / 1000, 1)} s"
    return relative(last["at"], now), change, "metric-change" if ok else "metric-change down"


def maintenance_rows(maintenance: Row, name_of: NameOf, busy: set[str]) -> list[Row]:
    rows = []
    for k, mt in maintenance.items():
        a, env = k.split("|", 1)
        failed = mt["kind"] == "restauracao_falhou"
        lbl = "Em manutenção (restauração falhou)" if failed else "Em manutenção (restauração)"
        rows.append({"app": a, "env": env, "name": name_of(a) + (" (staging)" if env == "staging" else ""),
                     "detail": f"{lbl}: {mt.get('detail', '')}", "failed": failed or a not in busy})  # fmt: skip
    return rows
