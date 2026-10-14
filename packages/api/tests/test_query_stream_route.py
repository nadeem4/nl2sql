"""``POST /api/v1/query/stream``: the run as Server-Sent Events.

Same request, same auth as ``POST /api/v1/query``; the response is
``text/event-stream``, one event per node, answer deltas, and a ``final``
event whose data is what ``/query`` returns.
"""
import json

from nl2sql import QueryResult, SubQueryResult


def _query_result():
    return QueryResult(
        sub_queries=[SubQueryResult(id="sq-1", intent="total revenue by region",
                                    sql="SELECT region, SUM(revenue) FROM sales GROUP BY region")],
        final_answer={"summary": "Revenue by region", "format_type": "table", "content": "| region |"},
        trace_id="trace-123",
        status="success",
    )


def parse_sse(text):
    """``[(event, data)]`` from a ``text/event-stream`` body."""
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "data" in fields:
            events.append((fields.get("event", "message"), json.loads(fields["data"])))
    return events


def test_the_stream_is_server_sent_events_ending_in_the_query_response(api_client):
    client, engine = api_client(_query_result())

    response = client.post("/api/v1/query/stream", json={"natural_language": "revenue by region",
                                                          "datasource_id": "warehouse", "execute": False})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["node_started", "node_finished", "answer_delta", "final"]
    final = events[-1][1]
    assert final == client.post("/api/v1/query", json={"natural_language": "revenue by region"}).json()
    assert final["final_answer"]["summary"] == "Revenue by region"
    call = engine.calls[0]
    assert call["stream"] is True and call["datasource_id"] == "warehouse" and call["execute"] is False
    assert call["user_context"].roles == ["admin"]


def test_the_stream_needs_a_role_like_the_query_route(api_client):
    client, engine = api_client(QueryResult(status="success"), authenticated=False)

    response = client.post("/api/v1/query/stream", json={"natural_language": "q"})

    assert response.status_code == 401
    assert engine.calls == []


def test_a_run_that_breaks_mid_stream_ends_in_an_error_event(api_client):
    client, _ = api_client(RuntimeError("boom"))

    response = client.post("/api/v1/query/stream", json={"natural_language": "q"})

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events == [("error", {"message": "Failed to execute query.", "error_code": "UNKNOWN_ERROR"})]


def test_the_route_is_documented():
    from nl2sql_api.main import app

    operation = app.openapi()["paths"]["/api/v1/query/stream"]["post"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]
