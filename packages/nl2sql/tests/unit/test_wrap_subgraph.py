from datetime import datetime
from types import SimpleNamespace

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.execution.contracts import ArtifactRef, ExecutorResponse
from nl2sql.pipeline.graph_utils import wrap_subgraph
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery


class _Sub:
    def __init__(self, returned):
        self.returned = returned

    def invoke(self, state, config=None):
        return {**state, **self.returned}


def _state():
    return {
        "trace_id": "t1",
        "subgraph_id": "sql_agent:sq1:t1",
        "user_context": None,
        "decomposer_response": DecomposerResponse(
            sub_queries=[SubQuery(id="sq1", datasource_id="ds", intent="q")],
            combine_groups=[],
        ),
    }


def test_exhausted_validation_returns_structured_error_not_crash():
    # A sub-query whose validation is exhausted must surface a structured error
    # rather than crashing the whole run on a missing executor response.
    err = PipelineError(
        node="logical_validator",
        message="denied",
        severity=ErrorSeverity.CRITICAL,
        error_code=ErrorCode.SECURITY_VIOLATION,
    )
    wrapped = wrap_subgraph(
        _Sub({"executor_response": None, "ast_planner_response": None, "errors": [err]}),
        "sql_agent",
        SimpleNamespace(),
    )

    out = wrapped(_state())

    assert out["artifact_refs"] == {}
    output = out["subgraph_outputs"]["sql_agent:sq1:t1"]
    assert output.status == "error"
    assert output.errors[0].error_code == ErrorCode.SECURITY_VIOLATION


def _warning():
    return PipelineError(
        node="logical_validator",
        message="Column 'CustomerIdd' not found",
        severity=ErrorSeverity.WARNING,
        error_code=ErrorCode.COLUMN_NOT_FOUND,
    )


def _table_not_found():
    return PipelineError(
        node="logical_validator",
        message="Table 'Customers' not found",
        severity=ErrorSeverity.ERROR,
        error_code=ErrorCode.TABLE_NOT_FOUND,
    )


def _artifact():
    return ArtifactRef(uri="x", backend="local", format="parquet", row_count=1, columns=["n"], bytes=1,
                       content_hash="h", created_at=datetime.now(), path_template="p")


def _executed(artifact):
    return ExecutorResponse(executor_name="sqlite", subgraph_name="sql_agent", node_id="sq1",
                            trace_id="t1", tenant_id="default_tenant", artifact=artifact)


def test_warnings_alone_without_sql_report_error():
    # Ending without SQL is a failure whatever the severities say: a sub-query
    # whose only findings were warnings used to be reported "success".
    wrapped = wrap_subgraph(
        _Sub({"executor_response": None, "generator_response": None, "errors": [_warning()]}),
        "sql_agent",
        SimpleNamespace(),
    )

    out = wrapped(_state())

    output = out["subgraph_outputs"]["sql_agent:sq1:t1"]
    assert output.status == "error"
    assert output.sql_draft is None
    # The run carries a blocking error that says why, so the question fails too.
    assert any(e.severity == ErrorSeverity.ERROR and e.error_code == ErrorCode.MISSING_SQL
               for e in out["errors"])


def test_a_retry_that_recovers_reports_success_and_keeps_the_history():
    # Attempt 1 failed validation; attempt 2 produced SQL and rows. The first
    # attempt's error must not decide the status, but it must stay visible.
    wrapped = wrap_subgraph(
        _Sub({
            "retry_count": 1,
            "generator_response": GeneratorResponse(sql_draft="SELECT COUNT(*) FROM Customer"),
            "executor_response": _executed(_artifact()),
            "errors": [_table_not_found()],
        }),
        "sql_agent",
        SimpleNamespace(),
    )

    out = wrapped(_state())

    output = out["subgraph_outputs"]["sql_agent:sq1:t1"]
    assert output.status == "success"
    assert [e.error_code for e in output.errors] == [ErrorCode.TABLE_NOT_FOUND]
    assert out["errors"] == []
    [superseded] = out["warnings"]
    assert superseded["error_code"] == "TABLE_NOT_FOUND"
    assert superseded["severity"] == "ERROR"
    assert superseded["sub_query_id"] == "sq1"


def test_sql_that_did_not_execute_reports_error():
    wrapped = wrap_subgraph(
        _Sub({
            "generator_response": GeneratorResponse(sql_draft="SELECT 1"),
            "executor_response": None,
            "errors": [PipelineError(node="executor", message="no such column", severity=ErrorSeverity.ERROR,
                                     error_code=ErrorCode.DB_EXECUTION_ERROR)],
        }),
        "sql_agent",
        SimpleNamespace(),
    )

    assert wrapped(_state())["subgraph_outputs"]["sql_agent:sq1:t1"].status == "error"


def test_plan_only_sql_reports_success():
    # With execute=False there is no executor: SQL is the whole outcome.
    wrapped = wrap_subgraph(
        _Sub({"generator_response": GeneratorResponse(sql_draft="SELECT 1"), "executor_response": None,
              "errors": [_warning()]}),
        "sql_agent",
        SimpleNamespace(),
        execute=False,
    )

    out = wrapped(_state())

    assert out["subgraph_outputs"]["sql_agent:sq1:t1"].status == "success"
    assert out["errors"] == []


def test_the_question_status_follows_the_sub_query():
    from nl2sql.api.query_api import result_from_state

    def run(returned):
        out = wrap_subgraph(_Sub(returned), "sql_agent", SimpleNamespace())(_state())
        return result_from_state({**out, "trace_id": "t1"})

    failed = run({"executor_response": None, "generator_response": None, "errors": [_warning()]})
    assert failed.sub_queries[0].status == "error"
    assert failed.status == "error"

    recovered = run({"retry_count": 1, "generator_response": GeneratorResponse(sql_draft="SELECT 1"),
                     "executor_response": _executed(_artifact()), "errors": [_table_not_found()]})
    assert recovered.sub_queries[0].status == "success"
    assert recovered.status == "success"
    assert recovered.errors == []
    assert [w["error_code"] for w in recovered.warnings] == ["TABLE_NOT_FOUND"]
