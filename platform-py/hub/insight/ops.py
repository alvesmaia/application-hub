"""Flags, SLOs, incidents and monitored services in `admin.json` (`hub-admin::store`), all audited."""

from __future__ import annotations

import time
from typing import Any

from hub.admin.config import AdminError, env_label
from hub.admin.store import AdminStore
from hub.insight.flags import MAX_FLAGS_PER_APP, Flag, flag_detail, parse_flag

KINDS = ("Aplicação", "Serviço", "Identity")
STATUSES = ("Disponível", "Manutenção", "Indisponível")
MAX_INCIDENTS = 200


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _bad(msg: str) -> AdminError:
    return AdminError(msg, 400)


def _missing(msg: str) -> AdminError:
    return AdminError(msg, 404)


def seed_health(data: dict[str, Any], apps: list[str], now: int) -> None:
    """Initial services of the mockup (only the apps that exist in the registry)."""
    if data.get("health") or data.get("health_seeded"):
        return
    rows = [
        ("Autenticação", "Identity", None, 2),
        ("Gestão de Ativos", "Aplicação", "ativos", 2),
        ("Rede de Fornecedores", "Aplicação", "fornecedores", 2),
        ("Central de Serviços", "Aplicação", "servicos", 2),
        ("Notificações", "Serviço", None, 5),
    ]
    data["health"] = [
        {
            "name": n,
            "kind": k,
            "status": "Disponível",
            "app": a,
            "checked_at": now - m * 60_000,
            "latency_ms": None,
        }
        for n, k, a, m in rows
        if a is None or a in apps
    ]
    data["health_seeded"] = True


class InsightOps:
    def __init__(self, store: AdminStore) -> None:
        self.s = store

    # ---- leitura -----------------------------------------------------------------------------------
    async def read(self) -> dict[str, Any]:
        data = await self.s.cfg.read()
        if not data.get("health") and not data.get("health_seeded"):
            async with self.s.locked(True) as d:
                seed_health(d, self.s.apps, now_ms())
            data = await self.s.cfg.read()
        return data

    async def flags(self) -> tuple[list[dict[str, Any]], int]:
        d = await self.s.cfg.read()
        return d.get("flags", []), d.get("flags_version", 0)

    async def flag_objects(self) -> list[Flag]:
        return [Flag.model_validate(f) for f in (await self.flags())[0]]

    # ---- flags ---------------------------------------------------------------------------------------
    async def upsert_flag(self, actor: str, raw: dict[str, Any], create: bool) -> dict[str, Any]:  # noqa: C901, PLR0915
        try:
            flag = parse_flag(raw)
            if flag.app not in self.s.apps:
                raise _bad(f"Aplicação desconhecida: {flag.app}.")
            flag.validate_rules()
        except ValueError as e:
            raise e if isinstance(e, AdminError) else _bad(str(e)) from e

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            flags = d.setdefault("flags", [])
            pos = next(
                (i for i, x in enumerate(flags) if x["key"] == flag.key and x["app"] == flag.app), None
            )
            if pos is not None and create:
                raise _bad("Já existe uma flag com essa chave neste app.")
            if pos is None and not create:
                raise _missing("Flag não encontrada.")
            if pos is None:
                if sum(1 for x in flags if x["app"] == flag.app) >= MAX_FLAGS_PER_APP:
                    raise _bad(f"Limite de {MAX_FLAGS_PER_APP} flags por aplicação.")
                if len(flags) >= 500:
                    raise _bad("Limite de 500 flags.")
                flags.append(flag.dump())
                title = "Feature flag criada"
            else:
                flags[pos] = flag.dump()
                title = "Feature flag alterada"
            d["flags_version"] = d.get("flags_version", 0) + 1
            return flag.dump(), title, flag_detail(flag), "settings", flag.app

        return await self.s.cfg.mutate(actor, run)

    async def set_flag_enabled(self, actor: str, app: str, key: str, env: str, on: bool) -> dict[str, Any]:
        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            raw = next((x for x in d.get("flags", []) if x["key"] == key and x["app"] == app), None)
            if raw is None:
                raise _missing("Flag não encontrada.")
            f = Flag.model_validate(raw)
            rule = f.envs.get(env)
            if rule is None:
                from hub.insight.flags import EnvRule

                rule = f.envs[env] = EnvRule(enabled=False, default=f.off_value())
            rule.enabled = on
            try:
                f.validate_rules()
            except ValueError as e:
                raise _bad(str(e)) from e
            raw.clear()
            raw.update(f.dump())
            d["flags_version"] = d.get("flags_version", 0) + 1
            title = "Feature flag ligada" if on else "Feature flag desligada (kill switch)"
            return f.dump(), title, f"{key} · {env_label(env)}", "settings", app

        return await self.s.cfg.mutate(actor, run)

    async def delete_flag(self, actor: str, app: str, key: str) -> None:
        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            flags = d.get("flags", [])
            i = next((i for i, x in enumerate(flags) if x["key"] == key and x["app"] == app), None)
            if i is None:
                raise _missing("Flag não encontrada.")
            del flags[i]
            d["flags_version"] = d.get("flags_version", 0) + 1
            return None, "Feature flag excluída", key, "trash", app

        await self.s.cfg.mutate(actor, run)

    # ---- SLOs ----------------------------------------------------------------------------------------
    async def set_slo(self, actor: str, raw: dict[str, Any]) -> dict[str, Any]:
        try:
            slo: dict[str, Any] = {
                "app": str(raw["app"]),
                "env": str(raw["env"]),
                "availability": float(raw["availability"]),
                "latency_ms": int(raw["latency_ms"]),
                "latency_target": float(raw.get("latency_target", 95.0)),
                "window_days": int(raw["window_days"]),
            }
        except (KeyError, TypeError, ValueError) as e:
            raise _bad("Dados inválidos: verifique os campos enviados.") from e
        if slo["app"] not in self.s.apps:
            raise _bad(f"Aplicação desconhecida: {slo['app']}.")
        if slo["env"] not in ("producao", "staging"):
            raise _bad("Ambiente inválido (staging ou producao).")
        if not (50.0 <= slo["availability"] <= 99.999 and 50.0 <= slo["latency_target"] <= 99.999):
            raise _bad("Metas entre 50% e 99,999%.")
        if not 0 < slo["latency_ms"] <= 60_000:
            raise _bad("Latência alvo entre 1 ms e 60 s.")
        if slo["window_days"] not in (7, 30):
            raise _bad("Janela de 7 ou 30 dias.")

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            d["slos"] = [
                x for x in d.get("slos", []) if not (x["app"] == slo["app"] and x["env"] == slo["env"])
            ]
            d["slos"].append(slo)
            detail = (
                f"{env_label(slo['env'])} · disponibilidade {slo['availability']:g}% · "
                f"{slo['latency_target']:g}% em até {slo['latency_ms']} ms · {slo['window_days']} dias"
            )
            return slo, "SLO definido", detail, "check", slo["app"]

        return await self.s.cfg.mutate(actor, run)

    async def delete_slo(self, actor: str, app: str, env: str) -> None:
        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            slos = d.get("slos", [])
            i = next((i for i, x in enumerate(slos) if x["app"] == app and x["env"] == env), None)
            if i is None:
                raise _missing("SLO não encontrado.")
            del slos[i]
            return None, "SLO removido", env_label(env), "trash", app

        await self.s.cfg.mutate(actor, run)

    # ---- incidentes ----------------------------------------------------------------------------------
    @staticmethod
    def _push_incident(
        d: dict[str, Any], app: str, env: str, title: str, kind: str, severity: str, author: str, note: str
    ) -> dict[str, Any]:
        d["next_incident_id"] = max(d.get("next_incident_id", 0), 1)
        inc = {
            "id": d["next_incident_id"],
            "app": app,
            "env": env,
            "title": title,
            "kind": kind,
            "severity": severity,
            "started": now_ms(),
            "resolved": None,
            "notes": [{"at": now_ms(), "author": author, "text": note.strip()}] if note.strip() else [],
        }
        d["next_incident_id"] += 1
        d["incidents"] = [inc, *d.get("incidents", [])][:MAX_INCIDENTS]
        return inc

    async def incidents(self) -> list[dict[str, Any]]:
        return (await self.s.cfg.read()).get("incidents", [])

    async def create_incident(self, actor: str, raw: dict[str, Any]) -> dict[str, Any]:
        app, title = str(raw.get("app", "")), str(raw.get("title", "")).strip()
        note, severity = str(raw.get("note", "")), str(raw.get("severity", ""))
        if app != "platform" and app not in self.s.apps:
            raise _bad("Aplicação desconhecida.")
        if not title or len(title) > 120 or len(note) > 2000:
            raise _bad("Informe o título (até 120) e a nota (até 2000 caracteres).")
        if severity not in ("major", "minor", "info"):
            raise _bad("Severidade inválida.")
        env = "staging" if raw.get("env") == "staging" else "producao"

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            return (
                self._push_incident(d, app, env, title, "manual", severity, actor, note),
                "Incidente registrado",
                title,
                "file",
                app,
            )

        return await self.s.cfg.mutate(actor, run)

    async def update_incident(self, actor: str, iid: int, note: str, resolve: bool) -> dict[str, Any]:
        if len(note) > 2000:
            raise _bad("Nota até 2000 caracteres.")

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            inc = next((x for x in d.get("incidents", []) if x["id"] == iid), None)
            if inc is None:
                raise _missing("Incidente não encontrado.")
            if note.strip():
                inc["notes"].append({"at": now_ms(), "author": actor, "text": note.strip()})
            if resolve and inc["resolved"] is None:
                inc["resolved"] = now_ms()
            title = "Incidente resolvido" if resolve else "Nota de incidente adicionada"
            return inc, title, inc["title"], "file", inc["app"]

        return await self.s.cfg.mutate(actor, run)

    async def open_auto_incident(self, app: str, env: str, title: str, severity: str, detail: str) -> None:
        async with self.s.locked(True) as d:
            if not any(
                i["app"] == app
                and i["env"] == env
                and i["kind"] == "auto"
                and i["resolved"] is None
                and i["title"] == title
                for i in d.get("incidents", [])
            ):
                self._push_incident(d, app, env, title, "auto", severity, "plataforma", detail)

    async def resolve_auto_incident(self, app: str, env: str, title: str, note: str) -> None:
        async with self.s.locked(True) as d:
            for i in d.get("incidents", []):
                if (
                    i["app"] == app
                    and i["env"] == env
                    and i["kind"] == "auto"
                    and i["resolved"] is None
                    and i["title"] == title
                ):
                    i["resolved"] = now_ms()
                    i["notes"].append({"at": now_ms(), "author": "plataforma", "text": note})

    # ---- monitored services ----------------------------------------------------------------------------
    def _check_service(self, raw: dict[str, Any], name: str) -> dict[str, Any]:
        kind, status, app = str(raw.get("kind", "")), str(raw.get("status", "")), raw.get("app") or None
        if not name or len(name) > 60:
            raise _bad("Informe o nome do serviço (até 60 caracteres).")
        if kind not in KINDS:
            raise _bad("Tipo de serviço inválido.")
        if status not in STATUSES:
            raise _bad("Status inválido.")
        if app and app not in self.s.apps:
            raise _bad(f"Aplicação desconhecida: {app}.")
        return {"kind": kind, "status": status, "app": app}

    async def create_service(self, actor: str, raw: dict[str, Any]) -> dict[str, Any]:
        name = str(raw.get("name", "")).strip()
        fields = self._check_service(raw, name)

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            seed_health(d, self.s.apps, now_ms())
            if any(s["name"].lower() == name.lower() for s in d["health"]):
                raise _bad(f"Já existe um serviço chamado {name}.")
            svc = {"name": name, **fields, "checked_at": now_ms(), "latency_ms": None}
            d["health"].append(svc)
            return svc, "Serviço adicionado ao monitoramento", name, "check", "platform"

        return await self.s.cfg.mutate(actor, run)

    async def update_service(self, actor: str, name: str, raw: dict[str, Any]) -> dict[str, Any]:
        fields = self._check_service(raw, name)

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            svc = next((s for s in d.get("health", []) if s["name"] == name), None)
            if svc is None:
                raise _missing("Serviço não encontrado.")
            svc.update(fields, checked_at=now_ms())
            return svc, "Serviço atualizado", name, "check", "platform"

        return await self.s.cfg.mutate(actor, run)

    async def delete_service(self, actor: str, name: str) -> None:
        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            health = d.get("health", [])
            i = next((i for i, s in enumerate(health) if s["name"] == name), None)
            if i is None:
                raise _missing("Serviço não encontrado.")
            del health[i]
            return None, "Serviço removido do monitoramento", name, "trash", "platform"

        await self.s.cfg.mutate(actor, run)

    async def record_probes(self, actor: str, results: list[tuple[str, bool, int]]) -> list[dict[str, Any]]:
        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            now = now_ms()
            for s in d.get("health", []):
                hit = next((r for r in results if r[0] == s.get("app")), None)
                if hit:
                    s["status"] = "Disponível" if hit[1] else "Indisponível"
                    s["latency_ms"], s["checked_at"] = hit[2], now
            down = sum(1 for r in results if not r[1])
            detail = (
                f"{len(results)} aplicações disponíveis"
                if not down
                else f"{down} de {len(results)} aplicações indisponíveis"
            )
            return d.get("health", []), "Verificação de integridade concluída", detail, "check", "platform"

        return await self.s.cfg.mutate(actor, run)
