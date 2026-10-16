"""The public API v1 components as JSON Schema (OpenAPI `components.schemas`, MCP `inputSchema`, edge validation),
built from the Pydantic models of `schemas` and `schemas_admin`."""

from __future__ import annotations

import copy
import functools
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel

from hub.api import schemas_admin as sa
from hub.api.schemas import EXAMPLES, MODELS

ALL: tuple[type, ...] = (*MODELS, *sa.MODELS)
_NULL = {"type": "null"}


def _clean_props(props: dict[str, Any]) -> dict[str, Any]:
    """Field names are kept as they are (a field may be called `title`)."""
    return {name: _clean(x) for name, x in props.items()}


def _clean(v: Any) -> Any:
    """Pydantic JSON Schema for the utoipa shape (no `title`; `anyOf` with null becomes nullable)."""
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if not isinstance(v, dict):
        return v
    out: dict[str, Any] = {
        k: _clean_props(x) if k == "properties" else _clean(x)
        for k, x in v.items()
        if k not in ("title", "default")
    }
    if out.get("additionalProperties") is True:
        del out["additionalProperties"]
    alts = out.get("anyOf")
    if alts and _NULL in alts:
        rest = [a for a in alts if a != _NULL]
        del out["anyOf"]
        if len(rest) == 1 and "$ref" not in rest[0] and isinstance(rest[0].get("type"), str):
            out |= rest[0] | {"type": [rest[0]["type"], "null"]}
        else:
            out["oneOf"] = [*rest, _NULL]
    return out


def schemas() -> dict[str, Any]:
    """All components (`#/components/schemas/<Name>`), with `examples` when present (a copy: callers may change it)."""
    return copy.deepcopy(_schemas())


@functools.cache
def _schemas() -> dict[str, Any]:
    """Built once: the models never change at runtime (and every MCP `tools/list` inlines dozens of them)."""
    out: dict[str, Any] = {}
    examples = EXAMPLES | sa.EXAMPLES
    for m in ALL:
        if issubclass(m, StrEnum):
            s: dict[str, Any] = {"type": "string", "enum": [e.value for e in m]}
        else:
            raw = cast(Any, m).model_json_schema(by_alias=True, ref_template="#/components/schemas/{model}")
            raw.pop("$defs", None)
            s = _clean(raw)
            s["additionalProperties"] = cast(Any, m).model_config.get("extra") != "forbid"
            if m.__doc__:
                s["description"] = m.__doc__.strip()
        if m.__name__ in examples:
            s["examples"] = [examples[m.__name__]]
        out[m.__name__] = s
    return out


def inline(name: str) -> dict[str, Any]:
    """Schema without `$ref` (the `inputSchema` of the MCP tools)."""
    all_ = _schemas()  # read only: `walk` builds new dicts and lists

    def walk(v: Any, depth: int = 0) -> Any:
        if isinstance(v, dict):
            if isinstance(r := v.get("$ref"), str):
                return walk(all_[r.rsplit("/", 1)[-1]], depth + 1) if depth < 8 else {}
            return {k: walk(x, depth) for k, x in v.items() if k != "examples"}
        if isinstance(v, list):
            return [walk(x, depth) for x in v]
        return v

    return walk(all_[name]) if name in all_ else {}


def model(name: str) -> type[BaseModel] | None:
    """The Pydantic model of a component (`None` = unknown or an enum)."""
    m = next((m for m in ALL if m.__name__ == name), None)
    return m if isinstance(m, type) and issubclass(m, BaseModel) else None


def example(name: str) -> Any:
    return EXAMPLES.get(name, sa.EXAMPLES.get(name))
