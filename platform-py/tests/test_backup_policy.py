"""Backup policy validation (`parse_policy`): the first invalid field names the error, in form order."""

from __future__ import annotations

import pytest

from hub.ops.backup_policy import parse_policy
from hub.ops.model import OpsError

GOOD = {"schedule": "Diário 02:00", "keep": 7, "volumes": ["data"], "destination": "local"}


def test_valid_policy_gets_defaults() -> None:
    pol = parse_policy("ativos", GOOD)
    assert pol == {"app": "ativos", "schedule": "Diário 02:00", "keep": 7, "volumes": ["data"], "hooks": False,
                   "destination": "local", "envs": ["producao"], "missed": "executar", "jitter_s": 20, "tz": ""}  # fmt: skip


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        ({"schedule": ""}, "agenda"),
        ({"keep": 0}, "Retenção"),
        ({"volumes": ["Data!"]}, "Volumes"),
        ({"volumes": []}, "Volumes"),
        ({"destination": ""}, "destino"),
        ({"envs": ["dev"]}, "Ambientes"),
        ({"missed": "talvez"}, "Execução perdida"),
        ({"tz": "Lua/Base"}, "Fuso horário desconhecido: Lua/Base"),
        ({"keep": 0, "destination": ""}, "Retenção"),  # first invalid field wins
    ],
)
def test_invalid_field_is_named(patch: dict[str, object], needle: str) -> None:
    with pytest.raises(OpsError, match=needle):
        parse_policy("ativos", GOOD | patch)
