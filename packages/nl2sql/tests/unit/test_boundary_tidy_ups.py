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
    from nl2sql.evaluation.gold import CHINOOK_DB_PATH as gold_path

    assert CHINOOK_DB_PATH.is_file()
    assert "cli" not in CHINOOK_DB_PATH.relative_to(SRC).parts
    assert gold_path == CHINOOK_DB_PATH


def test_the_wheel_carries_the_chinook_database():
    package_data = _pyproject()["tool"]["setuptools"]["package-data"]
    assert "*.sqlite" in package_data["nl2sql.datasets"]
    assert not any(p.startswith("data/") and p.endswith(".sqlite") for p in package_data.get("nl2sql.cli.demo", []))


# F18: the DAG models are neutral, so the aggregation service need not import
# the pipeline. The import rule itself lives in tests/architecture/.
def test_the_global_planner_uses_the_neutral_dag_models():
    from nl2sql.execution import dag
    from nl2sql.pipeline.nodes.global_planner import schemas

    assert schemas.ExecutionDAG is dag.ExecutionDAG
    assert schemas.LogicalNode is dag.LogicalNode


# F16: SQL is compared in the datasource's dialect, not sqlglot's default.
def test_semantic_sql_comparison_parses_in_the_given_dialect():
    from nl2sql.evaluation.evaluator import ModelEvaluator

    # sqlglot's default dialect cannot parse T-SQL's TOP at all.
    assert ModelEvaluator.compare_sql_semantic("SELECT TOP 5 [Name] FROM t", "select top 5 [Name] from t",
                                               dialect="tsql")


# F17: the SDK's ResultFrame/ResultError are the only result and error envelopes.
def test_sqlalchemy_base_has_no_duplicate_result_models():
    from nl2sql.adapters import sqlalchemy_base
    from nl2sql.adapters.sqlalchemy_base import models

    for name in ("QueryResult", "AdapterError"):
        assert not hasattr(models, name)
        assert name not in sqlalchemy_base.__all__


# F19: index health opens the snapshot store through the factory.
def test_index_health_builds_its_snapshot_store_through_the_factory(tmp_path, monkeypatch):
    from langchain_core.documents import Document
    from langchain_core.embeddings import FakeEmbeddings

    from nl2sql.common.settings import settings
    from nl2sql.indexing import health
    from nl2sql.indexing.vector_store import VectorStore

    store = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=FakeEmbeddings(size=8))
    store.vectorstore.add_documents([Document(page_content="ds", metadata={
        "type": "schema.datasource", "datasource_id": "chinook", "schema_version": "v9"})])
    (tmp_path / "schema.db").touch()
    calls = []

    class _Snapshots:
        def get_latest_version(self, ds_id):
            return "v9"

    def fake_build(backend, max_versions, path=None):
        calls.append((backend, max_versions, path))
        return _Snapshots()

    monkeypatch.setattr(health, "build_schema_store", fake_build)
    result = health.inspect_index_at(tmp_path / "vs", "nl2sql_store", tmp_path / "schema.db", ["chinook"])

    assert calls == [(settings.schema_store_backend, settings.schema_store_max_versions, tmp_path / "schema.db")]
    assert result.datasources[0].snapshot_version == "v9"


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

    assert {member.name for member in ErrorCode} - raised - explained == set()


def test_cancelled_is_a_string_not_a_one_tuple():
    # `CANCELLED = "CANCELLED",` worked only because ErrorCode mixes in str,
    # so the enum machinery unpacked the tuple as constructor arguments.
    from nl2sql.common.errors import ErrorCode

    assert ErrorCode.CANCELLED.value == "CANCELLED"
    assert all(isinstance(member.value, str) for member in ErrorCode)


def test_every_capability_flag_is_one_something_queries():
    """`DatasourceCapability` carries only flags a code path checks.

    Six did not. `SUPPORTS_SQL` is the only flag the engine ever queries;
    `SUPPORTS_REST` is kept as the stand-in for a datasource the SQL agent
    cannot serve, which is what the capability-gating tests are about.
    """
    import re

    from nl2sql_adapter_sdk.capabilities import DatasourceCapability

    source = "\n".join(path.read_text(encoding="utf-8") for path in SRC.rglob("*.py"))
    queried = set(re.findall(r"DatasourceCapability\.([A-Z_]+)", source))

    assert {member.name for member in DatasourceCapability} - queried == {"SUPPORTS_REST"}


def test_the_public_facade_exposes_nothing_without_methods():
    # `NL2SQL().results` was an empty class exported as `nl2sql.ResultAPI`,
    # mounted on the facade and given a docs page. Removing it is a public API
    # break, which is why it is worth doing while the surface is small.
    import nl2sql

    assert "ResultAPI" not in nl2sql.__all__
    assert not hasattr(nl2sql, "ResultAPI")
