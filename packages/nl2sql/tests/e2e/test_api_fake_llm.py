"""The HTTP query route runs the real graph end to end, with no API key.

FastAPI runs the synchronous ``/query`` handler in Starlette's threadpool, so
this exercises ``run_with_graph`` off the main thread -- the exact path that
``signal.signal`` used to break.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("nl2sql_api")

from .recordings_manufacturing import RULES_COUNT_EMPLOYEES  # noqa: E402


@pytest.mark.e2e
def test_query_route_returns_sql(demo_project, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient

    from nl2sql.common.settings import reload_settings
    from nl2sql_api.main import app

    server, env = fake_llm(RULES_COUNT_EMPLOYEES)
    monkeypatch.chdir(demo_project)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("LLM_CONFIG", "configs/llm.fake.yaml")
    reload_settings()

    try:
        # The context manager runs the lifespan, which constructs the real engine.
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/query",
                json={
                    "natural_language": "How many employees are there?",
                    "user_context": {"roles": ["admin"]},
                },
            )
    finally:
        reload_settings()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["errors"] == []
    assert "COUNT(" in body["sub_queries"][0]["sql"]
