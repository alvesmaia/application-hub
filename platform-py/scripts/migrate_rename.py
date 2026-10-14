"""Migrates the hub state from `hubpy-*` to `app-hub-*` (F7). Usage (inside the app-hub-web image, /data = copy of the volume):
    python scripts/migrate_rename.py /data
Rewrites container/host names in admin.json (vault values are opened and sealed again) and in the state JSONs.
Passwords containing `hubpy-` (hubpy-dev, hubpy-app-dev, hubpy-objstore-dev) do NOT change. The audit chain is not touched."""

import json
import sys
from pathlib import Path

from hub.vault import Vault, is_sealed

KEEP = ("hubpy-app-dev", "hubpy-objstore-dev", "hubpy-dev")


def rename(text: str) -> str:
    for i, k in enumerate(KEEP):
        text = text.replace(k, f"@@{i}@@")
    text = text.replace("hubpy_default", "app-hub_default").replace("hubpy-", "app-hub-")
    for i, k in enumerate(KEEP):
        text = text.replace(f"@@{i}@@", k)
    return text


def main(data: Path) -> None:
    vault = Vault.open(data)
    p = data / "admin.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    for e in doc.get("env", []):
        if is_sealed(e["value"]):
            e["value"] = vault.open_value(e["value"], f"admin.env:{e['name']}")
    kinds = {e["name"]: e.get("kind") for e in doc.get("env", [])}
    out = json.loads(rename(json.dumps(doc, ensure_ascii=False)))
    for e in out.get("env", []):
        if kinds.get(e["name"]) != "publica":
            e["value"] = vault.seal(e["value"], f"admin.env:{e['name']}")
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for n in ("ops.json", "ux.json", "inbox.json", "analytics.json", "deployments.json", "connections.json"):
        f = data / n
        if f.exists():
            f.write_text(rename(f.read_text(encoding="utf-8")), encoding="utf-8")
    print("estado migrado")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
