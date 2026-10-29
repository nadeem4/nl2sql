"""``COUNT(*)`` and ``t1.*``: the validator and the generator read a star the same way.

A ``*`` column name is the plan's way to write the wildcard. The validator used
to map it to a real star in a subclass of the visitor while the generator quoted
it (``COUNT(t1."*")``), so the plan passed validation and failed at execution,
after the last retry edge. Both now build the star in ``SqlVisitor``.
"""

import re
from types import SimpleNamespace

import pytest
from sqlglot import expressions as exp

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
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime, stub_ds_registry
from nl2sql_sdk import Limits

DIALECTS = ["sqlite", "postgres", "tsql", "mysql", "duckdb"]
QUOTED_STARS = ['"*"', "[*]", "`*`"]


def _star(alias=None) -> Expr:
    return Expr(kind="column", alias=alias, column_name="*")


def _count_star_plan(alias=None) -> PlanModel:
    """``COUNT(*)``, or ``COUNT(u.*)`` as a model writes it with ``alias``."""
    count = Expr(kind="func", func_name="COUNT", args=[_star(alias)])
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


@pytest.mark.parametrize("alias", [None, "u"])
@pytest.mark.parametrize("dialect", DIALECTS)
def test_count_star_passes_the_validator_and_renders_unquoted(dialect, alias):
    plan = _count_star_plan(alias)

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
