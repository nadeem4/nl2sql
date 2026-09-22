"""Only known function names reach the SQL.

A plan's ``func_name`` is text the model wrote, and the generator puts it into
the SQL as the function's name. Nothing checked it except for ``DISTINCT``, so
``SELECT 1); DELETE FROM T; --`` rendered as-is. The logical validator now
accepts only a plain identifier from the plan language's function list and
rejects anything else with ``UNSUPPORTED_FUNCTION``, which the refiner retries.
"""
from types import SimpleNamespace

import pytest

from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.pipeline.nodes.ast_planner.functions import ALLOWED_FUNCTIONS
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse, Expr, OrderItem, PlanModel, SelectItem, TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState

NAME = Expr(kind="column", alias="t1", column_name="Name")


def _func(name, *args, **kw):
    return Expr(kind="func", func_name=name, args=list(args), **kw)


def _errors(expr, where="select"):
    plan = PlanModel(
        tables=[TableRef(name="Artist", alias="t1", ordinal=0)],
        select_items=[SelectItem(ordinal=0, alias="v", expr=expr if where == "select" else NAME)],
        order_by=[OrderItem(ordinal=0, expr=expr)] if where == "order_by" else [],
    )
    rbac = SimpleNamespace(get_allowed_tables=lambda _: ["*"])
    node = LogicalValidatorNode(SimpleNamespace(ds_registry=SimpleNamespace(), rbac=rbac))
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="q"),
        relevant_tables=[Table(name="Artist", columns=[Column(name="Name", type="text")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )
    return node(state).get("errors") or []


def _unsupported(errors):
    return [e for e in errors if e.error_code == ErrorCode.UNSUPPORTED_FUNCTION]


@pytest.mark.parametrize("name", [
    "SELECT 1); DELETE FROM T; --",
    "UPPER(x)); DROP TABLE Artist; --",
    "load_extension",
    "LOWER ",  # not a bare identifier
    "pg_sleep",
    "",
])
def test_an_unknown_or_malformed_function_name_is_rejected(name):
    errors = _unsupported(_errors(_func(name or " ", NAME)))

    assert len(errors) == 1
    assert errors[0].severity == ErrorSeverity.ERROR  # retryable: the refiner gets the message
    assert errors[0].is_retryable
    assert "UPPER" in errors[0].message  # it lists what is allowed


def test_a_nested_or_ordering_function_is_checked_too():
    nested = _func("UPPER", _func("evil();--", NAME))

    assert _unsupported(_errors(nested))
    assert _unsupported(_errors(_func("sleep", NAME), where="order_by"))


@pytest.mark.parametrize("name", ["UPPER", "lower", "Count", "ROUND", "COALESCE", "STRFTIME", "LENGTH"])
def test_known_functions_pass_in_any_case(name):
    assert not _unsupported(_errors(_func(name, NAME)))


def test_the_allow_list_holds_only_plain_identifiers():
    assert all(n.isidentifier() and n == n.upper() for n in ALLOWED_FUNCTIONS)
    assert {"COUNT", "SUM", "AVG", "MIN", "MAX"} <= ALLOWED_FUNCTIONS
