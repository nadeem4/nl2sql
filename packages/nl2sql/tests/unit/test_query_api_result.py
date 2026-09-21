"""QueryAPI must return a typed QueryResult built from the raw graph state."""

from datetime import datetime
from types import SimpleNamespace

from nl2sql.api import query_api
from nl2sql.api.query_api import QueryAPI, QueryResult, result_from_state
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.validator.schemas import ValidationCheck
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


def test_result_carries_plan_validation_rows_status_and_timings():
    plan = PlanModel(tables=[TableRef(name="t", alias="a", ordinal=0)], joins=[],
                     select_items=[SelectItem(ordinal=0, expr=Expr(kind="column", alias="a", column_name="id"))])
    artifact = ArtifactRef(uri="x", backend="local", format="parquet", row_count=120, columns=["id"], bytes=1,
                           content_hash="h", created_at=datetime.now(), path_template="p")
    state = {
        "trace_id": "t", "timings": {"ast_planner": 0.12},
        "subgraph_outputs": {"sql_agent:sq1:t": SubgraphOutput(
            subgraph_id="sql_agent:sq1:t", sql_draft="SELECT id FROM t", plan=plan, artifact=artifact, status="success", retry_count=1,
            validation=[ValidationCheck(name="policy", passed=True, message="ok")],
            sub_query=SubQuery(id="sq1", intent="i", datasource_id="ds", schema_version="v"))},
        "errors": [PipelineError(node="refiner", message="hint", severity=ErrorSeverity.WARNING, error_code=ErrorCode.PLAN_FEEDBACK)],
    }

    class _Store:
        def read_result_frame(self, ref):
            return SimpleNamespace(columns=["id"], rows=[[i] for i in range(120)], row_count=120)

    result = result_from_state(state, artifact_store=_Store(), sample_rows=50)
    sq = result.sub_queries[0]
    assert sq.plan["tables"][0]["name"] == "t"
    assert sq.validation[0].name == "policy" and sq.validation[0].passed
    assert sq.rows.total_rows == 120 and len(sq.rows.rows) == 50
    assert sq.status == "success" and sq.retry_count == 1
    assert result.status == "success"
    assert result.errors == [] and result.warnings[0]["message"] == "hint"
    assert result.timings == {"ast_planner": 0.12}


def test_plan_only_status_when_no_artifact_and_no_errors():
    state = {"trace_id": "t", "subgraph_outputs": {"sql_agent:sq1:t": SubgraphOutput(
        subgraph_id="sql_agent:sq1:t", sql_draft="SELECT 1", status="success",
        sub_query=SubQuery(id="sq1", intent="i", datasource_id="ds"))}}
    assert result_from_state(state).status == "plan_only"


def test_result_carries_per_node_and_per_question_usage():
    from nl2sql.services.callbacks.token_handler import QuestionUsage

    usage = {
        "total": {"calls": 2, "input_tokens": 12900, "cached_input_tokens": 7680, "output_tokens": 350,
                  "reasoning_tokens": 128, "total_tokens": 13250, "latency_s": 1.5},
        "nodes": {"ast_planner": {"calls": 1, "input_tokens": 11000, "cached_input_tokens": 7680,
                                  "output_tokens": 300, "reasoning_tokens": 128, "total_tokens": 11300,
                                  "latency_s": 1.2}},
        "calls": [],
    }
    result = result_from_state({"trace_id": "t", "usage": usage})
    assert isinstance(result.usage, QuestionUsage)
    assert result.usage.total.calls == 2
    assert result.usage.nodes["ast_planner"].cached_input_tokens == 7680
    assert result.usage.total.cost is None


def test_usage_defaults_to_empty_when_the_state_has_none():
    result = result_from_state({"trace_id": "t"})
    assert result.usage.total.calls == 0 and result.usage.nodes == {} and result.usage.calls == []
