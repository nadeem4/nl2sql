"""The HTTP response mirrors every field a UI renders off ``QueryResult``."""

from nl2sql.api.query_api import QueryResult, RowSample, SubQueryResult
from nl2sql.pipeline.nodes.validator.schemas import ValidationCheck
from nl2sql.services.callbacks.token_handler import LLMCallUsage, QuestionUsage, UsageTotals


def _stub_result() -> QueryResult:
    return QueryResult(
        sub_queries=[
            SubQueryResult(
                id="sq1",
                intent="count customers",
                sql="SELECT COUNT(*) FROM Customer",
                datasource_id="chinook",
                schema_version="v1",
                plan={"tables": [{"name": "Customer", "alias": "c", "ordinal": 0}]},
                validation=[
                    ValidationCheck(name="plan_present", passed=True, message="Plan received from the planner"),
                    ValidationCheck(name="structure_and_schema", passed=True, message="ok"),
                    ValidationCheck(name="policy", passed=True, message="Role 'admin' may read every table in the plan"),
                ],
                rows=RowSample(columns=["n"], rows=[[42]], total_rows=1),
                status="success",
                retry_count=1,
            )
        ],
        trace_id="trace-1",
        status="success",
        timings={"ast_planner": 0.12},
        usage=QuestionUsage(
            total=UsageTotals(calls=2, input_tokens=12900, cached_input_tokens=7680, output_tokens=350,
                              reasoning_tokens=128, total_tokens=13250, latency_s=1.5),
            nodes={"ast_planner": UsageTotals(calls=1, input_tokens=11000, cached_input_tokens=7680,
                                              output_tokens=300, reasoning_tokens=128, total_tokens=11300,
                                              latency_s=1.2)},
            calls=[LLMCallUsage(node="ast_planner", model="gpt-4o", input_tokens=11000,
                                cached_input_tokens=7680, output_tokens=300, reasoning_tokens=128,
                                total_tokens=11300, latency_s=1.2)],
        ),
    )


def test_query_response_carries_plan_validation_rows_status_and_timings(api_client):
    client, _engine = api_client(_stub_result())

    response = client.post("/api/v1/query", json={"natural_language": "how many customers?"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["timings"] == {"ast_planner": 0.12}
    sub_query = body["sub_queries"][0]
    assert sub_query["plan"]["tables"][0]["name"] == "Customer"
    assert [check["name"] for check in sub_query["validation"]] == [
        "plan_present",
        "structure_and_schema",
        "policy",
    ]
    assert sub_query["validation"][2]["passed"] is True
    assert sub_query["rows"] == {"columns": ["n"], "rows": [[42]], "total_rows": 1}
    assert sub_query["status"] == "success"
    assert sub_query["retry_count"] == 1
    # The usage block is mirrored field for field.
    assert body["usage"] == _stub_result().usage.model_dump(mode="json")
    assert body["usage"]["nodes"]["ast_planner"]["cached_input_tokens"] == 7680


def test_plan_only_run_reports_no_rows(api_client):
    client, _engine = api_client(
        QueryResult(
            sub_queries=[SubQueryResult(id="sq1", sql="SELECT 1", status="success")],
            status="plan_only",
        )
    )

    response = client.post(
        "/api/v1/query", json={"natural_language": "how many customers?", "execute": False}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "plan_only"
    assert body["sub_queries"][0]["rows"] is None


def test_the_response_says_where_the_run_trace_was_written(api_client):
    client, _engine = api_client(QueryResult(status="error", trace_id="t-1",
                                             trace_path="traces/20260921T101112000000Z_t-1.json"))
    body = client.post("/api/v1/query", json={"natural_language": "q"}).json()
    assert body["trace_id"] == "t-1"
    assert body["trace_path"] == "traces/20260921T101112000000Z_t-1.json"


def test_no_trace_path_when_no_trace_was_written(api_client):
    client, _engine = api_client(QueryResult(status="success"))
    assert client.post("/api/v1/query", json={"natural_language": "q"}).json()["trace_path"] is None
