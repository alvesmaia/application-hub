"""Corporate network for the apps the hub builds and runs (install.sh --proxy/--ca-file/mirrors): build args for the
package build and environment for the app containers. A plain install (no such options) sets nothing.

The app's own calls to the hub (app-hub-web, app-hub-objstore) are excluded from the proxy by name: NO_PROXY has no
"single label" rule, so the fixed names of the hub services are listed."""

from __future__ import annotations

import os
import re
from pathlib import Path

from hub import httpclients

HUB_NAMES = (
    "localhost",
    "127.0.0.1",
    "app-hub-web",
    "app-hub-objstore",
    "app-hub-app-db",
    "hub-web",
    "hub-objstore",
)
CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"  # where the Dockerfiles append EXTRA_CA_PEM
_USERINFO = re.compile(r"(://)[^/@\s]*@")


def _ca_pem() -> str:
    try:
        return Path(os.getenv("HUB_EXTRA_CA_FILE", httpclients.CA_FILE)).read_text(encoding="utf-8")
    except OSError:
        return ""


def _proxy_env() -> dict[str, str]:
    proxy = httpclients.proxy_url()
    if not proxy:
        return {}
    np = ",".join(dict.fromkeys([*HUB_NAMES, *httpclients.no_proxy()]))
    return {k: proxy for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")} | {
        "NO_PROXY": np,
        "no_proxy": np,
    }


def build_args(podman: bool = False) -> dict[str, str]:
    """Build args of a package build (the template Dockerfile declares them; unused ones are only a warning).
    Podman (buildah) records build args in the image history, so an authenticated proxy is left out there."""
    proxy = httpclients.proxy_url() or ""
    out = {} if podman and _USERINFO.search(proxy) else _proxy_env()
    if pem := _ca_pem():
        out["EXTRA_CA_PEM"] = pem
    if index := os.getenv("HUB_PYPI_INDEX", "").strip():
        out |= {"PIP_INDEX_URL": index, "UV_DEFAULT_INDEX": index}
    if prefix := os.getenv("HUB_IMAGE_PREFIX", "").strip():
        out["IMAGE_PREFIX"] = prefix
    return out


def container_env() -> dict[str, str]:
    """Environment of an app container; the app's own values win over these."""
    out = _proxy_env()
    if _ca_pem():
        out |= {"SSL_CERT_FILE": CA_BUNDLE, "REQUESTS_CA_BUNDLE": CA_BUNDLE}
    return out


def mask(line: str) -> str:
    """Hides the user:password of URLs (proxy credentials) in a build log line."""
    return _USERINFO.sub(r"\1***@", line)
