"""CSP for app pages."""

from __future__ import annotations

from collections.abc import Callable

from hub.gateway.env_path import public


def app_url(app: str) -> str:
    return f"/apps/{app}"


def _ws_origin(origin: str | None, path: str) -> str:
    if origin is None:
        return "'self'"
    if origin.startswith("https://"):
        return f"wss://{origin[8:]}{path}"
    return f"ws://{origin.removeprefix('http://')}{path}"


_provider: Callable[[str, bool], list[tuple[str, str]]] | None = None


def set_script_provider(fn: Callable[[str, bool], list[tuple[str, str]]] | None) -> None:
    """Who says which app scripts were approved in review (ops, when assembling)."""
    global _provider
    _provider = fn


def approved_scripts(app: str, staging: bool) -> list[tuple[str, str]]:
    """`(file, "sha384-...")` approved in the security review of the app's active version (empty without ops)."""
    return _provider(app, staging) if _provider else []


def csp_with(
    origin: str | None, app: str | None, approved: list[tuple[str, str]] | None = None, env: str = "producao"
) -> str:
    """`env` = the page's environment: a staging page (`/staging/apps/…`) talks to the `/staging/…` URLs."""

    def o(p: str) -> str:
        return f"{origin}{p}" if origin else "'self'"

    base = "style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data: blob:"
    if app is None:
        return (
            f"default-src 'self'; script-src {o('/assets/')}; {base}; connect-src 'self'; "
            f"worker-src {o('/hub-sw.js')}; manifest-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        )
    frag, au = public(f"/_frag/{app}", env), public(app_url(app), env)
    scripts = o("/assets/")
    for file, sha in approved or []:
        scripts += f" '{sha}'"
        if origin:  # without an origin only the hash, never 'self'
            scripts += f" {o(f'{frag}/assets/{file}')}"
    if origin:
        connect = (
            f"{o(frag)} {o(frag + '/')} {_ws_origin(origin, f'{au}/ws/')} {_ws_origin(origin, f'{au}/ws')}"
        )
        forms = (
            f"{o(au)} {o(au + '/')} {o('/logout')} {o('/account/prefs')} {o('/account/notifications/read')}"
        )
    else:
        connect, forms = "'self'", "'none'"
    return (
        f"default-src 'self'; script-src {scripts}; {base}; connect-src {connect}; "
        f"worker-src {o('/hub-sw.js')}; manifest-src 'self'; form-action {forms}; "
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; "
        "require-trusted-types-for 'script'; trusted-types hub-parse hub-svg hub-script default"
    )


def sandbox_fragment(csp: str | None) -> str:
    return f"{csp or "default-src 'none'"}; sandbox"
