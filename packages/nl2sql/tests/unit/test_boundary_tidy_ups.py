"""Small boundary fixes from the architecture audit (F13, F16, F17, F19, F20)."""
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
