"""The REST API is a thin layer over the engine's public facade.

Every engine import in ``packages/api/src`` goes through the top-level ``nl2sql``
namespace (``import nl2sql`` or ``from nl2sql import X``), never a submodule.
"""
import ast
import pathlib

import nl2sql

API_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _engine_submodule_imports():
    offenders = []
    for path in sorted(API_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.startswith("nl2sql.") and not name.startswith("nl2sql_api"):
                    offenders.append(f"{path.relative_to(API_SRC)}:{node.lineno} {name}")
    return offenders


def test_the_api_imports_only_the_top_level_nl2sql_namespace():
    offenders = _engine_submodule_imports()
    assert not offenders, f"import these from top-level nl2sql instead: {offenders}"


def test_the_query_response_is_the_engine_result_so_the_two_cannot_drift():
    from nl2sql_api.routes import query

    route = next(r for r in query.router.routes if r.path == "/query" and "POST" in r.methods)
    assert route.response_model is nl2sql.QueryResult
