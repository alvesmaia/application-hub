"""Child process that runs a package: ``python -I -B -m automations.sandbox main.py:run`` with the
package extracted in the working directory.

Protocol: params as JSON on stdin; on stdout ONE JSON line — ``{"ok": {...}}`` or
``{"error": "message"}``; anything the package prints (and tracebacks) goes to stderr, which the
app keeps as the run log. Exit code 0 only on success. ``run(params)`` may be sync or async and
must return a JSON object.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import io
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, TextIO


def load(entrypoint: str) -> Any:
    file, func = entrypoint.split(":", 1)
    sys.path.insert(0, os.getcwd())  # the package's sibling modules (``import helpers``)
    spec = importlib.util.spec_from_file_location("automation_entry", Path(file).resolve())
    if spec is None or spec.loader is None:
        raise ImportError(f"não foi possível carregar {file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses (and pickling) look their module up there
    spec.loader.exec_module(module)
    return getattr(module, func)


def call(entrypoint: str, params: dict[str, Any]) -> dict[str, Any]:
    result = load(entrypoint)(params)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    if not isinstance(result, dict):
        raise TypeError(f"run() devolveu {type(result).__name__}; precisa devolver um objeto JSON (dict)")
    return result


def main(argv: list[str], stdin: TextIO, out: TextIO) -> int:
    sys.stdout = sys.stderr  # the package's prints go to the log, never mixed with the result
    try:
        params = json.loads(stdin.read() or "{}")
        result = call(argv[0], params)
        line = json.dumps({"ok": result}, ensure_ascii=False, default=str)
    except Exception as e:
        traceback.print_exc()
        out.write(json.dumps({"error": str(e) or type(e).__name__}, ensure_ascii=False) + "\n")
        return 1
    out.write(line + "\n")
    return 0


if __name__ == "__main__":
    # ``-I`` ignores PYTHONIOENCODING: the pipes are UTF-8 on every platform (Windows defaults to cp1252).
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:], sys.stdin, sys.stdout))
