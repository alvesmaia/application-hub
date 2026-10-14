"""Routes for Home, Applications and Status, and the `/api/me/*` APIs (prefs, onboarding, home, favorites)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from hub.gateway.registry import Registry
from hub.insight.analytics import FEATURES
from hub.ux import catalog, status
from hub.ux.inbox_routes import entries_of
from hub.ux.store import KINDS, STEPS, WIDGETS, Channel, UxStore, valid_layout
from hub.ux.texts import ago, t, tn, tr

_WEB = Path(__file__).parent.parent / "web"
_ICONS = cast(dict[str, str], json.loads((_WEB / "icons.json").read_text(encoding="utf-8")))
_THEMES = ("", "light", "dark", "auto")
_LANGS = ("", "pt-BR", "en")


class PrefsIn(BaseModel):
    theme: str | None = None
    lang: str | None = None
    analytics_opt_out: bool | None = None
    channels: dict[str, Channel] | None = None


class StepIn(BaseModel):
    key: str


class TourIn(BaseModel):
    tour: str


class LayoutIn(BaseModel):
    widgets: list[str] | None = None


class FavIn(BaseModel):
    on: bool


class FeatureIn(BaseModel):
    feature: str


def icon(name: str) -> str:
    return f'<svg viewBox="0 0 24 24" aria-hidden="true">{_ICONS.get(name, _ICONS["_"])}</svg>'


def _err(status_code: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status_code)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def visible(request: Request) -> set[str] | None:
    """Apps the user can reach (`None` = all)."""
    apps = getattr(request.state, "apps", None)
    return None if apps is None else set(apps)


def mount_ux(
    app: FastAPI,
    reg: Registry,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    store: UxStore,
    data_dir: Path,
    public_status: Callable[[], set[str]],
) -> Callable[[Request, int], None]:
    """Registers the routes; returns the `track` (usage observation) that the middleware calls on every response."""
    helpers: dict[str, Any] = {"icon": icon}

    async def live_states(request: Request, vis: set[str] | None) -> list[tuple[str, str, str]]:
        ins = request.app.state.insight
        return status.app_states(reg, vis, ins.tel.metrics, await ins.ops.incidents())

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        user = request.state.user
        lang = request.state.lang
        perms = cast(list[str], request.state.permissions)
        prefs = store.prefs(user["id"])
        layout, source = store.layout_for(user["id"], user["roles"])
        vis = visible(request)
        mine = catalog.cards(reg, data_dir, prefs.favorites, vis, allowed=True, lang=lang)
        shown = [(w, False) for w in layout if w in WIDGETS] + [(w, True) for w in WIDGETS if w not in layout]
        now = now_ms()
        seen: set[str] = set()
        recents = []
        for r in prefs.recents:
            path = catalog.recent_path(r.path)
            if path is None or path in seen:
                continue
            seen.add(path)
            recents.append((path, catalog.path_label(reg, path) or r.label, ago(r.at, now, lang)))
        inbox_items = [e for e in entries_of(request) if not e["archived"] and e["in_app"]]
        steps = [(k, link, k in prefs.steps) for k, perm, link in STEPS if perm is None or perm in perms]
        done = sum(1 for s in steps if s[2])
        total = len(steps)
        progress = f"{done}/{total}"
        ctx = {**page_ctx(request), **helpers} | {
            "greeting": tn("home.greeting", (user["name"].split() or [""])[0], lang),
            "subtitle": tn("home.subtitle", len(mine), lang),
            "widgets": [{"id": w, "title": t(f"widget.{w}", lang), "hidden": h} for w, h in shown],
            "layout_json": json.dumps(layout, separators=(",", ":")),
            "layout_source": source,
            "widget_options": [(w, t(f"widget.{w}", lang)) for w in WIDGETS],
            "favs": [c for f in prefs.favorites for c in mine if c.id == f],
            "recents": recents,
            "inbox": inbox_items[:5],
            "unread": sum(1 for e in inbox_items[:5] if not e["read"]),
            "states": [(i, n, *catalog.state_label(k, lang)) for i, n, k in await live_states(request, vis)],
            "steps": [
                {"key": k, "label": t(f"step.{k}", lang), "link": link, "done": d} for k, link, d in steps
            ],
            "progress": progress,
            "progress_label": tn("widget.progress", progress, lang),
            "progress_pct": str(done * 100 // total if total else 100),
            "all_done": done == total,
            "analytics": "editar_configuracoes" in perms,
        }
        return templates.TemplateResponse(request, "home.html", ctx)

    @app.get("/applications", response_class=HTMLResponse)
    async def applications(request: Request) -> Response:
        lang = request.state.lang
        prefs = store.prefs(request.state.user["id"])
        vis = visible(request)
        states = {i: catalog.state_label(k, lang) for i, _, k in await live_states(request, vis)}
        mine = catalog.cards(reg, data_dir, prefs.favorites, vis, allowed=True, lang=lang)
        others = catalog.cards(reg, data_dir, prefs.favorites, vis, allowed=False, lang=lang)
        ctx = {**page_ctx(request), **helpers} | {
            "mine": [
                (c, catalog.card_texts(c, lang), states.get(c.id, catalog.state_label("ok", lang)))
                for c in mine
            ],
            "others": others,
        }
        return templates.TemplateResponse(request, "applications.html", ctx)

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(request: Request) -> Response:
        signed_in = getattr(request.state, "user", None) is not None
        vis = visible(request) if signed_in else public_status()
        insight = request.app.state.insight
        admin_data = await insight.ops.read()
        data = status.status_data(reg, vis, time.time(), insight.tel.metrics, admin_data)
        perms = getattr(request.state, "permissions", [])
        meta = json.dumps(
            {
                "apps": [
                    {"id": e.id, "short": e.display.get("short", e.name)}
                    for e in reg.entries()
                    if vis is None or e.id in vis
                ]
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        lang = request.state.lang
        pub = {
            "brand": request.app.title,
            "lang": lang,
            "t": lambda k: t(k, lang),
            "tn": lambda k, n: tn(k, n, lang),
            "tr": lambda s: tr(s, lang),
            **helpers,
        }
        base = {**page_ctx(request), **helpers} if signed_in else pub
        ctx = base | {
            "data": data,
            "incident_meta": meta,
            "can_edit": "editar_configuracoes" in perms,
            "ok": data.overall.startswith("Todos"),
        }
        return templates.TemplateResponse(request, "status.html" if signed_in else "status_public.html", ctx)

    # ---- user APIs ------------------------------------------------------------------------

    @app.get("/api/me/prefs")
    async def get_prefs(request: Request) -> dict[str, Any]:
        u = request.state.user
        p = store.prefs(u["id"])
        layout, source = store.layout_for(u["id"], u["roles"])
        return {
            "theme": p.theme,
            "lang": p.lang,
            "tour": p.tour,
            "steps": sorted(p.steps),
            "layout": layout,
            "layout_source": source,
            "favorites": p.favorites,
            "recents": [r.model_dump() for r in p.recents],
            "searches": p.searches,
            "channels": {k: p.channel(k).model_dump() for k in KINDS},
            "analytics_opt_out": p.analytics_opt_out,
        }

    def apply_prefs(
        uid: str,
        theme: str | None = None,
        lang: str | None = None,
        analytics_opt_out: bool | None = None,
        channels: dict[str, Channel] | None = None,
    ) -> str | None:
        """Saves the preferences (API and menu form); returns the error message, if invalid."""
        if theme is not None and theme not in _THEMES:
            return "Tema inválido."
        if lang is not None and lang not in _LANGS:
            return "Idioma inválido."
        fields: dict[str, Any] = {
            k: v
            for k, v in (("theme", theme), ("lang", lang), ("analytics_opt_out", analytics_opt_out))
            if v is not None
        }
        merged = {**store.prefs(uid).channels, **{k: v for k, v in (channels or {}).items() if k in KINDS}}
        store.update(uid, channels=merged, **fields)
        if theme is not None:
            store.step(uid, "theme")
        if merged:
            store.step(uid, "notifications")
        app.state.inbox.poke()  # the bell counter depends on the channels that are on
        return None

    app.state.apply_prefs = apply_prefs

    @app.put("/api/me/prefs")
    async def put_prefs(request: Request) -> JSONResponse:
        try:
            b = PrefsIn.model_validate(await request.json())
        except (ValidationError, ValueError):
            return _err(400, "Dados inválidos.")
        msg = apply_prefs(request.state.user["id"], b.theme, b.lang, b.analytics_opt_out, b.channels)
        return _err(400, msg) if msg else JSONResponse({"ok": True})

    @app.put("/api/me/onboarding")
    async def put_onboarding(request: Request) -> JSONResponse:
        try:
            tour = TourIn.model_validate(await request.json()).tour.strip()
        except (ValidationError, ValueError):
            return _err(400, "Estado do tour inválido.")
        n = tour.removeprefix("step:")
        if not (
            tour in ("", "done", "skipped") or (tour.startswith("step:") and n.isdigit() and int(n) < 256)
        ):
            return _err(400, "Estado do tour inválido.")
        store.update(request.state.user["id"], tour=tour)
        return JSONResponse({"ok": True})

    @app.post("/api/me/onboarding/step")
    async def post_step(request: Request) -> JSONResponse:
        try:
            key = StepIn.model_validate(await request.json()).key
        except (ValidationError, ValueError):
            return _err(400, "Passo desconhecido.")
        if key != "search":  # only steps that happen in the browser; the server marks the others
            return _err(400, "Passo desconhecido.")
        store.step(request.state.user["id"], key)
        return JSONResponse({"ok": True})

    @app.put("/api/me/home")
    async def put_home(request: Request) -> JSONResponse:
        try:
            widgets = LayoutIn.model_validate(await request.json()).widgets
        except (ValidationError, ValueError):
            widgets = None
        if widgets is None:
            return _err(400, "Informe widgets.")
        try:
            clean = valid_layout(widgets)
        except ValueError as e:
            return _err(400, str(e))
        store.update(request.state.user["id"], layout=clean)
        return JSONResponse({"ok": True, "widgets": clean})

    @app.delete("/api/me/home")
    async def delete_home(request: Request) -> JSONResponse:
        u = request.state.user
        store.update(u["id"], layout=None)
        widgets, source = store.layout_for(u["id"], u["roles"])
        return JSONResponse({"ok": True, "widgets": widgets, "source": source})

    @app.put("/api/me/favorites/{app_id}")
    async def put_favorite(request: Request, app_id: str) -> JSONResponse:
        try:
            on = FavIn.model_validate(await request.json()).on
        except (ValidationError, ValueError):
            return _err(400, "Dados inválidos.")
        vis = visible(request)
        if on and (reg.get(app_id) is None or (vis is not None and app_id not in vis)):
            return _err(403, "Você não tem acesso a esta aplicação.")
        favorites = store.toggle_favorite(request.state.user["id"], app_id, on)
        return JSONResponse({"ok": True, "favorites": favorites})

    @app.post("/api/me/analytics")
    async def feature(request: Request) -> Response:
        try:
            name = FeatureIn.model_validate(await request.json()).feature
        except (ValidationError, ValueError):
            return _err(400, "Recurso desconhecido.")
        if name not in FEATURES:
            return _err(400, "Recurso desconhecido.")
        request.app.state.insight.analytics.record(request.state.user["id"], "feature", name)
        return Response(status_code=204)

    def track(request: Request, status_code: int) -> None:
        """Usage observation after each response: Home's Recents and the "open an application" step."""
        user = getattr(request.state, "user", None)
        if not user or request.method != "GET" or not 200 <= status_code < 400:
            return
        segs = request.url.path.strip("/").split("/")
        vis = visible(request)
        entry = reg.get(segs[1]) if segs[0] in ("_frag", "apps") and len(segs) > 1 else None
        if entry is None or (vis is not None and entry.id not in vis):
            return
        if segs[0] == "_frag":
            sec = segs[2] if len(segs) > 2 else ""
            path = catalog.recent_path(f"/apps/{entry.id}/{sec}")
            if path and request.headers.get("x-hub-nav") and sec and not sec.startswith(("_", "e2e")):
                store.visit(user["id"], path, f"{entry.name} · {catalog.section_label(sec)}", now_ms())
        elif len(segs) <= 3:
            store.step(user["id"], "open_app")
            if path := catalog.recent_path(request.url.path):
                store.visit(user["id"], path, catalog.path_label(reg, path) or entry.name, now_ms())

    return track
