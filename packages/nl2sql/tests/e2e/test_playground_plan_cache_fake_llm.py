"""The playground's plan-cache label follows the cache.

Debug says "Plan from the plan cache" when ``/api/ask`` returns a sub-query with
``plan_source: "cache"``. A running playground shares the state store file with
``nl2sql cache clear`` run on the same ``nl2sql.yaml``, so once that has run the next question calls
the planner again and the label goes away, without a restart.

The test works on its own copy of the demo project, and that copy's plan cache
is emptied before the first question. ``demo_project`` is shared by the whole
session, so whatever another test left in its schema store would otherwise
answer the first question from the cache, and the outcome would depend on the
order the tests ran in.
"""
import shutil

import pytest

pytest.importorskip("fastapi")

from nl2sql.cli.demo.llm_config import point_llm_config_at  # noqa: E402
from nl2sql.schema import SqliteSchemaStore  # noqa: E402
from nl2sql.testing.fake_llm import FakeLLMServer  # noqa: E402

from .conftest import _base_env, run_cli  # noqa: E402
from .recordings_chinook import RULES_COUNT_CUSTOMERS  # noqa: E402


@pytest.mark.e2e
def test_after_cache_clear_the_playground_reports_a_planner_call(demo_project, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from nl2sql import NL2SQL
    from nl2sql.cli.demo.playground.app import build_app
    project = tmp_path / "demo"
    shutil.copytree(demo_project, project)
    # This copy's store and nothing else (its nl2sql.yaml keeps it under the
    # copy), emptied of any plan another test cached in the shared project
    # before it was copied. The demo's own config leaves the plan cache on.
    store_path = project / ".nl2sql" / "state.db"
    store = SqliteSchemaStore(path=store_path)
    try:
        store.clear_plan_cache()
    finally:
        store.close()
    server = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    point_llm_config_at(project, server.base_url, api_key="replay")

    try:
        engine = NL2SQL.from_config(project / "nl2sql.yaml")
        app = build_app(engine, questions=[], roles=["admin"], mode="replay", dataset="chinook",
                        project_dir=project, host="127.0.0.1")
        client = TestClient(app, base_url="http://127.0.0.1:8765")
        question = {"question": "How many customers are there?", "role": "admin"}

        first = client.post("/api/ask", json=question).json()
        second = client.post("/api/ask", json=question).json()
        cleared = run_cli(project, _base_env(), "cache", "clear", config="nl2sql.yaml")
        third = client.post("/api/ask", json=question).json()
    finally:
        server.stop()

    assert cleared.returncode == 0, cleared.stdout + cleared.stderr
    assert first["sub_queries"][0]["plan_source"] == "llm", first["errors"]
    assert second["sub_queries"][0]["plan_source"] == "cache"
    assert "Cleared 1 cached plans" in " ".join(cleared.stdout.split())
    assert third["sub_queries"][0]["plan_source"] == "llm"
