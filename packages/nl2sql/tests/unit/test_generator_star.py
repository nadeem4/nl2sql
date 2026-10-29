"""``COUNT(*)`` and ``t1.*``: the validator and the generator read a star the same way.

A ``*`` column name is the plan's way to write the wildcard. The validator used
to map it to a real star in a subclass of the visitor while the generator quoted
it (``COUNT(t1."*")``), so the plan passed validation and failed at execution,
after the last retry edge. Both now build the star in ``SqlVisitor``, which
allows it in two places only: a whole select item with no alias (``*``,
``u.*``) and ``COUNT(*)``. Anywhere else it is a retryable plan error.
"""

import re
from types import SimpleNamespace

import pytest
from sqlglot import expressions as exp

from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    GroupByItem,
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
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime, stub_ds_registry
from nl2sql_sdk import Limits

DIALECTS = ["sqlite", "postgres", "tsql", "mysql", "duckdb"]
QUOTED_STARS = ['"*"', "[*]", "`*`"]


def _star(alias=None) -> Expr:
    return Expr(kind="column", alias=alias, column_name="*")


def _count_star_plan() -> PlanModel:
    """``COUNT(*)``."""
    count = Expr(kind="func", func_name="COUNT", args=[_star()])
    return PlanModel(tables=[TableRef(name="users", alias="u")], select_items=[SelectItem(expr=count, alias="n")])


def _qualified_star_plan() -> PlanModel:
    return PlanModel(tables=[TableRef(name="users", alias="u")], select_items=[SelectItem(expr=_star("u"))])


def _state(plan: PlanModel) -> SubgraphExecutionState:
    users = Table(name="users", columns=[Column(name="id", type="int"), Column(name="name", type="string")])
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[users],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )


def _validate(plan: PlanModel, dialect: str) -> list:
    adapter = SimpleNamespace(get_dialect=lambda: dialect)
    roles = {"admin": {"description": "d", "allow": ["*"]}}
    ctx = fake_runtime(ds_registry=stub_ds_registry(adapter, limits=Limits(row_limit=1000)), policies={"roles": roles})
    return LogicalValidatorNode(ctx)(_state(plan)).get("errors") or []


def _generate(plan: PlanModel, dialect: str) -> str:
    adapter = SimpleNamespace(get_dialect=lambda: dialect)
    ctx = fake_runtime(ds_registry=stub_ds_registry(adapter, limits=Limits(row_limit=1000)))
    result = GeneratorNode(ctx)(_state(plan))
    assert not result.get("errors"), result.get("errors")
    return result["generator_response"].sql_draft


@pytest.mark.parametrize("dialect", DIALECTS)
def test_count_star_passes_the_validator_and_renders_unquoted(dialect):
    plan = _count_star_plan()

    assert _validate(plan, dialect) == []
    sql = _generate(plan, dialect)

    # T-SQL spells it COUNT_BIG(*); the star is bare and unqualified either way.
    assert re.search(r"COUNT(_BIG)?\(\*\)", sql.upper()), sql
    assert not any(q in sql for q in QUOTED_STARS)
    tree = exp.maybe_parse(sql, dialect=dialect)
    assert tree.find(exp.Count).this.is_star


@pytest.mark.parametrize("dialect", DIALECTS)
def test_qualified_star_passes_the_validator_and_renders_unquoted(dialect):
    plan = _qualified_star_plan()

    assert _validate(plan, dialect) == []
    sql = _generate(plan, dialect)

    assert not any(q in sql for q in QUOTED_STARS)
    star = exp.maybe_parse(sql, dialect=dialect).find(exp.Star)
    assert star is not None
    assert isinstance(star.parent, exp.Column) and star.parent.table == "u"


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_bare_star_select_item_passes_the_validator_and_renders_unquoted(dialect):
    plan = PlanModel(tables=[TableRef(name="users", alias="u")], select_items=[SelectItem(expr=_star())])

    assert _validate(plan, dialect) == []
    sql = _generate(plan, dialect)

    assert not any(q in sql for q in QUOTED_STARS)
    assert exp.maybe_parse(sql, dialect=dialect).find(exp.Star) is not None


def _count(*args: Expr, distinct: bool = False, name: str = "COUNT") -> Expr:
    return Expr(kind="func", func_name=name, args=list(args), distinct=distinct, is_aggregate=True)


def _id() -> Expr:
    return Expr(kind="column", alias="u", column_name="id")


def _one() -> Expr:
    return Expr(kind="literal", value=1)


def _plan(select=None, **rest) -> PlanModel:
    items = select if select is not None else [SelectItem(expr=_id())]
    return PlanModel(tables=[TableRef(name="users", alias="u")], select_items=items, **rest)


# Every star the database would refuse, or would read differently from what the
# plan says. ``COUNT(u.*)`` is refused rather than rewritten to ``COUNT(*)``:
# over an outer join Postgres counts the NULL-extended rows of ``u`` as 0, and
# ``COUNT(*)`` counts them as 1.
MISPLACED_STARS = {
    "COUNT(u.*)": _plan([SelectItem(expr=_count(_star("u")), alias="n")]),
    "COUNT(DISTINCT *)": _plan([SelectItem(expr=_count(_star(), distinct=True), alias="n")]),
    "COUNT(DISTINCT u.*)": _plan([SelectItem(expr=_count(_star("u"), distinct=True), alias="n")]),
    "SUM(*)": _plan([SelectItem(expr=_count(_star(), name="SUM"), alias="n")]),
    "SUM(u.*)": _plan([SelectItem(expr=_count(_star("u"), name="SUM"), alias="n")]),
    "MAX(*)": _plan([SelectItem(expr=_count(_star(), name="MAX"), alias="n")]),
    "u.* AS x": _plan([SelectItem(expr=_star("u"), alias="x")]),
    "GROUP BY u.*": _plan(group_by=[GroupByItem(expr=_star("u"))]),
    "ORDER BY u.*": _plan(order_by=[OrderItem(expr=_star("u"))]),
    "WHERE u.* = 1": _plan(where=Expr(kind="binary", op="=", left=_star("u"), right=_one())),
    "u.id + *": _plan([SelectItem(expr=Expr(kind="binary", op="+", left=_id(), right=_star()), alias="n")]),
    "COUNT(*, u.id)": _plan([SelectItem(expr=_count(_star(), _id()), alias="n")]),
    "x.* undeclared": _plan([SelectItem(expr=_star("x"))]),
}


@pytest.mark.parametrize("case", MISPLACED_STARS)
@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_misplaced_star_is_a_retryable_plan_error(dialect, case):
    errors = _validate(MISPLACED_STARS[case], dialect)

    codes = [e.error_code for e in errors]
    assert ErrorCode.VALIDATOR_CRASH not in codes, errors
    [star] = [e for e in errors if e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE]
    assert star.severity == ErrorSeverity.ERROR and star.is_retryable
    if case != "x.* undeclared":  # named on its own below
        assert "COUNT(*)" in star.message, star.message


def test_count_of_a_qualified_star_tells_the_planner_to_count_a_key():
    [error] = _validate(MISPLACED_STARS["COUNT(u.*)"], "postgres")

    assert "non-null column such as its key" in error.message, error.message


def test_an_undeclared_star_alias_is_named():
    [error] = _validate(MISPLACED_STARS["x.* undeclared"], "sqlite")

    assert "'x'" in error.message, error.message


@pytest.mark.parametrize("case", MISPLACED_STARS)
def test_the_generator_never_renders_a_misplaced_star(case):
    adapter = SimpleNamespace(get_dialect=lambda: "postgres")
    ctx = fake_runtime(ds_registry=stub_ds_registry(adapter, limits=Limits(row_limit=1000)))

    result = GeneratorNode(ctx)(_state(MISPLACED_STARS[case]))

    assert [e.error_code for e in result["errors"]] == [ErrorCode.SQL_GEN_FAILED]


def test_the_planner_system_message_says_how_to_count_rows():
    from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_PROMPT

    system = PLANNER_PROMPT.format_messages(
        examples="", relevant_tables="", expected_schema="", semantic_context="", business_context="{}",
        feedback="", user_query="q", today="2013-12-31",
    )[0].content

    assert "COUNT(*)" in system and 'column_name "*"' in system