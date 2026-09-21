from types import SimpleNamespace

import pytest

from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    PlanModel,
    TableRef,
    JoinSpec,
    SelectItem,
    Expr,
    GroupByItem,
    OrderItem,
    ASTPlannerResponse,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import SubgraphExecutionState


def _col(alias: str, name: str):
    return Expr(kind="column", alias=alias, column_name=name)


def test_generator_orders_select_items_by_ordinal():
    # Validates deterministic ordering because column order affects clients.
    # Arrange
    adapter = SimpleNamespace(row_limit=5, max_bytes=1000, get_dialect=lambda: "sqlite")
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    node = GeneratorNode(ctx)

    plan = PlanModel(
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[
            SelectItem(expr=_col("u", "name"), alias="name_second", ordinal=1),
            SelectItem(expr=_col("u", "id"), alias="id_first", ordinal=0),
        ],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
    )

    # Act
    sql = node(state)["generator_response"].sql_draft.lower()

    # Assert
    assert sql.find("id_first") < sql.find("name_second")


def test_generator_applies_limit_clamp():
    # Validates limit clamping because adapters enforce row limits.
    # Arrange
    adapter = SimpleNamespace(row_limit=5, max_bytes=1000, get_dialect=lambda: "sqlite")
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    node = GeneratorNode(ctx)

    plan = PlanModel(
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=0)],
        joins=[],
        limit=100,
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
    )

    # Act
    sql = node(state)["generator_response"].sql_draft.lower()

    # Assert
    assert "limit 5" in sql


def test_generator_raises_on_unknown_join_alias():
    # Validates join alias enforcement because invalid aliases must fail fast.
    # Arrange
    adapter = SimpleNamespace(row_limit=5, max_bytes=1000, get_dialect=lambda: "sqlite")
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    node = GeneratorNode(ctx)

    plan = PlanModel(
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=0)],
        joins=[
            JoinSpec(
                left_alias="u",
                right_alias="o",
                join_type="inner",
                ordinal=0,
                condition=Expr(kind="binary", op="=", left=_col("u", "id"), right=_col("o", "user_id")),
            )
        ],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
    )

    # Act
    result = node(state)

    # Assert
    assert result["errors"]


def test_generator_requires_datasource_id():
    # Validates datasource requirement because SQL generation depends on adapter dialect.
    adapter = SimpleNamespace(row_limit=5, max_bytes=1000, get_dialect=lambda: "sqlite")
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    node = GeneratorNode(ctx)

    plan = PlanModel(
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
    )
    state.sub_query.datasource_id = None

    result = node(state)

    assert result["errors"]


def test_generator_requires_plan():
    # Validates missing plan handling because generator must fail closed.
    adapter = SimpleNamespace(row_limit=5, max_bytes=1000, get_dialect=lambda: "sqlite")
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    node = GeneratorNode(ctx)

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
    )

    result = node(state)

    assert result["errors"]


def test_generator_emits_the_requested_sort_direction():
    """``ORDER BY ... DESC`` must survive into the SQL.

    ``Select.order_by(expr, desc=True)`` only ever applied ``desc`` while
    parsing a *string*; an ``Expression`` argument is used as-is and the keyword
    is dropped, so every "top N by <aggregate>" answer came back ascending.
    """
    adapter = SimpleNamespace(row_limit=5, max_bytes=1000, get_dialect=lambda: "sqlite")
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    node = GeneratorNode(ctx)

    count_id = Expr(
        kind="func",
        func_name="COUNT",
        is_aggregate=True,
        args=[_col("u", "id")],
    )
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[
            SelectItem(expr=_col("u", "name"), alias="name", ordinal=0),
            SelectItem(expr=count_id, alias="n", ordinal=1),
        ],
        joins=[],
        group_by=[GroupByItem(ordinal=0, expr=_col("u", "name"))],
        order_by=[OrderItem(ordinal=0, direction="desc", expr=count_id)],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
    )

    sql = node(state)["generator_response"].sql_draft.upper()

    assert "ORDER BY COUNT(U.ID) DESC" in sql
