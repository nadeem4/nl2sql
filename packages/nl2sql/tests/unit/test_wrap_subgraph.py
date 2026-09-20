from types import SimpleNamespace

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.pipeline.graph_utils import wrap_subgraph
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


def test_warning_only_errors_report_success():
    # The refiner's WARNING feedback is not a failure; a retried-then-succeeded
    # sub-query must not report an error status.
    warn = PipelineError(
        node="refiner",
        message="try again",
        severity=ErrorSeverity.WARNING,
        error_code=ErrorCode.PLAN_FEEDBACK,
    )
    wrapped = wrap_subgraph(
        _Sub({"executor_response": None, "ast_planner_response": None, "errors": [warn]}),
        "sql_agent",
        SimpleNamespace(),
    )

    out = wrapped(_state())

    assert out["subgraph_outputs"]["sql_agent:sq1:t1"].status == "success"
