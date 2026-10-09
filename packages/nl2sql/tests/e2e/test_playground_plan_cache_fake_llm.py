"""The playground's plan-cache label follows the cache.

Debug says "Plan from the plan cache" when ``/api/ask`` returns a sub-query with
``plan_source: "cache"``. A running playground shares the schema store file with
``nl2sql --env demo cache clear``, so once that has run the next question calls
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
    from nl2sql.common.settings import reload_settings, settings

    project = tmp_path / "demo"
    shutil.copytree(demo_project, project)
    # This copy's store and nothing else: named outright for the engine here
    # and for the CLI below, and emptied of any plan another test cached in the
    # shared project before it was copied.
    store_path = project / "data" / "schema_store.db"
    store = SqliteSchemaStore(path=store_path)
    try:
        store.clear_plan_cache()
    finally:
        store.close()
    server = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    point_llm_config_at(project, server.base_url)

    monkeypatch.chdir(project)
    monkeypatch.setenv("SCHEMA_STORE_PATH", str(store_path))
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("LLM_CONFIG", "configs/llm.demo.yaml")
    monkeypatch.setenv("OPENAI_API_KEY", "replay")
    monkeypatch.setenv("PLAN_CACHE_ENABLED", "true")
    reload_settings()
    monkeypatch.setattr(settings, "plan_cache_enabled", True)

    try:
        engine = NL2SQL()
        app = build_app(engine, questions=[], roles=["admin"], mode="replay", dataset="chinook",
                        project_dir=project, host="127.0.0.1")
        client = TestClient(app, base_url="http://127.0.0.1:8765")
        question = {"question": "How many customers are there?", "role": "admin"}

        first = client.post("/api/ask", json=question).json()
        second = client.post("/api/ask", json=question).json()
        env = _base_env()
        env["PLAN_CACHE_ENABLED"] = "true"
        env["SCHEMA_STORE_PATH"] = str(store_path)
        cleared = run_cli(project, env, "cache", "clear")
        third = client.post("/api/ask", json=question).json()
    finally:
        server.stop()
        reload_settings()

    assert cleared.returncode == 0, cleared.stdout + cleared.stderr
    assert first["sub_queries"][0]["plan_source"] == "llm", first["errors"]
    assert second["sub_queries"][0]["plan_source"] == "cache"
    assert "Cleared 1 cached plans" in " ".join(cleared.stdout.split())
    assert third["sub_queries"][0]["plan_source"] == "llm"
