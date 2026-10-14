"""App registry read from apps.json / apps.compose.json (HUB_APPS_FILE)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from hub.models import AppManifest, AppRecord


@dataclass(frozen=True)
class AppEntry:
    id: str
    name: str
    upstream: str
    version: str = ""
    max_upload_mb: int = 16
    forward_auth_schemes: tuple[str, ...] = ()
    display: dict[str, str] = field(default_factory=dict)
    manifest: AppManifest | None = None

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


class Registry:
    def __init__(self, entries: list[AppEntry]) -> None:
        self._apps = {e.id: e for e in entries}
        self.staging: dict[str, str] = {}  # staging upstream per app (container `<app>__staging`)

    def set_upstream(self, app_id: str, url: str, version: str | None = None) -> None:
        """Switches the app's target (blue/green): the next requests already go to the new upstream."""
        if (e := self._apps.get(app_id)) is not None:
            self._apps[app_id] = replace(e, upstream=url, version=e.version if version is None else version)

    def add(self, entry: AppEntry) -> None:
        """New app deployed by the hub (developer kit) — `hub.ops.newapp`."""
        self._apps[entry.id] = entry

    def get(self, app_id: str) -> AppEntry | None:
        return self._apps.get(app_id)

    def entries(self) -> list[AppEntry]:
        return sorted(self._apps.values(), key=lambda e: e.id)

    def ids(self) -> list[str]:
        return list(self._apps)


def load_registry(path: Path | str | None = None) -> Registry:
    if path is None:
        env = os.environ.get("HUB_APPS_FILE")
        root = Path(os.environ.get("HUB_PLATFORM_DIR", Path(__file__).resolve().parents[3] / "platform"))
        path = env or root / "config/apps.json"
    p = Path(path)
    if not p.is_file():
        return Registry([])
    raw = [AppRecord.model_validate(item) for item in json.loads(p.read_text(encoding="utf-8"))]
    out = []
    for r in raw:
        m = r.manifest
        out.append(
            AppEntry(
                id=m.id,
                name=m.name or m.id,
                upstream=r.upstream,
                version=str(m.version or ""),
                max_upload_mb=int(m.limits.get("max_upload_mb", 16)),
                forward_auth_schemes=tuple(m.forward_auth_schemes),
                display=r.display,
                manifest=m,
            )
        )
    return Registry(out)
