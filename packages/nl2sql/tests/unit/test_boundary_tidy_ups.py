"""Small boundary fixes from the architecture audit (F13, F16, F17, F18, F19, F20).

The package boundaries themselves are enforced in
``packages/nl2sql/tests/architecture/test_boundaries.py``.
"""
import pathlib
import tomllib

ENGINE = pathlib.Path(__file__).resolve().parents[2]
SRC = ENGINE / "src" / "nl2sql"


def _pyproject() -> dict:
    return tomllib.loads((ENGINE / "pyproject.toml").read_text(encoding="utf-8"))


# F13: the vendored Chinook database is a dataset, not part of the CLI.
def test_chinook_lives_in_the_neutral_datasets_package():
    from nl2sql.datasets import CHINOOK_DB_PATH

    assert CHINOOK_DB_PATH.is_file()
    assert "cli" not in CHINOOK_DB_PATH.relative_to(SRC).parts


def test_the_wheel_carries_the_chinook_database():
    package_data = _pyproject()["tool"]["setuptools"]["package-data"]
    assert "*.sqlite" in package_data["nl2sql.datasets"]
    assert not any(p.startswith("data/") and p.endswith(".sqlite") for p in package_data.get("nl2sql.cli.demo", []))


# F18: the DAG models are neutral, so the aggregation service need not import
# the pipeline. The import rule itself lives in tests/architecture/.
def test_the_decomposer_builds_the_neutral_dag_models():
    from nl2sql.execution import dag
    from nl2sql.pipeline.nodes.decomposer import dag as builder

    assert builder.ExecutionDAG is dag.ExecutionDAG
    assert builder.LogicalNode is dag.LogicalNode


# F16: SQL is compared in the datasource's dialect, not sqlglot's default.
def test_semantic_sql_comparison_parses_in_the_given_dialect():
    from nl2sql.evaluation.evaluator import ModelEvaluator

    # sqlglot's default dialect cannot parse T-SQL's TOP at all.
    assert ModelEvaluator.compare_sql_semantic("SELECT TOP 5 [Name] FROM t", "select top 5 [Name] from t",
                                               dialect="tsql")


# F17: the SDK's ResultFrame/ResultError are the only result and error envelopes.
def test_sqlalchemy_base_has_no_duplicate_result_models():
    import nl2sql_sdk.sqlalchemy as base

    # B24: DryRunResult, QueryPlan and CostEstimate went with dry_run, explain and cost_estimate.
    for name in ("QueryResult", "AdapterError", "DryRunResult", "QueryPlan", "CostEstimate"):
        assert not hasattr(base, name)


# F20: no core dependency the engine never imports.
def test_pandas_is_not_a_core_dependency():
    deps = [d.split(">")[0].split("=")[0].split("<")[0].strip().lower() for d in _pyproject()["project"]["dependencies"]]
    assert "pandas" not in deps


# --- The architecture review's dead-code list. Each of these was defined,
# exported or declared and read by nothing; the assertions keep them gone.

def test_every_error_code_is_raised_or_is_a_declared_refusal():
    """No `ErrorCode` member exists that nothing raises and nothing explains.

    Eleven did. A code nothing can produce is a promise to a caller the engine
    never keeps: it reaches the docs and the trace schema, and someone writes a
    handler for a case that cannot happen.

    A code with an entry in `SAFE_ERROR_MESSAGES` is the exception: that entry
    is the caller-facing text for a refusal the engine has committed to, which
    makes it a declared path rather than a leftover.
    """
    import re

    from nl2sql.common.errors import SAFE_ERROR_MESSAGES, ErrorCode

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SRC.rglob("*.py")
        if path.name != "errors.py"
    )
    raised = set(re.findall(r"ErrorCode\.([A-Z_]+)", source))
    explained = {code.name for code in SAFE_ERROR_MESSAGES}

    assert {member.name for member in ErrorCode} - raised - explained - set(DECLARED_AHEAD) == set()
    # A code declared ahead leaves the list in the PR that raises it.
    assert not set(DECLARED_AHEAD) & raised, sorted(set(DECLARED_AHEAD) & raised)


# Codes added by a wave's foundation PR before their producers, so the parallel
# PRs never edit errors.py (docs/architecture/wave_a.md, D35; wave_b.md, D59),
# each with the PR that raises it. Wave A's are all raised now (S1, E3).
DECLARED_AHEAD: dict = {
    "CLARIFICATION_INVALID": "L2",
    "CLARIFICATION_STALE": "L2",
    "COST_LIMIT_EXCEEDED": "C2",
}


def test_cancelled_is_a_string_not_a_one_tuple():
    # `CANCELLED = "CANCELLED",` worked only because ErrorCode mixes in str,
    # so the enum machinery unpacked the tuple as constructor arguments.
    from nl2sql.common.errors import ErrorCode

    assert ErrorCode.CANCELLED.value == "CANCELLED"
    assert all(isinstance(member.value, str) for member in ErrorCode)


def test_every_capability_flag_is_one_something_queries():
    """`DatasourceCapability` carries only flags a code path checks.

    Six did not, and `SUPPORTS_REST` went with adapter interface 2. The two
    enforcement flags are what an adapter proves to the conformance kit;
    `check()` reads `ENFORCES_READ_ONLY` to warn when a session can write.
    """
    import re

    from nl2sql_sdk.capabilities import DatasourceCapability

    source = "\n".join(path.read_text(encoding="utf-8") for path in SRC.rglob("*.py"))
    queried = set(re.findall(r"DatasourceCapability\.([A-Z_]+)", source))

    assert {member.name for member in DatasourceCapability} - queried <= {"ENFORCES_TIMEOUT"}


def test_the_public_facade_exposes_nothing_without_methods():
    # `NL2SQL().results` was an empty class exported as `nl2sql.ResultAPI`,
    # mounted on the facade and given a docs page. Removing it is a public API
    # break, which is why it is worth doing while the surface is small.
    import nl2sql

    assert "ResultAPI" not in nl2sql.__all__
    assert not hasattr(nl2sql, "ResultAPI")
