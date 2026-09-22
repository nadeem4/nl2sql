"""``nl2sql cache clear``: empties the plan cache in the schema store, and nothing else."""
from __future__ import annotations

import re

from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.common.settings import settings
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.plan_cache import PlanCache
from nl2sql.schema import SqliteSchemaStore

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plan() -> PlanModel:
    return PlanModel.model_validate({
        "tables": [{"name": "Customer", "alias": "t1", "ordinal": 0}],
        "select_items": [{"ordinal": 0, "alias": "n",
                          "expr": {"kind": "column", "alias": "t1", "column_name": "CustomerId"}}],
    })


def _sq(intent: str) -> SubQuery:
    return SubQuery(id="sq1", datasource_id="chinook", intent=intent, schema_version="v1")


def test_cache_clear_removes_the_cached_plans_and_keeps_the_schema(tmp_path, monkeypatch):
    path = tmp_path / "data" / "schema_store.db"
    store = SqliteSchemaStore(path=path)
    cache = PlanCache(store)
    monkeypatch.setattr(settings, "plan_cache_enabled", True)
    cache.put(_sq("How many customers are there?"), _plan())
    cache.put(_sq("How many albums are there?"), _plan())
    store.close()
    monkeypatch.setattr(settings, "schema_store_backend", "sqlite")
    monkeypatch.setattr(settings, "schema_store_path", str(path))

    result = runner.invoke(app, ["cache", "clear"])

    out = ANSI.sub("", result.output)
    assert result.exit_code == 0, out
    assert "Cleared 2 cached plans" in out
    reopened = SqliteSchemaStore(path=path)
    try:
        assert PlanCache(reopened).get(_sq("How many customers are there?")) is None
        # The schema tables are untouched.
        assert reopened.list_versions("chinook") == []
        assert reopened._connection.execute("SELECT name FROM sqlite_master WHERE name='schema_snapshots'").fetchone()
    finally:
        reopened.close()


def test_cache_clear_without_a_store_file_creates_nothing(tmp_path, monkeypatch):
    path = tmp_path / "data" / "schema_store.db"
    monkeypatch.setattr(settings, "schema_store_backend", "sqlite")
    monkeypatch.setattr(settings, "schema_store_path", str(path))

    result = runner.invoke(app, ["cache", "clear"])

    out = ANSI.sub("", result.output)
    assert result.exit_code == 0, out
    assert "No plan cache" in out
    assert not path.exists()
