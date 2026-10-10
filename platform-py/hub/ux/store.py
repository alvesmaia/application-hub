"""Preferências por usuário (tema, passos, layout do Início, favoritos, recentes) e layouts por função."""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from pydantic import BaseModel, Field

WIDGETS = ("onboarding", "favorites", "inbox", "recents", "status", "shortcuts")
# (chave, permissão exigida, link): "Primeiros passos".
STEPS: tuple[tuple[str, str | None, str], ...] = (
    ("open_app", None, "/applications"),
    ("theme", None, "#theme"),
    ("notifications", None, "/inbox#prefs"),
    ("search", None, "#search"),
    ("variable", "gerenciar_variaveis", "/admin/env"),
    ("package", "editar_configuracoes", "/admin/sdk"),
    ("backup", "editar_configuracoes", "/admin/backups"),
)
KINDS = ("alert", "notification", "approval")
RECENTS_CAP, SEARCHES_CAP, FAVORITES_CAP = 8, 8, 24


class Recent(BaseModel):
    path: str
    label: str
    at: int


class Channel(BaseModel):
    in_app: bool = True
    email: bool = False
    teams: bool = False


class Prefs(BaseModel):
    theme: str = ""
    lang: str = ""
    tour: str = ""
    steps: list[str] = Field(default_factory=list)
    layout: list[str] | None = None
    favorites: list[str] = Field(default_factory=list)
    recents: list[Recent] = Field(default_factory=list)
    searches: list[str] = Field(default_factory=list)
    channels: dict[str, Channel] = Field(default_factory=dict)
    analytics_opt_out: bool = False

    def channel(self, kind: str) -> Channel:
        return self.channels.get(kind) or Channel()


class UxData(BaseModel):
    users: dict[str, Prefs] = Field(default_factory=dict)
    role_layouts: dict[str, list[str]] = Field(default_factory=dict)


class UxStore:
    """Arquivo JSON único (`ux.json`); a escrita é atômica e acontece só quando algo muda."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = UxData()
        with suppress(OSError, ValueError):
            self.data = UxData.model_validate_json(path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(self.data.model_dump_json(), encoding="utf-8")
        os.replace(tmp, self.path)

    def prefs(self, user: str) -> Prefs:
        return self.data.users.get(user, Prefs()).model_copy(deep=True)

    def update(self, user: str, **fields: object) -> Prefs:
        cur = self.data.users.setdefault(user, Prefs())
        for k, v in fields.items():
            setattr(cur, k, v)
        self._save()
        return cur.model_copy(deep=True)

    def step(self, user: str, key: str) -> None:
        if user and key not in self.prefs(user).steps:
            self.update(user, steps=[*self.prefs(user).steps, key])

    def visit(self, user: str, path: str, label: str, now: int) -> None:
        if not user:
            return
        cur = self.prefs(user).recents
        if cur and cur[0].path == path:
            return
        rest = [r for r in cur if r.path != path]
        self.update(user, recents=[Recent(path=path, label=label[:80], at=now), *rest][:RECENTS_CAP])

    def remember_search(self, user: str, text: str) -> None:
        rest = [s for s in self.prefs(user).searches if s != text]
        self.update(user, searches=[text, *rest][:SEARCHES_CAP])

    def toggle_favorite(self, user: str, app: str, on: bool) -> list[str]:
        favs = [a for a in self.prefs(user).favorites if a != app]
        if on:
            favs = [*favs, app][:FAVORITES_CAP]
        return self.update(user, favorites=favs).favorites

    def set_role_layout(self, role: str, widgets: list[str] | None) -> None:
        if widgets is None:
            self.data.role_layouts.pop(role, None)
        else:
            self.data.role_layouts[role] = widgets
        self._save()

    def layout_for(self, user: str, roles: list[str]) -> tuple[list[str], str]:
        """Layout do usuário, senão o padrão da 1ª função que tem um, senão o global."""
        own = self.data.users.get(user)
        if own and own.layout is not None:
            return list(own.layout), "user"
        for r in roles:
            if r in self.data.role_layouts:
                return list(self.data.role_layouts[r]), "role"
        return list(WIDGETS), "default"


def valid_layout(widgets: list[str]) -> list[str]:
    out: list[str] = []
    for w in widgets[:20]:
        if w not in WIDGETS:
            raise ValueError(f"Widget desconhecido: {w}")
        if w not in out:
            out.append(w)
    return out
