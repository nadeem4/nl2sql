import inspect
import json
import logging
import pathlib
from datetime import datetime, timezone

from nl2sql import ClarificationChoice, ClarificationOption, ClarificationRequest, ExplainedStep, QueryResult, SubQueryResult
from nl2sql.execution.contracts import ArtifactRef


def _artifact_ref() -> ArtifactRef:
    return ArtifactRef(
        uri="file:///artifacts/sq-1.parquet",
        format="parquet",
        row_count=2,
        columns=["region", "revenue"],
        bytes=512,
        content_hash="abc123",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        path_template="{trace_id}/{node_id}",
    )


def _query_result() -> QueryResult:
    return QueryResult(
        sub_queries=[
            SubQueryResult(
                id="sq-1",
                intent="total revenue by region",
                sql="SELECT region, SUM(revenue) FROM sales GROUP BY region",
                datasource_id="warehouse",
                schema_version="v3",
            )
        ],
        final_answer={"summary": "Revenue by region", "format_type": "table", "content": "| region |"},
        trace_id="trace-123",
        reasoning=[{"node": "decomposer", "message": "split into 1 sub-query"}],
        warnings=[{"code": "RESULT_TRUNCATED", "node": "executor", "message": "row limit applied"}],
        artifact_refs={"sq-1": _artifact_ref()},
    )


def test_execute_query_returns_sql_and_final_answer(api_client):
    client, _ = api_client(_query_result())

    response = client.post("/api/v1/query", json={"question": "revenue by region"})

    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == "trace-123"
    assert body["final_answer"]["summary"] == "Revenue by region"
    assert len(body["sub_queries"]) == 1
    sub_query = body["sub_queries"][0]
    assert sub_query["id"] == "sq-1"
    assert sub_query["sql"] == "SELECT region, SUM(revenue) FROM sales GROUP BY region"
    assert sub_query["datasource_id"] == "warehouse"
    assert body["reasoning"][0]["node"] == "decomposer"
    assert body["warnings"][0]["node"] == "executor"


def test_execute_query_returns_artifact_refs_not_rows(api_client):
    client, _ = api_client(_query_result())

    body = client.post("/api/v1/query", json={"question": "revenue by region"}).json()

    assert "results" not in body
    assert body["artifact_refs"]["sq-1"]["uri"] == "file:///artifacts/sq-1.parquet"
    assert body["artifact_refs"]["sq-1"]["row_count"] == 2


def test_execute_query_forwards_request_options(api_client):
    client, engine = api_client(QueryResult())

    client.post(
        "/api/v1/query",
        json={
            "question": "revenue by region",
            "datasource_id": "warehouse",
            "execute": False,
        },
    )

    call = engine.calls[0]
    assert call["question"] == "revenue by region"
    assert call["datasource_id"] == "warehouse"
    assert call["execute"] is False
    # The role comes from the auth dependency (stubbed to admin), not the body.
    assert call["user_context"].roles == ["admin"]


def test_pipeline_errors_are_a_200_response(api_client):
    client, _ = api_client(
        QueryResult(
            trace_id="trace-err",
            errors=[{"node": "generator", "message": "no such column", "error_code": "SQL_GEN_FAILED", "severity": "ERROR"}],
        )
    )

    response = client.post("/api/v1/query", json={"question": "bad query"})

    assert response.status_code == 200
    body = response.json()
    assert body["errors"][0]["error_code"] == "SQL_GEN_FAILED"
    assert body["sub_queries"] == []


def test_unexpected_failure_is_a_500_without_internal_detail(api_client):
    client, _ = api_client(RuntimeError("connection string user=admin password=hunter2"))

    response = client.post("/api/v1/query", json={"question": "revenue by region"})

    assert response.status_code == 500
    assert "hunter2" not in response.text
    assert response.json()["error"]["request_id"]


def test_the_old_field_name_is_refused(api_client):
    client, engine = api_client(QueryResult())

    response = client.post("/api/v1/query", json={"natural_language": "revenue by region"})

    assert response.status_code == 422 and engine.calls == []


def test_invalid_payload_is_a_422(api_client):
    client, _ = api_client(QueryResult())

    assert client.post("/api/v1/query", json={}).status_code == 422


def test_query_route_is_not_a_coroutine():
    """Blocking pipeline work must not run on the event loop."""
    from nl2sql_api.routes import query as query_routes

    assert not inspect.iscoroutinefunction(query_routes.execute_query)


def _request(question="How many customers?"):
    step = ExplainedStep(datasource_id="chinook", source="generated", text="Counts customers.")
    return ClarificationRequest.build(
        question=question, phrase="customers", datasource_id="chinook", schema_version="v1", knowledge_digest="k1",
        sub_query={"id": "sq1", "intent": question},
        options=[ClarificationOption(id=i, label=label, plan={}, explanation=step)
                 for i, label in (("a", "Active customers"), ("b", "All customers"))])


def test_query_body_question_choice_and_approval_ids(api_client):
    client, engine = api_client(QueryResult())
    choice = ClarificationChoice(request=_request(), option_id="a")

    client.post("/api/v1/query", json={"question": "How many customers?", "choice": choice.model_dump(mode="json")})
    client.post("/api/v1/query", json={"question": "How many customers?", "approval_ids": ["r1", "r2"]})
    client.post("/api/v1/query", json={"question": "How many customers?"})

    with_choice, with_approvals, plain = engine.calls
    assert with_choice["choice"] == choice and with_choice["approvals"] is None
    assert with_approvals["approvals"] == ["r1", "r2"] and with_approvals["choice"] is None
    assert plain["choice"] is None and plain["approvals"] is None and plain["llm_override"] is None


def test_choice_with_approval_ids_is_422(api_client):
    client, engine = api_client(QueryResult())
    choice = ClarificationChoice(request=_request(), option_id="a")

    for path in ("/api/v1/query", "/api/v1/query/stream"):
        response = client.post(path, json={"question": "q", "choice": choice.model_dump(mode="json"),
                                           "approval_ids": ["r1"]})
        assert response.status_code == 422, path
        assert response.json()["error"]["code"] == "CLARIFICATION_INVALID"
    assert engine.calls == []


def test_an_unknown_body_field_is_422(api_client):
    client, engine = api_client(QueryResult())

    assert client.post("/api/v1/query", json={"question": "q", "user_context": {"roles": ["admin"]}}).status_code == 422
    assert engine.calls == []


KEY = "sk-" + "request-key-" + "x" * 24  # built at runtime: no key-shaped literal in the repository


def test_request_key_headers_refused_without_request_llm(api_client):
    client, engine = api_client(QueryResult())

    for path in ("/api/v1/query", "/api/v1/query/stream"):
        response = client.post(path, json={"question": "q"}, headers={"X-NL2SQL-Api-Key-openai": KEY})
        assert response.status_code == 403, path
        assert "api.request_llm" in response.json()["error"]["message"]
        assert KEY not in response.text
    assert engine.calls == []


def test_request_key_and_models_reach_the_engine_as_an_override(api_client):
    client, engine = api_client(QueryResult(), api={"request_llm": True})

    client.post("/api/v1/query", json={"question": "q"},
                headers={"X-NL2SQL-Api-Key-anthropic": KEY, "X-NL2SQL-Models": '{"ast_planner": "anthropic:claude-opus-5"}'})

    override = engine.calls[0]["llm_override"]
    assert override.keys == {"anthropic": KEY} and override.models == {"ast_planner": ("anthropic", "claude-opus-5")}


def test_model_override_without_request_key_is_422_even_when_server_has_key(api_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    client, engine = api_client(QueryResult(), api={"request_llm": True})

    for path in ("/api/v1/query", "/api/v1/query/stream"):
        response = client.post(path, json={"question": "q"},
                               headers={"X-NL2SQL-Models": '{"ast_planner": "anthropic:claude-opus-5"}'})
        assert response.status_code == 422, path
        error = response.json()["error"]
        assert error["code"] == "MISSING_PROVIDER_KEY" and "ast_planner" in error["message"]
        assert KEY not in response.text
    assert engine.calls == []


def test_request_key_never_in_a_response_or_the_server_log(api_client, caplog):
    caplog.set_level(logging.DEBUG)
    client, engine = api_client(RuntimeError(f"provider said {KEY} is invalid"), api={"request_llm": True})

    response = client.post("/api/v1/query", json={"question": "q"}, headers={"X-NL2SQL-Api-Key-openai": KEY})

    assert response.status_code == 500
    assert KEY not in response.text
    assert KEY not in repr(engine.calls[0]["llm_override"])
    # The unhandled error is logged with its traceback, whose message held the key: no logger may write it.
    assert "RuntimeError" in caplog.text and KEY not in caplog.text


def test_request_key_never_in_the_log_of_a_failed_stream(api_client, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    client, engine = api_client(None, api={"request_llm": True})

    def broken(*args, **kwargs):
        yield from ()
        raise RuntimeError(f"provider said {KEY} is invalid")

    engine.stream_query = broken
    response = client.post("/api/v1/query/stream", json={"question": "q"}, headers={"X-NL2SQL-Api-Key-openai": KEY})

    assert "run.failed" in response.text and KEY not in response.text
    assert "RuntimeError" in caplog.text and KEY not in caplog.text


def test_query_docs_name_no_removed_variable():
    from nl2sql_api.main import app
    from nl2sql_api.models import query as models
    from nl2sql_api.routes import approvals, query as routes

    removed = ("NL2SQL_API_ROLE_HEADER", "NL2SQL_API_ROLE", "NL2SQL_API_TRUST_BODY_ROLE")
    docs = pathlib.Path(__file__).resolve().parents[3] / "docs" / "api" / "rest"
    text = " ".join([inspect.getsource(models), inspect.getsource(routes), inspect.getsource(approvals),
                      json.dumps(app.openapi())]
                     + [path.read_text(encoding="utf-8") for path in docs.glob("*.md")])
    assert not [name for name in removed if name in text]
