"""O OpenAPI gerado da tabela de operações equivale a `hub-v1.json` (gerado pelo hub Rust)."""

import json
from pathlib import Path
from typing import Any

from hub.api import ops

REF = json.loads((Path(__file__).parent / "vectors/hub-v1.json").read_text(encoding="utf-8"))
DROP = {"description", "title", "examples", "format", "default"}


def norm(v: Any) -> Any:
    """Forma comparável: sem textos/formatos; `oneOf[$ref|null]` e `type: [x, null]` viram `nullable`."""
    if isinstance(v, list):
        return [norm(x) for x in v]
    if not isinstance(v, dict):
        return v
    d = {k: norm(x) for k, x in v.items() if k not in DROP}
    if isinstance(d.get("type"), list):
        d["type"] = sorted(x for x in d["type"] if x != "null")[0]
        d["nullable"] = True
    for key in ("oneOf", "anyOf"):
        alts = d.get(key)
        if alts and {"type": "null"} in alts:
            rest = [a for a in alts if a != {"type": "null"}]
            del d[key]
            d["nullable"] = True
            if len(rest) == 1:
                d |= rest[0]
    return d


def test_document_shape_matches() -> None:
    doc = ops.openapi()
    for k in ("openapi", "info", "servers", "security", "tags"):
        assert doc[k] == REF[k], k
    assert doc["components"]["securitySchemes"] == REF["components"]["securitySchemes"]
    assert doc["components"]["responses"] == REF["components"]["responses"]


def test_paths_methods_operation_ids_and_operations_are_identical() -> None:
    doc = ops.openapi()
    assert doc["paths"].keys() == REF["paths"].keys()
    for path, item in REF["paths"].items():
        assert item.keys() == doc["paths"][path].keys(), path
        for method, op in item.items():
            assert doc["paths"][path][method] == op, f"{method} {path}"


def test_schemas_are_equivalent() -> None:
    got, ref = ops.sc.schemas(), REF["components"]["schemas"]
    assert got.keys() == ref.keys()
    for name, schema in ref.items():
        assert norm(got[name]) == norm(schema), name


def test_examples_are_kept() -> None:
    got, ref = ops.sc.schemas(), REF["components"]["schemas"]
    for name, schema in ref.items():
        assert got[name].get("examples") == schema.get("examples"), name
