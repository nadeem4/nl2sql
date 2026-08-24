from datetime import datetime, timezone

from nl2sql.api.query_api import QueryResult, SubQueryResult
from nl2sql.execution.contracts import ArtifactRef


def _artifact_ref() -> ArtifactRef:
    return ArtifactRef(
        uri="file:///artifacts/sq-1.parquet",
        backend="local",
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
        warnings=[{"node": "executor", "message": "row limit applied"}],
        artifact_refs={"sq-1": _artifact_ref()},
    )


def test_execute_query_returns_sql_and_final_answer(api_client):
    client, _ = api_client(_query_result())

    response = client.post("/api/v1/query", json={"natural_language": "revenue by region"})

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

    body = client.post("/api/v1/query", json={"natural_language": "revenue by region"}).json()

    assert "results" not in body
    assert body["artifact_refs"]["sq-1"]["uri"] == "file:///artifacts/sq-1.parquet"
    assert body["artifact_refs"]["sq-1"]["row_count"] == 2


def test_execute_query_forwards_request_options(api_client):
    client, engine = api_client(QueryResult())

    client.post(
        "/api/v1/query",
        json={
            "natural_language": "revenue by region",
            "datasource_id": "warehouse",
            "execute": False,
            "user_context": {"user_id": "u-1"},
        },
    )

    call = engine.calls[0]
    assert call["natural_language"] == "revenue by region"
    assert call["datasource_id"] == "warehouse"
    assert call["execute"] is False
    assert call["user_context"].user_id == "u-1"


def test_pipeline_errors_are_a_200_response(api_client):
    client, _ = api_client(
        QueryResult(
            trace_id="trace-err",
            errors=[{"node": "generator", "message": "no such column", "error_code": "SQL_GEN_FAILED", "severity": "ERROR"}],
        )
    )

    response = client.post("/api/v1/query", json={"natural_language": "bad query"})

    assert response.status_code == 200
    body = response.json()
    assert body["errors"][0]["error_code"] == "SQL_GEN_FAILED"
    assert body["sub_queries"] == []


def test_unexpected_failure_is_a_500_without_internal_detail(api_client):
    client, _ = api_client(RuntimeError("connection string user=admin password=hunter2"))

    response = client.post("/api/v1/query", json={"natural_language": "revenue by region"})

    assert response.status_code == 500
    assert "hunter2" not in response.json()["detail"]


def test_invalid_payload_is_a_422(api_client):
    client, _ = api_client(QueryResult())

    assert client.post("/api/v1/query", json={}).status_code == 422


def test_query_route_is_not_a_coroutine():
    """Blocking pipeline work must not run on the event loop."""
    import inspect

    from nl2sql_api.routes import query as query_routes

    assert not inspect.iscoroutinefunction(query_routes.execute_query)
