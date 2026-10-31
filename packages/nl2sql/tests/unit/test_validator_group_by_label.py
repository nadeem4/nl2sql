"""A label built from several columns of a table must be grouped with that table's key.

Grouping by ``first_name || ' ' || last_name`` alone merges two people who share a
name. The validator (which has the retry edge) rejects it and says to add the key.
"""
from types import SimpleNamespace

from nl2sql.common.errors import ErrorCode
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse, Expr, GroupByItem, PlanModel, SelectItem, TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime


def _col(name):
    return Expr(kind="column", alias="a", column_name=name)


def _label():
    return Expr(kind="binary", op="||", left=Expr(kind="binary", op="||", left=_col("first_name"),
                right=Expr(kind="literal", value=" ")), right=_col("last_name"))


def _errors(group_by, primary_key=("actor_id",)):
    table = Table(
        name="actor",
        columns=[Column(name=n, type="text") for n in ("actor_id", "first_name", "last_name")],
        primary_key=list(primary_key) if primary_key else None,
    )
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="actor", alias="a")],
        select_items=[SelectItem(alias="n", expr=_label()),
                      SelectItem(alias="c", expr=Expr(kind="func", func_name="COUNT", is_aggregate=True,
                                                      args=[_col("actor_id")]))],
        group_by=[GroupByItem(expr=e) for e in group_by],
    )
    state = SubgraphExecutionState(
        trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[table], ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )
    ctx = fake_runtime(ds_registry=SimpleNamespace(), policies={"allow_all": True})
    return [e for e in LogicalValidatorNode(ctx)(state)["errors"] if "group" in e.message.lower()]


def test_a_label_without_the_tables_key_is_rejected_with_the_fix():
    errors = _errors([_label()])
    assert len(errors) == 1
    assert errors[0].error_code == ErrorCode.INVALID_PLAN_STRUCTURE
    assert "actor_id" in errors[0].message and "GROUP BY" in errors[0].message.upper()


def test_a_label_grouped_with_the_key_is_accepted():
    assert _errors([_col("actor_id"), _label()]) == []


def test_grouping_by_one_column_is_accepted():
    assert _errors([_col("first_name")]) == []


def test_a_date_part_of_one_column_is_accepted():
    part = Expr(kind="func", func_name="DATE_PART",
                args=[Expr(kind="literal", value="year"), _col("first_name")])
    assert _errors([part]) == []


def test_a_table_without_a_known_key_is_not_judged():
    assert _errors([_label()], primary_key=None) == []


def test_concat_function_of_two_columns_is_rejected_too():
    label = Expr(kind="func", func_name="CONCAT", args=[_col("first_name"), _col("last_name")])
    assert len(_errors([label])) == 1


def test_the_message_says_to_group_by_the_key():
    assert "group by" in _errors([_label()])[0].message.lower()


def test_arithmetic_coalesce_and_date_diff_of_two_columns_are_allowed():
    minus = Expr(kind="binary", op="-", left=_col("first_name"), right=_col("last_name"))
    coalesce = Expr(kind="func", func_name="COALESCE", args=[_col("first_name"), _col("last_name")])
    diff = Expr(kind="func", func_name="DATE_DIFF",
                args=[Expr(kind="literal", value="day"), _col("first_name"), _col("last_name")])
    for expr in (minus, coalesce, diff):
        assert _errors([expr]) == []


def test_the_key_is_looked_up_in_the_plans_schema():
    other = Table(name="other.actor", columns=[Column(name="actor_id", type="text")], primary_key=["wrong_id"])
    mine = Table(name="main.actor", columns=[Column(name=n, type="text") for n in ("actor_id", "first_name", "last_name")],
                 primary_key=["actor_id"])
    plan = PlanModel(
        query_type="READ", tables=[TableRef(name="actor", schema_name="main", alias="a")],
        select_items=[SelectItem(alias="n", expr=_label())], group_by=[GroupByItem(expr=_label())],
    )
    state = SubgraphExecutionState(
        trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[other, mine], ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )
    ctx = fake_runtime(ds_registry=SimpleNamespace(), policies={"allow_all": True})
    errors = [e for e in LogicalValidatorNode(ctx)(state)["errors"] if "group" in e.message.lower()]
    assert len(errors) == 1 and "actor_id" in errors[0].message and "wrong_id" not in errors[0].message