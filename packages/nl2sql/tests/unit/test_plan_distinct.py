"""DISTINCT in the plan language: ``COUNT(DISTINCT x)`` and ``SELECT DISTINCT``.

PlanModel had no DISTINCT aggregate, so gold plans wrote ``COUNT(DISTINCT x)``
as COUNT over a function named ``DISTINCT``. That worked only because
``COUNT(DISTINCT(x))`` happens to parse as a distinct count. An aggregate now
carries ``distinct: true``, and a function named DISTINCT is rejected.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlglot
from sqlglot import expressions as exp

from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.common.settings import settings
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_PROMPT
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    PlanModel,
    SelectItem,
    TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState


def _col(name: str) -> Expr:
    return Expr(kind="column", alias="t1", column_name=name)


def _count_distinct(column: str = "CustomerId") -> Expr:
    return Expr(kind="func", func_name="COUNT", is_aggregate=True, distinct=True, args=[_col(column)])


def _state(plan: PlanModel) -> SubgraphExecutionState:
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="Invoice", columns=[
            Column(name="InvoiceId", type="int"), Column(name="CustomerId", type="int"),
        ])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )


def _plan(*exprs: Expr, distinct: bool = False) -> PlanModel:
    return PlanModel(
        distinct=distinct,
        tables=[TableRef(name="Invoice", alias="t1", ordinal=0)],
        select_items=[SelectItem(expr=e, alias=f"c{i}", ordinal=i) for i, e in enumerate(exprs)],
    )


def _sql(plan: PlanModel, dialect: str = "sqlite") -> str:
    adapter = SimpleNamespace(row_limit=100, get_dialect=lambda: dialect)
    node = GeneratorNode(SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter)))
    return node(_state(plan))["generator_response"].sql_draft


def _validate(plan: PlanModel):
    rbac = SimpleNamespace(get_allowed_tables=lambda _ctx: ["*"])
    return LogicalValidatorNode(SimpleNamespace(ds_registry=SimpleNamespace(), rbac=rbac))(_state(plan))


# --- the model -----------------------------------------------------------------


def test_distinct_defaults_to_false():
    assert Expr(kind="func", func_name="COUNT", args=[_col("CustomerId")]).distinct is False


def test_distinct_is_only_valid_on_a_function():
    with pytest.raises(ValueError, match="distinct"):
        Expr(kind="column", alias="t1", column_name="CustomerId", distinct=True)


# --- the generator -------------------------------------------------------------


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "tsql"])
def test_count_distinct_renders_as_a_distinct_aggregate(dialect):
    sql = _sql(_plan(_count_distinct()), dialect)

    # sqlglot spells a count COUNT_BIG on T-SQL, as its own parser does.
    assert ("COUNT_BIG" if dialect == "tsql" else "COUNT") + "(DISTINCT t1.CustomerId)" in sql
    count = sqlglot.parse_one(sql, read=dialect).find(exp.Count)
    assert isinstance(count.this, exp.Distinct)


def test_plan_distinct_renders_select_distinct():
    sql = _sql(_plan(_col("CustomerId"), distinct=True))

    assert sql.startswith("SELECT DISTINCT t1.CustomerId")


def test_a_plain_count_is_unchanged():
    plain = Expr(kind="func", func_name="COUNT", is_aggregate=True, args=[_col("CustomerId")])

    assert "COUNT(t1.CustomerId)" in _sql(_plan(plain))


# --- the validator -------------------------------------------------------------


def test_the_validator_accepts_count_distinct(monkeypatch):
    monkeypatch.setattr(settings, "logical_validator_strict_columns", True)

    result = _validate(_plan(_count_distinct(), distinct=True))

    assert result["errors"] == []


def test_the_validator_still_resolves_the_column_inside_distinct(monkeypatch):
    monkeypatch.setattr(settings, "logical_validator_strict_columns", True)

    result = _validate(_plan(_count_distinct("NoSuchColumn")))

    assert [e.error_code for e in result["errors"]] == [ErrorCode.COLUMN_NOT_FOUND]


def test_a_function_named_distinct_is_rejected():
    legacy = Expr(kind="func", func_name="COUNT", is_aggregate=True, args=[
        Expr(kind="func", func_name="distinct", args=[_col("CustomerId")]),
    ])

    errors = _validate(_plan(legacy))["errors"]

    [error] = errors
    assert error.error_code == ErrorCode.INVALID_PLAN_STRUCTURE
    assert error.severity == ErrorSeverity.ERROR
    assert "distinct: true" in error.message


# --- the prompt ----------------------------------------------------------------


def test_the_planner_system_message_explains_distinct():
    messages = PLANNER_PROMPT.format_messages(
        examples="", relevant_tables="", expected_schema="", semantic_context="",
        feedback="", user_query="q",
    )
    system, human = messages[0].content, messages[1].content

    assert "COUNT(DISTINCT" in system and '"distinct": true' in system
    assert "DISTINCT" not in human
