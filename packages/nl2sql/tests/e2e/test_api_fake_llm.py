"""The HTTP query route runs the real graph end to end, with no API key.

FastAPI runs the synchronous ``/query`` handler in Starlette's threadpool, so
this exercises ``run_with_graph`` off the main thread -- the exact path that
``signal.signal`` used to break.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("nl2sql_api")

from .recordings_chinook import RULES_COUNT_CUSTOMERS, RULES_TOP_CUSTOMERS  # noqa: E402


@pytest.mark.e2e
def test_query_route_returns_sql(demo_project, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient

    from nl2sql.common.settings import reload_settings
    from nl2sql_api.main import app

    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
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
                    "natural_language": "How many customers are there?",
                    "user_context": {"roles": ["admin"]},
                },
            )
    finally:
        reload_settings()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["errors"] == []
    assert "COUNT(" in body["sub_queries"][0]["sql"]
    # Everything the playground's four panes render: status, plan, checks, rows.
    assert body["status"] == "success"
    assert body["sub_queries"][0]["rows"]["total_rows"] == 1
    assert body["sub_queries"][0]["validation"][2]["name"] == "policy"
    assert body["sub_queries"][0]["plan"]["tables"]
    assert {"ast_planner", "logical_validator", "generator", "executor"} <= set(body["timings"])


@pytest.mark.e2e
def test_a_denied_query_reports_the_denial_first(demo_project, fake_llm, monkeypatch):
    """A refused query ends at the refusal: status ``error``, denial first.

    The aggregator and answer synthesizer used to run after a denial, adding
    AGGREGATOR_FAILED and, against a real model, a rate-limit error from a
    synthesizer call that had nothing to explain.
    """
    from fastapi.testclient import TestClient

    from nl2sql.common.settings import reload_settings
    from nl2sql_api.main import app

    server, env = fake_llm(RULES_TOP_CUSTOMERS)
    monkeypatch.chdir(demo_project)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("LLM_CONFIG", "configs/llm.fake.yaml")
    reload_settings()

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/query",
                json={
                    "natural_language": "Who are the top customers by total spend?",
                    "user_context": {"roles": ["viewer"]},
                },
            )
    finally:
        reload_settings()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "error"
    assert "SECURITY_VIOLATION" in str(body["errors"][0]["error_code"])
    codes = " ".join(str(e["error_code"]) for e in body["errors"])
    assert "AGGREGATOR_FAILED" not in codes
    assert "answer_synthesizer" not in body["timings"]
    assert "AggregatedResponse" not in [c["name"] for c in server.calls]
