"""The HTTP query route runs the real graph end to end, with no API key.

FastAPI runs the synchronous ``/query`` handler in Starlette's threadpool, so
this exercises ``run_with_graph`` off the main thread -- the exact path that
``signal.signal`` used to break.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("nl2sql_api")

from .recordings_chinook import RULES_COUNT_CUSTOMERS, RULES_TOP_CUSTOMERS  # noqa: E402


@pytest.fixture(autouse=True)
def _role_from_a_trusted_proxy_header(monkeypatch):
    """The API takes the role from a proxy header, as a deployment would."""
    monkeypatch.setenv("NL2SQL_API_ROLE_HEADER", "X-NL2SQL-Role")


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
                json={"natural_language": "How many customers are there?"},
                headers={"X-NL2SQL-Role": "admin"},
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
                json={"natural_language": "Who are the top customers by total spend?"},
                headers={"X-NL2SQL-Role": "viewer"},
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


@pytest.mark.e2e
def test_an_unanswerable_question_is_refused_after_one_model_call(demo_project, fake_llm, monkeypatch):
    """No datasource holds the weather: the resolver refuses, and nothing else runs."""
    from fastapi.testclient import TestClient

    from nl2sql.common.settings import reload_settings
    from nl2sql.testing.fake_llm import Rule
    from nl2sql_api.main import app

    unanswerable = Rule("AnswerabilityResponse",
                        {"answerable_datasource_ids": [], "reason": "Chinook holds no weather data."})
    server, env = fake_llm([unanswerable] + RULES_COUNT_CUSTOMERS)
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
                json={"natural_language": "What is the weather in Paris?"},
                headers={"X-NL2SQL-Role": "admin"},
            )
    finally:
        reload_settings()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "error"
    [error] = body["errors"]
    assert error["error_code"] == "QUESTION_NOT_ANSWERABLE"
    assert "can't be answered" in error["message"]
    assert body["sub_queries"] == []
    assert [c["name"] for c in server.calls] == ["AnswerabilityResponse"]
    assert not {"decomposer", "ast_planner", "refiner", "answer_synthesizer"} & set(body["timings"])


# Magnitudes from a real Chinook run (tiktoken o200k_base): decomposer ~1.9k input
# tokens, the AST planner ~11k per call (7.7k of it the schema block), the refiner
# ~8.6k, the synthesizer a few hundred for one row. The planner's second call
# re-sends the same schema block, which is what a provider's prompt cache serves.
def _usage(prompt, completion, cached=0, reasoning=0):
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": reasoning}}


def _rules_with_one_retry():
    """Count customers, but the first plan names a table that does not exist.

    The logical validator rejects it, the refiner runs, and the planner's second
    plan is the right one: two planner calls and one refiner call per question.
    """
    from nl2sql.testing.fake_llm import Rule

    from .recordings_chinook import (ANSWERABLE, COUNT_CUSTOMERS_DECOMPOSER, COUNT_CUSTOMERS_PLAN,
                                     count_customers_answer)

    bad_plan = {**COUNT_CUSTOMERS_PLAN, "tables": [{"name": "Customers", "alias": "t1", "ordinal": 0}]}
    planner_calls = []

    def plan(_text):
        planner_calls.append(1)
        return bad_plan if len(planner_calls) % 2 == 1 else COUNT_CUSTOMERS_PLAN

    return [
        Rule(ANSWERABLE.name, ANSWERABLE.payload, usage=_usage(900, 30, cached=768)),
        Rule("DecomposerResponse", COUNT_CUSTOMERS_DECOMPOSER, usage=_usage(1900, 250)),
        Rule("PlanModel", plan, usage=_usage(11000, 300, cached=7680, reasoning=128)),
        Rule("AggregatedResponse", count_customers_answer, usage=_usage(300, 40)),
        Rule("plain", "Use the table Customer, not Customers.", usage=_usage(8600, 60, reasoning=32)),
    ]


def _assert_usage(usage):
    nodes = usage["nodes"]
    assert set(nodes) == {"datasource_resolver", "decomposer", "ast_planner", "refiner", "answer_synthesizer"}
    assert nodes["datasource_resolver"]["calls"] == 1
    planner = nodes["ast_planner"]
    assert planner["calls"] == 2
    assert (planner["input_tokens"], planner["cached_input_tokens"]) == (22000, 15360)
    assert (planner["output_tokens"], planner["reasoning_tokens"]) == (600, 256)
    assert nodes["refiner"]["calls"] == 1 and nodes["refiner"]["reasoning_tokens"] == 32
    assert nodes["decomposer"]["input_tokens"] == 1900
    assert nodes["answer_synthesizer"]["input_tokens"] == 300
    total = usage["total"]
    assert total["calls"] == 6
    assert total["input_tokens"] == 900 + 1900 + 22000 + 8600 + 300
    assert total["cached_input_tokens"] == 768 + 15360
    assert total["output_tokens"] == 30 + 250 + 600 + 60 + 40
    assert total["reasoning_tokens"] == 256 + 32
    assert total["total_tokens"] == total["input_tokens"] + total["output_tokens"]
    assert total["latency_s"] > 0
    assert total["cost"] is None  # no price configured
    assert [c["node"] for c in usage["calls"]].count("ast_planner") == 2
    assert all(c["model"].startswith("gpt-4o") for c in usage["calls"])


@pytest.mark.e2e
def test_usage_reaches_query_result_and_the_rest_response(demo_project, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient

    from nl2sql.api.query_api import QueryResult
    from nl2sql.auth.models import UserContext
    from nl2sql.common.settings import reload_settings
    from nl2sql_api.main import app

    server, env = fake_llm(_rules_with_one_retry())
    monkeypatch.chdir(demo_project)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("LLM_CONFIG", "configs/llm.fake.yaml")
    monkeypatch.setenv("SQL_AGENT_RETRY_BASE_DELAY_SEC", "0")
    monkeypatch.setenv("SQL_AGENT_RETRY_JITTER_SEC", "0")
    reload_settings()

    try:
        with TestClient(app) as client:
            # The Python API: the engine the REST route delegates to.
            result = app.state.engine.run_query(
                "How many customers are there?", user_context=UserContext(roles=["admin"]))
            response = client.post(
                "/api/v1/query",
                json={"natural_language": "How many customers are there?"},
                headers={"X-NL2SQL-Role": "admin"},
            )
    finally:
        reload_settings()

    assert isinstance(result, QueryResult)
    # The retry recovered: the second plan ran and returned the count, so the
    # sub-query and the run succeeded. The first plan's TABLE_NOT_FOUND stays
    # visible as a warning, not as an error.
    assert result.sub_queries[0].retry_count == 1
    assert result.sub_queries[0].rows.total_rows == 1
    assert result.sub_queries[0].status == "success"
    assert result.status == "success"
    assert result.errors == []
    assert any(w.get("error_code") == "TABLE_NOT_FOUND" for w in result.warnings)
    _assert_usage(result.usage.model_dump(mode="json"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sub_queries"][0]["retry_count"] == 1
    assert body["sub_queries"][0]["status"] == "success"
    assert body["status"] == "success"
    _assert_usage(body["usage"])
