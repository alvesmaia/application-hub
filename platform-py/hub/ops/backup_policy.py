"""Backup policy sent by the Backups page (`PUT /api/ops/backups/policies/{app}`): validation into the stored record."""

from __future__ import annotations

import zoneinfo
from typing import Any

from hub.ops.model import OpsError
from hub.ops.schedule import normalize

_VOLUME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


def _schedule(p: dict[str, Any]) -> str:
    schedule = str(p.get("schedule", "")).strip()
    if not schedule or len(schedule) > 60:
        raise OpsError("Informe a agenda (até 60 caracteres).")
    normalize(schedule)
    return schedule


def _keep(p: dict[str, Any]) -> int:
    keep = p.get("keep")
    if not isinstance(keep, int) or not 1 <= keep <= 90:
        raise OpsError("Retenção deve manter entre 1 e 90 backups.")
    return keep


def _volumes(p: dict[str, Any]) -> list[str]:
    vols = p.get("volumes")
    ok = isinstance(vols, list) and 1 <= len(vols) <= 8
    if not ok or not all(
        isinstance(v, str) and 0 < len(v) <= 32 and set(v) <= _VOLUME_CHARS for v in vols or []
    ):
        raise OpsError("Volumes inválidos: use de 1 a 8 nomes simples (ex.: data, uploads).")
    return list(vols or [])


def _destination(p: dict[str, Any]) -> str:
    dest = str(p.get("destination", "")).strip()
    if not dest or len(dest) > 80:
        raise OpsError("Informe o destino (até 80 caracteres).")
    return dest


def _envs(p: dict[str, Any]) -> list[str]:
    envs = p.get("envs") or ["producao"]
    if (
        not isinstance(envs, list)
        or not 1 <= len(envs) <= 2
        or not all(x in ("producao", "staging") for x in envs)
    ):
        raise OpsError("Ambientes: producao e/ou staging.")
    return [str(x) for x in envs]


def _missed(p: dict[str, Any]) -> str:
    missed = p.get("missed") or "executar"
    if missed not in ("executar", "pular"):
        raise OpsError("Execução perdida: executar ou pular.")
    return str(missed)


def _tz(p: dict[str, Any]) -> str:
    tz = str(p.get("tz") or "").strip()
    if tz:
        try:
            zoneinfo.ZoneInfo(tz)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as ex:
            raise OpsError(f"Fuso horário desconhecido: {tz}.") from ex
    return tz


def parse_policy(app_id: str, p: dict[str, Any]) -> dict[str, Any]:
    """The stored policy, or `OpsError` naming the first invalid field (same order as the form)."""
    schedule, keep, vols, dest = _schedule(p), _keep(p), _volumes(p), _destination(p)
    envs, missed, tz = _envs(p), _missed(p), _tz(p)
    return {"app": app_id, "schedule": schedule, "keep": keep, "volumes": vols, "hooks": bool(p.get("hooks")),
            "destination": dest, "envs": envs, "missed": missed, "jitter_s": min(int(p.get("jitter_s") or 20), 3600),
            "tz": tz}  # fmt: skip
