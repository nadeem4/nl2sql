from types import SimpleNamespace

import pytest

from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, PlanModel, TableRef, SelectItem, Expr
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse, PhysicalValidatorResponse
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode


def _plan_ok():
    return PlanModel(
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=Expr(kind="column", alias="u", column_name="id"), ordinal=0)],
        joins=[],
    )


def test_sql_agent_happy_path(monkeypatch):
    # Validates basic routing because all nodes should execute once.
    def schema_retriever(state):
        return {"relevant_tables": []}

    def planner(state):
        return {"ast_planner_response": ASTPlannerResponse(plan=_plan_ok()), "errors": []}

    def logical(state):
        return {"logical_validator_response": LogicalValidatorResponse(errors=[]), "errors": []}

    def generator(state):
        return {"generator_response": GeneratorResponse(sql_draft="SELECT 1")}

    def physical(state):
        return {"physical_validator_response": PhysicalValidatorResponse(errors=[]), "errors": []}

    def executor(state):
        return {"executor_response": SimpleNamespace(errors=[], reasoning=[]), "errors": []}

    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.SchemaRetrieverNode", lambda _ctx: schema_retriever)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ASTPlannerNode", lambda _ctx: planner)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.LogicalValidatorNode", lambda _ctx: logical)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.GeneratorNode", lambda _ctx: generator)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.PhysicalValidatorNode", lambda _ctx: physical)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ExecutorNode", lambda _ctx: executor)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.RefinerNode", lambda _ctx: (lambda _s: {}))

    ctx = SimpleNamespace()
    graph = build_sql_agent_graph(ctx)

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
    )

    result = graph.invoke(state)

    assert result["executor_response"] is not None


def test_sql_agent_planner_retry(monkeypatch):
    # Validates retry loop because planner errors should trigger refiner.
    call_count = {"planner": 0}

    def schema_retriever(state):
        return {"relevant_tables": []}

    def planner(state):
        call_count["planner"] += 1
        if call_count["planner"] == 1:
            return {"ast_planner_response": ASTPlannerResponse(plan=None), "errors": [PipelineError(node="astplanner", message="fail", severity=ErrorSeverity.ERROR, error_code=ErrorCode.PLANNING_FAILURE)]}
        return {"ast_planner_response": ASTPlannerResponse(plan=_plan_ok()), "errors": []}

    def logical(state):
        return {"logical_validator_response": LogicalValidatorResponse(errors=[]), "errors": []}

    def generator(state):
        return {"generator_response": GeneratorResponse(sql_draft="SELECT 1")}

    def physical(state):
        return {"physical_validator_response": PhysicalValidatorResponse(errors=[]), "errors": []}

    def executor(state):
        return {"executor_response": SimpleNamespace(errors=[], reasoning=[]), "errors": []}

    def refiner(state):
        return {"reasoning": [{"node": "refiner", "content": "retry"}]}

    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.SchemaRetrieverNode", lambda _ctx: schema_retriever)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ASTPlannerNode", lambda _ctx: planner)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.LogicalValidatorNode", lambda _ctx: logical)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.GeneratorNode", lambda _ctx: generator)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.PhysicalValidatorNode", lambda _ctx: physical)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ExecutorNode", lambda _ctx: executor)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.RefinerNode", lambda _ctx: refiner)

    ctx = SimpleNamespace()
    graph = build_sql_agent_graph(ctx)

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
    )

    result = graph.invoke(state)

    assert call_count["planner"] >= 2
    assert result["executor_response"] is not None


def test_sql_agent_physical_retry(monkeypatch):
    # Validates physical retry because validation errors should trigger refiner.
    call_count = {"physical": 0}

    def schema_retriever(state):
        return {"relevant_tables": []}

    def planner(state):
        return {"ast_planner_response": ASTPlannerResponse(plan=_plan_ok()), "errors": []}

    def logical(state):
        return {"logical_validator_response": LogicalValidatorResponse(errors=[]), "errors": []}

    def generator(state):
        return {"generator_response": GeneratorResponse(sql_draft="SELECT 1")}

    def physical(state):
        call_count["physical"] += 1
        if call_count["physical"] == 1:
            return {
                "physical_validator_response": PhysicalValidatorResponse(
                    errors=[
                        PipelineError(
                            node="physical_validator",
                            message="retryable",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.EXECUTION_ERROR,
                            is_retryable=True,
                        )
                    ]
                ),
                "errors": [
                    PipelineError(
                        node="physical_validator",
                        message="retryable",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.EXECUTION_ERROR,
                        is_retryable=True,
                    )
                ],
            }
        return {"physical_validator_response": PhysicalValidatorResponse(errors=[]), "errors": []}

    def executor(state):
        return {"executor_response": SimpleNamespace(errors=[], reasoning=[]), "errors": []}

    def refiner(state):
        return {"reasoning": [{"node": "refiner", "content": "retry"}]}

    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.SchemaRetrieverNode", lambda _ctx: schema_retriever)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ASTPlannerNode", lambda _ctx: planner)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.LogicalValidatorNode", lambda _ctx: logical)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.GeneratorNode", lambda _ctx: generator)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.PhysicalValidatorNode", lambda _ctx: physical)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ExecutorNode", lambda _ctx: executor)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.RefinerNode", lambda _ctx: refiner)

    ctx = SimpleNamespace()
    graph = build_sql_agent_graph(ctx)

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
    )

    result = graph.invoke(state)

    assert call_count["physical"] >= 2
    assert result["executor_response"] is not None
