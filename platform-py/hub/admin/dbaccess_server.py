"""Applies the database network rules inside the Postgres container: certificate, `pg_hba.conf`, TLS on, reload.

Runs at start-up and every few minutes (an app network attached later gets its rule), and when Administration opens
the page. Off and never turned on: the container is not touched. Errors are reported, never raised into the hub."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from pathlib import Path
from typing import Any

from hub.admin import dbaccess_net as net
from hub.ops.backup_db import tmp_sql
from hub.ops.runtime import exec_in

log = logging.getLogger("hub.dbaccess")
HEADER = "# Managed by Application Hub"
PERIOD_S = 300
_SSL = ("ALTER SYSTEM SET ssl = 'on'", "ALTER SYSTEM SET ssl_cert_file = 'server.crt'",
        "ALTER SYSTEM SET ssl_key_file = 'server.key'", "ALTER SYSTEM SET ssl_min_protocol_version = 'TLSv1.2'")  # fmt: skip


async def _sh(b: Any, script: str, files: dict[str, bytes] | None = None) -> str:
    rc, out = await exec_in(b.engine, b.db_container, ["sh", "-c", script], files=files, user="root")
    if rc != 0:
        raise RuntimeError(f"{script.split()[0]}: {out.decode(errors='replace').strip()[-200:]}")
    return out.decode(errors="replace")


async def networks(b: Any) -> list[net.Net]:
    """(subnet, gateway) of every Docker network the database container is on."""
    attrs = await b.engine._run(lambda cl: cl.containers.get(b.db_container).attrs)
    out: list[net.Net] = []
    for n in (attrs.get("NetworkSettings", {}).get("Networks") or {}).values():
        if n.get("IPAddress") and n.get("IPPrefixLen"):
            subnet = str(ipaddress.ip_interface(f"{n['IPAddress']}/{n['IPPrefixLen']}").network)
            out.append((subnet, str(n.get("Gateway") or "")))
    return out


async def _certificate(b: Any, cfg: net.DbAccessCfg) -> bytes:
    """The certificate in use; the installation's own files win, else a self-signed one made once."""
    if cfg.cert_file and cfg.key_file:
        cert, key = await asyncio.to_thread(
            lambda: (Path(cfg.cert_file).read_bytes(), Path(cfg.key_file).read_bytes())
        )
    elif "BEGIN CERTIFICATE" in (have := await _sh(b, 'cat "$PGDATA/server.crt" 2>/dev/null || true')):
        return have.encode()
    else:
        cert, key = net.self_signed(cfg.host)
    k, c = tmp_sql("dbkey"), tmp_sql("dbcrt")
    await _sh(b, f'install -m 600 -o postgres -g postgres {k} "$PGDATA/server.key" && '
                 f'install -m 644 -o postgres -g postgres {c} "$PGDATA/server.crt"', {k: key, c: cert})  # fmt: skip
    return cert


async def sync(b: Any, cfg: net.DbAccessCfg) -> dict[str, Any]:
    """Writes the rules and reloads Postgres; returns what Administration shows (on, fingerprint, problem)."""
    current = await _sh(b, 'cat "$PGDATA/pg_hba.conf"')
    if not cfg.public and HEADER not in current:
        return {"public": False, "fingerprint": "", "error": ""}
    fp = net.fingerprint(await _certificate(b, cfg)) if cfg.public else ""
    text = net.hba(cfg, await networks(b))
    if text != current:
        h = tmp_sql("hba")
        await _sh(b, f'install -m 600 -o postgres -g postgres {h} "$PGDATA/pg_hba.conf"', {h: text.encode()})
    await b._psql("postgres", *(_SSL if cfg.public else ()), "SELECT pg_reload_conf()")
    bad = (
        await b._psql("postgres", "SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL")
    ).strip()
    return {
        "public": cfg.public,
        "fingerprint": fp,
        "error": "" if bad == "0" else "regras do pg_hba com erro",
    }


async def keep_synced(b: Any, cfg: net.DbAccessCfg, state: dict[str, Any]) -> None:
    while True:
        try:
            state.update(await sync(b, cfg))
        except Exception as e:  # the database may be starting; the hub keeps running
            state.update(error=str(e)[:200])
            log.warning("acesso ao banco: %s", e)
        await asyncio.sleep(PERIOD_S)
