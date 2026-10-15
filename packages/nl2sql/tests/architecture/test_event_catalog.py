"""Every event the engine publishes is in the catalog (docs/architecture/wave_a.md, section 15).

An AST scan of every ``publish("<name>", ...)`` call site, ``bus.publish``,
``scope.publish`` and the module function ``nl2sql.events.publish`` alike.
The bus refuses an uncatalogued name at run time too; this finds one on a
path no test runs.
"""
from __future__ import annotations

import ast
import pathlib

from nl2sql.events.catalog import CATALOG

ENGINE = pathlib.Path(__file__).resolve().parents[2] / "src" / "nl2sql"


def _published_names():
    for path in sorted(ENGINE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in {"publish", "_publish"}:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                yield path.relative_to(ENGINE).as_posix(), node.lineno, first.value


def test_every_published_event_is_in_the_catalog():
    found = list(_published_names())
    offenders = [f"{path}:{lineno} {name}" for path, lineno, name in found if name not in CATALOG]
    assert not offenders, (
        "Every event the engine publishes is listed in nl2sql/events/catalog.py with a model for its "
        "data, so an observer author and the docs read the same list. Uncatalogued: " + ", ".join(offenders))
    assert "run.started" in {name for _, _, name in found}, "the scan found no publish call site"
