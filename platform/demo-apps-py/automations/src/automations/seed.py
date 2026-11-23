"""First start: the sample packages (``samples/``) are imported, with one process each and one
example trigger (disabled). Idempotent: runs only while there is no package at all."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hub_sdk.app import HubSdk

from automations import packages, processes, triggers
from automations.package_format import build_zip
from automations.schedule_rules import ScheduleIn

SAMPLES = Path(__file__).resolve().parent / "samples"
SYSTEM = "sistema"
SEED_LOCK = 7_420_311
# package → (process name, example trigger params)
SAMPLE_PROCESSES = {
    "consultar-cep": ("Consultar CEP", {"cep": "01001-000"}),
    "feriados-nacionais": ("Feriados nacionais", {"ano": 2026}),
    "validar-documentos": (
        "Validar CPF/CNPJ",
        {"documentos": "529.982.247-25\n11.222.333/0001-81", "tipo": "auto"},
    ),
}
EXAMPLE_RULE = ScheduleIn(kind="diario", at_time="08:00")


async def seed(sdk: HubSdk) -> None:
    if sdk.pool is None:
        return
    async with sdk.pool.acquire() as c, c.transaction():
        await c.execute("SELECT pg_advisory_xact_lock($1)", SEED_LOCK)
        if await c.fetchval("SELECT count(*) FROM aut_packages"):
            return
        for folder in sorted(p for p in SAMPLES.iterdir() if p.is_dir() and p.name in SAMPLE_PROCESSES):
            await _import(c, folder)


async def _import(conn: object, folder: Path) -> None:
    pkg = await packages.register(conn, build_zip(folder), SYSTEM)
    name, example = SAMPLE_PROCESSES[folder.name]
    spec = processes.ProcessIn(name=name, package_name=pkg.name, package_version=pkg.version)
    pid = await processes.save_process(conn, None, spec, SYSTEM)
    await triggers.save_trigger(conn, None, pid, example, EXAMPLE_RULE, SYSTEM, datetime.now(UTC))
