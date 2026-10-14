"""Probes, front-end assets (theme layer, i18n catalog, files, service worker), PWA manifest and offline page."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from hub import __version__, devmode
from hub.ux.texts import js_catalog, resolve, t
from hub.webctx import STAGING_BAR_CSS, THEME, WebCtx, find_asset, web

router = APIRouter()

_ICONS = [
    {"purpose": "any", "sizes": "192x192", "src": "/assets/icon-192.png", "type": "image/png"},
    {"purpose": "any", "sizes": "512x512", "src": "/assets/icon-512.png", "type": "image/png"},
    {"purpose": "maskable", "sizes": "512x512", "src": "/assets/icon-maskable-512.png", "type": "image/png"},
]


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
async def readyz(w: WebCtx = Depends(web)) -> Response:
    """Ready to receive traffic: the state database responds and `/data` is readable (the edge and the
    `hub-update` only send traffic to a ready copy)."""
    problems = []
    if (db := w.auth_store.db) is not None:
        try:
            await asyncio.wait_for(db.pool.fetchval("SELECT 1"), 3)
        except Exception:
            problems.append("banco de estado")
    if not os.access(w.data_dir, os.R_OK | os.X_OK):
        problems.append("dados")
    if problems:
        return JSONResponse({"status": "not-ready", "problems": problems, "version": __version__}, 503)
    return JSONResponse({"status": "ready", "version": __version__})


@router.get("/assets/theme.css")
async def theme_css(w: WebCtx = Depends(web)) -> Response:
    """Installation theme layer (white label): semantic tokens only; the color is validated as #rrggbb."""
    out = "/* Tema da instalação (Plataforma › Configurações). */\n" + STAGING_BAR_CSS
    if c := w.brand_color():
        dark = f"--color-accent: color-mix(in srgb, {c} 45%, #ffffff);"
        out += f":root {{ --color-accent: {c}; --c-blue-31: {c}; }}\n"
        out += f':root[data-theme="dark"] {{ {dark} }}\n'
        out += f'@media (prefers-color-scheme: dark) {{ :root[data-theme="auto"] {{ {dark} }} }}\n'
    return Response(out, media_type="text/css", headers={"cache-control": "no-cache"})


@router.get("/assets/i18n/{file}")
async def i18n_catalog(file: str) -> Response:
    if file != "en.json":
        raise HTTPException(404)
    return JSONResponse(js_catalog(file[:-5]), headers={"cache-control": "no-cache"})


def asset(w: WebCtx, path: str) -> tuple[bytes, str, str] | None:
    """Body, type and ETag; the assets folder is often on a slow bind mount (Windows/Docker) —
    keeps them in memory and only rechecks the file every 2 s (dev mode: always)."""
    now = time.monotonic()
    hit = w.asset_cache.get(path)
    if hit and not devmode.enabled() and now - hit[0] < 2:
        return hit[3], hit[4], hit[5]
    f = find_asset(w.settings, path)
    if f is None:
        return None
    st = f.stat()
    if hit and hit[1] == st.st_mtime_ns and hit[2] == st.st_size:
        w.asset_cache[path] = (now, *hit[1:])
        return hit[3], hit[4], hit[5]
    body = f.read_bytes()
    mt = (
        "font/woff2"
        if f.suffix == ".woff2"
        else (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
    )
    etag = f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"'
    w.asset_cache[path] = (now, st.st_mtime_ns, st.st_size, body, mt, etag)
    return body, mt, etag


def _cache_of(path: str) -> str:
    """Fonts 7 days, vendor 1 day, the rest revalidates (ETag → 304); dev mode: no cache."""
    if devmode.enabled():
        return "no-store"
    if path.startswith("fonts/"):
        return "public, max-age=604800"
    return "public, max-age=86400" if path.startswith("vendor/") else "no-cache"


@router.get("/assets/{path:path}")
async def assets(request: Request, path: str, w: WebCtx = Depends(web)) -> Response:
    got = await asyncio.to_thread(asset, w, path)
    if got is None:
        raise HTTPException(404)
    body, mt, etag = got
    cache = _cache_of(path)
    headers = {"cache-control": cache, "etag": etag}
    if cache == "no-cache":  # CDN (e.g. Cloudflare) must not cache: else old CSS/JS after a deploy
        headers["cdn-cache-control"] = "no-store"
    if cache == "no-cache" and etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=mt, headers=headers)


@router.get("/hub-sw.js")
async def service_worker(w: WebCtx = Depends(web)) -> Response:
    f = find_asset(w.settings, "hub-sw.js")
    if f is None:
        raise HTTPException(404)
    headers = {"cache-control": "no-cache", "service-worker-allowed": "/"}
    return FileResponse(f, media_type="text/javascript", headers=headers)


@router.get("/manifest.webmanifest")
async def manifest(request: Request, start: str = "", w: WebCtx = Depends(web)) -> JSONResponse:
    lang = resolve("", request.headers.get("accept-language"))
    ents = sorted(w.gateway.registry.entries(), key=lambda e: (e.id != "coletor", e.id))
    s = start.strip()
    sid = s.removeprefix("/apps/") if s.startswith("/apps/") else s.removeprefix("/")
    brand = w.settings.brand
    data = {
        "id": "/",
        "name": brand,
        "short_name": brand.split()[-1] if brand.split() else "Hub",
        "lang": lang,
        "description": t("pwa.description", lang),
        "start_url": f"/apps/{sid}" if any(e.id == sid for e in ents) else "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": THEME,
        "theme_color": THEME,
        "icons": _ICONS,
        "shortcuts": [
            {
                "name": e.name,
                "short_name": e.display.get("short", e.name),
                "url": f"/apps/{e.id}",
                "icons": [{"src": "/assets/icon-192.png", "sizes": "192x192", "type": "image/png"}],
            }
            for e in ents[:4]
        ],
    }
    return JSONResponse(data, media_type="application/manifest+json", headers={"cache-control": "no-cache"})


@router.get("/offline", response_class=HTMLResponse)
async def offline(request: Request, w: WebCtx = Depends(web)) -> Response:
    lang = resolve("", request.headers.get("accept-language"))
    return w.templates.TemplateResponse(
        request,
        "offline.html",
        {
            "brand": w.settings.brand,
            "lang": lang,
            "title": t("pwa.offline_title", lang),
            "copy": t("pwa.offline_copy", lang),
            "retry": t("pwa.offline_retry", lang),
        },
        headers={"cache-control": "no-cache"},
    )
