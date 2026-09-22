"""Import boundaries from the architecture audit (F14, F18).

- ``import nl2sql`` must not load ``nl2sql.evaluation``: the benchmark is an
  opt-in tool, not part of the runtime every SDK user pays for.
- ``nl2sql.aggregation`` must not import ``nl2sql.pipeline``: the pipeline's
  aggregator node uses the aggregation service, so the reverse edge was a cycle.
  The DAG models both need live in ``nl2sql.execution.dag``.
"""
import ast
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "nl2sql"


def _run(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_import_nl2sql_does_not_load_evaluation():
    assert _run("import sys, nl2sql; print('nl2sql.evaluation' in sys.modules)") == "False"


def test_building_the_benchmark_api_loads_evaluation_on_demand():
    code = ("import sys, nl2sql; from nl2sql import BenchmarkAPI, BenchmarkConfig; "
            "print('nl2sql.evaluation' in sys.modules, BenchmarkConfig.__module__)")
    assert _run(code) == "True nl2sql.evaluation.types"


def test_the_engine_builds_its_benchmark_api_lazily():
    from nl2sql.public_api import NL2SQL

    assert isinstance(NL2SQL.__dict__["benchmark"], property)


def _imports(path: pathlib.Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_aggregation_never_imports_the_pipeline():
    offenders = [f"{path.relative_to(SRC)}: {name}"
                 for path in (SRC / "aggregation").rglob("*.py")
                 for name in _imports(path) if name.startswith("nl2sql.pipeline")]
    assert offenders == []


def test_the_global_planner_uses_the_neutral_dag_models():
    from nl2sql.execution import dag
    from nl2sql.pipeline.nodes.global_planner import schemas

    assert schemas.ExecutionDAG is dag.ExecutionDAG
    assert schemas.LogicalNode is dag.LogicalNode
