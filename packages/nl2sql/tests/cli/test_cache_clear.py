"""``nl2sql cache clear``: empties the plan cache in the state store, and nothing else."""
from __future__ import annotations

import re

from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.feedback import rated_row
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.plan_cache import PlanCache
from nl2sql.storage.sqlite import SqliteState

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plan() -> PlanModel:
    return PlanModel.model_validate({
        "tables": [{"name": "Customer", "alias": "t1"}],
        "select_items": [{"alias": "n",
                          "expr": {"kind": "column", "alias": "t1", "column_name": "CustomerId"}}],
    })


def _sq(intent: str) -> SubQuery:
    return SubQuery(id="sq1", datasource_id="chinook", intent=intent, schema_version="v1")


def _opened(path) -> SqliteState:
    state = SqliteState(path)
    state.migrate()
    return state


def test_cache_clear_removes_the_cached_plans_and_keeps_the_feedback(project_stores):
    store = _opened(project_stores.state)
    cache = PlanCache(store)
    cache.put(_sq("How many customers are there?"), _plan())
    cache.put(_sq("How many albums are there?"), _plan())
    store.add_feedback(rated_row({"trace_id": "t1"}, "up", None))
    store.close()

    result = runner.invoke(app, ["cache", "clear"])

    out = ANSI.sub("", result.output)
    assert result.exit_code == 0, out
    assert "Cleared 2 cached plans" in out
    reopened = _opened(project_stores.state)
    try:
        assert PlanCache(reopened).get(_sq("How many customers are there?")) is None
        assert len(reopened.list_feedback()) == 1
    finally:
        reopened.close()


def test_cache_clear_on_a_new_project_clears_nothing(project_stores):
    result = runner.invoke(app, ["cache", "clear"])

    out = ANSI.sub("", result.output)
    assert result.exit_code == 0, out
    assert "Cleared 0 cached plans" in out
