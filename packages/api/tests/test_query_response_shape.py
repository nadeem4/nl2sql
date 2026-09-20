"""The HTTP response mirrors every field a UI renders off ``QueryResult``."""

from nl2sql.api.query_api import QueryResult, RowSample, SubQueryResult
from nl2sql.pipeline.nodes.validator.schemas import ValidationCheck


def _stub_result() -> QueryResult:
    return QueryResult(
        sub_queries=[
            SubQueryResult(
                id="sq1",
                intent="count employees",
                sql="SELECT COUNT(*) FROM employees",
                datasource_id="manufacturing_ops",
                schema_version="v1",
                plan={"tables": [{"name": "employees", "alias": "e", "ordinal": 0}]},
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
    )


def test_query_response_carries_plan_validation_rows_status_and_timings(api_client):
    client, _engine = api_client(_stub_result())

    response = client.post("/api/v1/query", json={"natural_language": "how many employees?"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["timings"] == {"ast_planner": 0.12}
    sub_query = body["sub_queries"][0]
    assert sub_query["plan"]["tables"][0]["name"] == "employees"
    assert [check["name"] for check in sub_query["validation"]] == [
        "plan_present",
        "structure_and_schema",
        "policy",
    ]
    assert sub_query["validation"][2]["passed"] is True
    assert sub_query["rows"] == {"columns": ["n"], "rows": [[42]], "total_rows": 1}
    assert sub_query["status"] == "success"
    assert sub_query["retry_count"] == 1


def test_plan_only_run_reports_no_rows(api_client):
    client, _engine = api_client(
        QueryResult(
            sub_queries=[SubQueryResult(id="sq1", sql="SELECT 1", status="success")],
            status="plan_only",
        )
    )

    response = client.post(
        "/api/v1/query", json={"natural_language": "how many employees?", "execute": False}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "plan_only"
    assert body["sub_queries"][0]["rows"] is None
