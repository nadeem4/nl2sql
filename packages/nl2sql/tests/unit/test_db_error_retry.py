"""A query the database rejects is sent back to the planner, not reported as a crash.

A plan can pass the logical validator and still be refused by the database: an
unknown column the retrieved schema had wrong, a type mismatch, a function
called with the wrong arguments. The executor used to end the sub-query there.
Now an error the adapter marks ``retryable`` routes to the refiner, like a
validation error, while retries remain; a refusal (permission, timeout,
connection) still ends it. The planner and refiner are told about the latest
attempt only, as ``{node, error_code, message}``: never a stack trace, never
an earlier attempt's errors again.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.settings import settings
from nl2sql.execution.contracts import ExecutorRequest, ExecutorResponse
from nl2sql.execution.executor.sql_executor import SqlExecutorService
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.refiner.node import RefinerNode
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse
from nl2sql.pipeline.retry_feedback import latest_attempt
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph
from nl2sql_sdk.contracts import ResultError, ResultFrame


# --- the executor service keeps the adapter's verdict ----------------------

def _service(frame: ResultFrame) -> SqlExecutorService:
    adapter = SimpleNamespace(execute=lambda _request: frame)
    registry = SimpleNamespace(supports=lambda *_a: True, get_adapter=lambda _id: adapter)
    return SqlExecutorService(registry)


def _request() -> ExecutorRequest:
    return ExecutorRequest(node_id="sq1", trace_id="t", subgraph_name="sql_agent",
                           datasource_id="ds1", sql="SELECT 1", tenant_id="t1")


def _failed(retryable: bool) -> ResultFrame:
    return ResultFrame(success=False, error=ResultError(
        error_code="QUERY_FAILED", safe_message="The database rejected the query: no such column: t1.Nme",
        retryable=retryable))


def test_a_query_the_planner_can_fix_is_a_retryable_execution_failure():
    [error] = _service(_failed(retryable=True)).execute(_request()).errors

    assert error.error_code == ErrorCode.EXECUTION_FAILED
    assert error.severity == ErrorSeverity.ERROR
    assert error.is_retryable
    assert error.message == "The database rejected the query: no such column: t1.Nme"


def test_a_refusal_is_a_terminal_database_error():
    [error] = _service(_failed(retryable=False)).execute(_request()).errors

    assert error.error_code == ErrorCode.DB_EXECUTION_ERROR
    assert not error.is_retryable


# --- the subgraph routes it ------------------------------------------------

def _plan(column: str) -> PlanModel:
    return PlanModel(tables=[TableRef(name="Customer", alias="t1")],
                     select_items=[SelectItem(expr=Expr(kind="column", alias="t1", column_name=column))])


def _db_error(retryable: bool) -> PipelineError:
    return PipelineError(
        node="sql_executor", message="The database rejected the query: no such column: t1.Nme",
        severity=ErrorSeverity.ERROR,
        error_code=ErrorCode.EXECUTION_FAILED if retryable else ErrorCode.DB_EXECUTION_ERROR)


def _graph(monkeypatch, executor_errors):
    """A subgraph whose executor fails with ``executor_errors[i]`` on attempt i (none once they run out)."""
    calls = {"planner": 0, "executor": 0, "refiner": 0}
    monkeypatch.setattr(settings, "sql_agent_retry_base_delay_sec", 0)
    monkeypatch.setattr(settings, "sql_agent_retry_jitter_sec", 0)

    def planner(state):
        calls["planner"] += 1
        return {"ast_planner_response": ASTPlannerResponse(plan=_plan("Nme" if calls["planner"] == 1 else "FirstName"))}

    def executor(state):
        attempt = calls["executor"]
        calls["executor"] += 1
        errors = [executor_errors[attempt]] if attempt < len(executor_errors) else []
        response = ExecutorResponse(executor_name="sql_executor", subgraph_name="sql_agent", node_id="sq1",
                                    trace_id="t", tenant_id="t1", errors=errors)
        return {"executor_response": response, "errors": errors}

    def refiner(state):
        calls["refiner"] += 1
        return {}

    nodes = {
        "SchemaRetrieverNode": lambda s: {"relevant_tables": []},
        "ASTPlannerNode": planner,
        "LogicalValidatorNode": lambda s: {"logical_validator_response": LogicalValidatorResponse(errors=[])},
        "GeneratorNode": lambda s: {"generator_response": GeneratorResponse(sql_draft="SELECT 1")},
        "ExecutorNode": executor,
        "RefinerNode": refiner,
    }
    for name, node in nodes.items():
        monkeypatch.setattr(f"nl2sql.pipeline.subgraphs.sql_agent.{name}", lambda _ctx, node=node: node)
    graph = build_sql_agent_graph(SimpleNamespace())
    state = SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"))
    return graph.invoke(state), calls


def test_a_retryable_database_error_goes_back_through_the_refiner(monkeypatch):
    result, calls = _graph(monkeypatch, [_db_error(retryable=True)])

    assert calls == {"planner": 2, "executor": 2, "refiner": 1}
    assert result["retry_count"] == 1
    assert result["executor_response"].errors == []


def test_a_refusal_ends_the_sub_query(monkeypatch):
    result, calls = _graph(monkeypatch, [_db_error(retryable=False)])

    assert calls == {"planner": 1, "executor": 1, "refiner": 0}
    assert result["executor_response"].errors[0].error_code == ErrorCode.DB_EXECUTION_ERROR


def test_database_retries_stop_at_the_retry_budget(monkeypatch):
    monkeypatch.setattr(settings, "sql_agent_max_retries", 2)
    result, calls = _graph(monkeypatch, [_db_error(retryable=True)] * 10)

    assert calls["executor"] == 3
    assert result["retry_count"] == 2


# --- what the planner and refiner are told --------------------------------

def _err(node, code, message, **extra):
    return PipelineError(node=node, message=message, severity=ErrorSeverity.ERROR, error_code=code, **extra)


FIRST = _err("logical_validator", ErrorCode.COLUMN_NOT_FOUND, "Column t1.Nme not found.",
             stack_trace="Traceback (most recent call last): ...", details={"secret": "x"})
FEEDBACK_1 = PipelineError(node="refiner", message="Use FirstName.", severity=ErrorSeverity.WARNING,
                           error_code=ErrorCode.PLAN_FEEDBACK)
SECOND = _err("sql_executor", ErrorCode.EXECUTION_FAILED, "The database rejected the query: no such column: t1.Frist",
              stack_trace="Traceback ...")
FEEDBACK_2 = PipelineError(node="refiner", message="The column is FirstName.", severity=ErrorSeverity.WARNING,
                           error_code=ErrorCode.PLAN_FEEDBACK)


def test_latest_attempt_is_the_errors_since_the_previous_refinement():
    # The refiner sees the attempt it is refining; the planner, that attempt and the refiner's answer.
    assert latest_attempt([FIRST, FEEDBACK_1, SECOND]) == [SECOND]
    assert latest_attempt([FIRST, FEEDBACK_1, SECOND, FEEDBACK_2]) == [SECOND, FEEDBACK_2]
    assert latest_attempt([FIRST, FEEDBACK_1]) == [FIRST, FEEDBACK_1]
    assert latest_attempt([]) == []


def test_the_planner_is_told_only_the_latest_attempt_without_traces():
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = _plan("FirstName")

    node(SubgraphExecutionState(trace_id="t", retry_count=2,
                                sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
                                errors=[FIRST, FEEDBACK_1, SECOND, FEEDBACK_2]))

    feedback = node.chain.invoke.call_args.args[0]["feedback"]
    lines = [json.loads(line) for line in feedback.splitlines()]
    assert lines == [
        {"node": "sql_executor", "error_code": "EXECUTION_FAILED",
         "message": "The database rejected the query: no such column: t1.Frist"},
        {"node": "refiner", "error_code": "PLAN_FEEDBACK", "message": "The column is FirstName."},
    ]
    assert "Traceback" not in feedback and "secret" not in feedback and "Nme" not in feedback


def test_the_refiner_is_told_only_the_latest_attempt():
    captured = {}

    class _Chain:
        def invoke(self, inputs):
            captured.update(inputs)
            return "The column is FirstName."

    node = RefinerNode(SimpleNamespace(llm_registry=SimpleNamespace(get_llm=lambda _name: None)))
    node.chain = _Chain()

    node(SubgraphExecutionState(trace_id="t", errors=[FIRST, FEEDBACK_1, SECOND]))

    assert captured["errors"] == (
        "- [sql_executor] EXECUTION_FAILED: The database rejected the query: no such column: t1.Frist"
    )
