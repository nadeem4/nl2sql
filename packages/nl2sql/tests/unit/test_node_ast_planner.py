from types import SimpleNamespace
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery, ExpectedColumn
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.schema_retriever.schema import Table, Column
from nl2sql.common.errors import ErrorCode


def test_ast_planner_successful_plan():
    # Validates successful planning because execution depends on deterministic plans.
    # Arrange
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = PlanModel(
        query_type="READ",
        tables=[],
        select_items=[],
        joins=[],
        reasoning="ok",
    )

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(
            id="sq1",
            datasource_id="ds1",
            intent="list users",
            expected_schema=[ExpectedColumn(name="id", dtype="int")],
        ),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
    )

    # Act
    result = node(state)

    # Assert
    assert result["ast_planner_response"].plan is not None
    # No errors key: ``errors`` is an additive reducer, so returning [] would
    # clear nothing. Earlier attempts' errors stay as retry feedback.
    assert "errors" not in result


def test_ast_planner_failure_returns_error():
    # Validates failure behavior because planning errors must be surfaced.
    # Arrange
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.side_effect = RuntimeError("LLM down")

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
    )

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.PLANNING_FAILURE


def test_ast_planner_none_plan_is_a_planning_failure():
    # A structured-output call can return None (the model declined to call the
    # tool). That must be a PLANNING_FAILURE in its own right, not an
    # AttributeError that happens to land in the except.
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = None

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
    )

    result = node(state)

    assert result["ast_planner_response"].plan is None
    [error] = result["errors"]
    assert error.error_code == ErrorCode.PLANNING_FAILURE
    assert error.message == "Planner returned no plan."
    assert error.stack_trace is None
