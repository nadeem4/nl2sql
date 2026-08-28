"""QueryAPI must return a typed QueryResult built from the raw graph state."""

from nl2sql.api import query_api
from nl2sql.api.query_api import QueryAPI, QueryResult, result_from_state
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput


def _dict_state():
    """Graph state where LangGraph handed back plain dicts."""
    return {
        "trace_id": "trace-1",
        "subgraph_outputs": {
            "sg-1": {
                "subgraph_id": "sg-1",
                "sql_draft": "SELECT 1",
                "sub_query": {
                    "id": "sq-1",
                    "intent": "count rows",
                    "datasource_id": "warehouse",
                    "schema_version": "v2",
                },
            }
        },
        "answer_synthesizer_response": {"final_answer": {"summary": "one row"}},
        "reasoning": [{"node": "decomposer"}],
        "warnings": [{"node": "executor"}],
        "errors": [
            {
                "node": "generator",
                "message": "boom",
                "error_code": "SQL_GEN_FAILED",
                "severity": "ERROR",
                "stack_trace": "Traceback (most recent call last): ...",
            }
        ],
    }


def _object_state():
    """The same state expressed with model instances."""
    return {
        "trace_id": "trace-1",
        "subgraph_outputs": {
            "sg-1": SubgraphOutput(
                subgraph_id="sg-1",
                sql_draft="SELECT 1",
                sub_query=SubQuery(
                    id="sq-1",
                    intent="count rows",
                    datasource_id="warehouse",
                    schema_version="v2",
                ),
            )
        },
        "answer_synthesizer_response": AnswerSynthesizerResponse(
            final_answer={"summary": "one row"}
        ),
        "reasoning": [{"node": "decomposer"}],
        "warnings": [{"node": "executor"}],
        "errors": [
            PipelineError(
                node="generator",
                message="boom",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.SQL_GEN_FAILED,
                stack_trace="Traceback (most recent call last): ...",
            )
        ],
    }


def _assert_mapped(result: QueryResult):
    assert result.trace_id == "trace-1"
    assert len(result.sub_queries) == 1
    sub_query = result.sub_queries[0]
    assert sub_query.id == "sq-1"
    assert sub_query.sql == "SELECT 1"
    assert sub_query.intent == "count rows"
    assert sub_query.datasource_id == "warehouse"
    assert sub_query.schema_version == "v2"
    assert result.final_answer == {"summary": "one row"}
    assert result.reasoning == [{"node": "decomposer"}]
    assert result.warnings == [{"node": "executor"}]
    assert result.errors == [
        {
            "node": "generator",
            "message": "boom",
            "error_code": "SQL_GEN_FAILED",
            "severity": "ERROR",
        }
    ]


def test_result_from_dict_state():
    _assert_mapped(result_from_state(_dict_state()))


def test_result_from_object_state():
    _assert_mapped(result_from_state(_object_state()))


def test_errors_omit_stack_traces():
    result = result_from_state(_dict_state())
    assert "stack_trace" not in result.errors[0]


def test_empty_state_yields_empty_result():
    result = result_from_state({})
    assert result == QueryResult()


def test_run_query_returns_query_result(monkeypatch):
    monkeypatch.setattr(query_api, "run_with_graph", lambda *args, **kwargs: _dict_state())

    result = QueryAPI(ctx=None).run_query("count the rows")

    assert isinstance(result, QueryResult)
    _assert_mapped(result)
