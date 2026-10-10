"""Barra segredos no commit: .dev.env, .prod.env, chaves/certificados, tokens hubt_, credenciais do cloudflared."""

import re
import sys
from pathlib import Path

NAME = re.compile(
    r"(^|/)(\.dev\.env|\.prod\.env|\.env|cert\.pem|[^/]*\.(pem|key|pfx|p12)|[0-9a-f-]{36}\.json)$|(^|/)\.cloudflared/",
    re.I,
)
TOKEN = re.compile(r"hubt_[A-Za-z0-9]{16,}|TunnelSecret|\"AccountTag\"")
OK_PATH = re.compile(r"(^|/)(tests?|fixtures)/|(^|/)secret_guard.py$")


def main() -> int:
    bad = []
    for f in sys.argv[1:]:
        p = f.replace("\\", "/")
        if OK_PATH.search(p):
            continue
        if NAME.search(p):
            bad.append(f"{f}: arquivo de segredo")
            continue
        try:
            text = Path(f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if TOKEN.search(text):
            bad.append(f"{f}: parece token/credencial")
    print("\n".join(bad)) if bad else None
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
