"""``PlanModel.offset`` reaches the SQL, on every dialect, under the row cap.

The planner could say "rows 11 to 20" (``offset: 10, limit: 10``) but the
generator never rendered ``offset``, so the query returned rows 1 to 10 and the
answer named the wrong customers with no error at all.

An offset pages through an order, so a plan with an offset and no ``order_by``
is malformed on every database (T-SQL cannot even express it without one). The
generator refuses to build it and the validator, which runs the generator's
builder, rejects it as a retryable ``INVALID_PLAN_STRUCTURE``.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from nl2sql.policies import UserContext
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    OrderItem,
    PlanModel,
    SelectItem,
    TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.testing.runtime import fake_runtime


def _col(name: str) -> Expr:
    return Expr(kind="column", alias="u", column_name=name)


def _plan(**kwargs) -> PlanModel:
    fields = dict(
        tables=[TableRef(name="users", alias="u")],
        select_items=[SelectItem(expr=_col("id"), alias="id")],
        order_by=[OrderItem(expr=_col("id"), direction="asc")],
    )
    fields.update(kwargs)
    return PlanModel(**fields)


def _ctx(dialect: str = "sqlite", row_limit: int = 1000):
    adapter = SimpleNamespace(row_limit=row_limit, max_bytes=1000, get_dialect=lambda: dialect)
    return fake_runtime(
        ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter),
        policies={"allow_all": True},
    )


def _state(plan: PlanModel) -> SubgraphExecutionState:
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )


def _generate(plan: PlanModel, dialect: str = "sqlite", row_limit: int = 1000):
    return GeneratorNode(_ctx(dialect, row_limit))(_state(plan))


def _rows(sql: str):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE users (id INTEGER)")
    con.executemany("INSERT INTO users VALUES (?)", [(i,) for i in range(1, 31)])
    return [r[0] for r in con.execute(sql)]


def test_rows_11_to_20_are_rows_11_to_20():
    response = _generate(_plan(limit=10, offset=10))["generator_response"]

    assert _rows(response.sql_draft) == list(range(11, 21))


@pytest.mark.parametrize(
    "dialect, fragment",
    [
        ("sqlite", "LIMIT 10 OFFSET 10"),
        ("postgres", "LIMIT 10 OFFSET 10"),
        ("mysql", "LIMIT 10 OFFSET 10"),
        ("duckdb", "LIMIT 10 OFFSET 10"),
        ("tsql", "OFFSET 10 ROWS FETCH FIRST 10 ROWS ONLY"),
    ],
)
def test_every_dialect_renders_the_offset(dialect, fragment):
    sql = _generate(_plan(limit=10, offset=10), dialect)["generator_response"].sql_draft

    assert fragment in sql.upper()
    assert "ORDER BY" in sql.upper()


def test_the_row_cap_still_bounds_a_page_and_says_so():
    # Rows 11 onwards with no limit of the plan's own: the cap (5) bounds the
    # page, and the generator reports it as truncation, as it does without an offset.
    response = _generate(_plan(offset=10), row_limit=5)["generator_response"]

    assert _rows(response.sql_draft) == [11, 12, 13, 14, 15]
    assert response.row_cap == 5


def test_a_page_inside_the_cap_is_not_a_cap():
    response = _generate(_plan(limit=10, offset=10), row_limit=1000)["generator_response"]

    assert response.row_cap is None


def test_offset_zero_is_no_offset():
    sql = _generate(_plan(limit=10, offset=0))["generator_response"].sql_draft

    assert "OFFSET" not in sql.upper()


@pytest.mark.parametrize(
    "plan",
    [
        _plan(limit=10, offset=10, order_by=[]),
        _plan(limit=10, offset=-1),
    ],
    ids=["offset without order_by", "negative offset"],
)
def test_the_generator_refuses_and_the_validator_rejects_it_first(plan):
    assert [e.error_code for e in _generate(plan)["errors"]] == [ErrorCode.SQL_GEN_FAILED]

    errors = LogicalValidatorNode(_ctx())(_state(plan))["errors"]
    blocking = [e for e in errors if e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL)]
    assert blocking, "the validator passed a plan the generator cannot build"
    assert all(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE and e.is_retryable for e in blocking)
    assert any("offset" in e.message.lower() for e in blocking)
