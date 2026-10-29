"""A rejected join says which joins are allowed, and a join may name its sides either way round (wave_c.md, section 18.3, C-W6).

The shapes are the Pagila ones the keyed run planned wrongly: shortcut joins on a shared key that is not a
declared foreign key, and a declared join written with its operands the other way round.
"""
from types import SimpleNamespace

from nl2sql.common.errors import ErrorCode
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, JoinSpec, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.retry_feedback import as_planner_feedback
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime


def _col(alias, name):
    return Expr(kind="column", alias=alias, column_name=name)


def _rel(from_table, column, to_table):
    return {"from_table": from_table, "to_table": to_table, "from_columns": [column], "to_columns": [column]}


def _table(name, columns, *relationships):
    return Table(name=name, columns=[Column(name=c, type="int") for c in columns], relationships=list(relationships))


# inventory and film_category both point at film; nothing joins them directly.
TABLES = [
    _table("inventory", ["inventory_id", "film_id", "store_id"], _rel("inventory", "film_id", "film")),
    _table("film", ["film_id", "title"]),
    _table("film_category", ["film_id", "category_id"], _rel("film_category", "film_id", "film"),
           _rel("film_category", "category_id", "category")),
    _table("category", ["category_id", "name"]),
    _table("rental", ["rental_id", "inventory_id"], _rel("rental", "inventory_id", "inventory")),
]


def _validate(plan):
    state = SubgraphExecutionState(
        trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"), relevant_tables=TABLES,
        ast_planner_response=ASTPlannerResponse(plan=plan), user_context=UserContext())
    ctx = fake_runtime(ds_registry=SimpleNamespace(), policies={"allow_all": True},
                       settings={"pipeline": {"strict_columns": True}})
    return LogicalValidatorNode(ctx)(state).get("errors") or []


def _join_plan(left, right, condition_left, condition_right, left_table="inventory", right_table="film_category"):
    return PlanModel(
        query_type="READ", tables=[TableRef(name=left_table, alias=left), TableRef(name=right_table, alias=right)],
        select_items=[SelectItem(expr=_col(left, condition_left[1]))],
        joins=[JoinSpec(left_alias=left, right_alias=right, join_type="inner",
                        condition=Expr(kind="binary", op="=", left=_col(*condition_left), right=_col(*condition_right)))])


def _rejection(errors):
    [error] = [e for e in errors if e.message.startswith("Join does not match any allowed relationship.")]
    return error


def test_a_shortcut_join_is_told_the_declared_path():
    plan = _join_plan("i", "fc", ("i", "film_id"), ("fc", "film_id"))

    error = _rejection(_validate(plan))

    assert error.error_code == ErrorCode.INVALID_PLAN_STRUCTURE
    assert "No relationship is declared between inventory and film_category." in error.message
    assert "inventory.film_id = film.film_id" in error.message
    assert "film.film_id = film_category.film_id" in error.message


def test_a_longer_path_names_every_bridge_in_order():
    plan = _join_plan("r", "c", ("r", "rental_id"), ("c", "category_id"), "rental", "category")

    message = _rejection(_validate(plan)).message

    steps = ["rental.inventory_id = inventory.inventory_id", "inventory.film_id = film.film_id",
             "film.film_id = film_category.film_id", "film_category.category_id = category.category_id"]
    assert [message.index(step) for step in steps] == sorted(message.index(step) for step in steps)


def test_tables_with_a_declared_key_are_told_that_key_when_the_columns_are_wrong():
    plan = _join_plan("i", "f", ("i", "store_id"), ("f", "film_id"), "inventory", "film")

    message = _rejection(_validate(plan)).message

    assert "Allowed joins between inventory and film: inventory.film_id = film.film_id." in message


def test_tables_with_no_path_in_the_schema_say_so():
    tables = TABLES + [_table("island", ["island_id"])]
    plan = _join_plan("i", "x", ("i", "film_id"), ("x", "island_id"), "inventory", "island")
    state = SubgraphExecutionState(
        trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"), relevant_tables=tables,
        ast_planner_response=ASTPlannerResponse(plan=plan), user_context=UserContext())
    ctx = fake_runtime(ds_registry=SimpleNamespace(), policies={"allow_all": True},
                       settings={"pipeline": {"strict_columns": True}})

    message = _rejection(LogicalValidatorNode(ctx)(state)["errors"]).message

    assert "No relationship is declared between inventory and island." in message
    assert "film" not in message.split("No relationship is declared between inventory and island.")[1]


def test_the_planner_and_the_refiner_are_sent_the_hint_on_a_retry():
    errors = _validate(_join_plan("i", "fc", ("i", "film_id"), ("fc", "film_id")))

    assert "film.film_id = film_category.film_id" in as_planner_feedback(errors)


def test_a_join_written_with_its_operands_the_other_way_round_is_accepted():
    plan = _join_plan("i", "f", ("f", "film_id"), ("i", "film_id"), "inventory", "film")

    errors = _validate(plan)

    assert not [e for e in errors if e.severity.value in ("ERROR", "CRITICAL")], [e.message for e in errors]


def test_the_reversed_join_still_generates_the_same_equality():
    plan = _join_plan("i", "f", ("f", "film_id"), ("i", "film_id"), "inventory", "film")
    node = LogicalValidatorNode(fake_runtime(ds_registry=SimpleNamespace(), policies={"allow_all": True}))

    sql = node._generator._build_query(plan, 10, "postgres").sql(dialect="postgres")

    assert "f.film_id = i.film_id" in sql


def test_the_path_never_runs_through_a_table_the_planner_was_not_sent():
    tables = [t for t in TABLES if t.name != "film"]
    plan = _join_plan("i", "fc", ("i", "film_id"), ("fc", "film_id"))
    state = SubgraphExecutionState(
        trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"), relevant_tables=tables,
        ast_planner_response=ASTPlannerResponse(plan=plan), user_context=UserContext())
    ctx = fake_runtime(ds_registry=SimpleNamespace(), policies={"allow_all": True},
                       settings={"pipeline": {"strict_columns": True}})

    message = _rejection(LogicalValidatorNode(ctx)(state)["errors"]).message

    assert "film." not in message and "film_id = film" not in message
    assert "No path of declared relationships connects them in the schema you were given." in message


def test_an_unknown_join_alias_gets_no_empty_table_name_in_a_hint():
    plan = _join_plan("i", "fc", ("i", "film_id"), ("fc", "film_id"))
    plan.joins[0].right_alias = "zz"

    messages = [e.message for e in _validate(plan)]

    assert not [m for m in messages if "between  " in m or " and ." in m], messages
