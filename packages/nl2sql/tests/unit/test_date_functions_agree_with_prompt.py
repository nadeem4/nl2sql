"""The date functions a plan may call are the ones the planner prompt offers.

The prompt tells the model to use only DATE_PART and DATE_TRUNC, while the
validator's allow-list also accepted DATE, DATETIME and STRFTIME, and its
UNSUPPORTED_FUNCTION message listed them back to the model on a retry. A rule
the prompt forbids and the validator teaches is no rule.

STRFTIME stays accepted (a recorded demo answer and three hand-written tier 1
gold plans call it), but only as a legacy name: nothing offers it to the model.
"""
from types import SimpleNamespace

from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode
from nl2sql.pipeline.nodes.ast_planner.functions import (
    ALLOWED_FUNCTIONS,
    LEGACY_FUNCTIONS,
    date_operation,
)
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_SYSTEM_PROMPT
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState

DATE = Expr(kind="column", alias="t1", column_name="d")
# Every name in the allow-list that works on a date or time.
DATE_NAMES = {"DATE", "DATETIME", "STRFTIME", "YEAR", "QUARTER", "MONTH", "DAY", "EXTRACT",
              "DATE_PART", "DATE_TRUNC", "TIME", "JULIANDAY", "NOW", "CURRENT_DATE"}


def _unsupported_message(func_name):
    plan = PlanModel(
        tables=[TableRef(name="T", alias="t1")],
        select_items=[SelectItem(alias="v", expr=Expr(kind="func", func_name=func_name, args=[DATE]))],
    )
    node = LogicalValidatorNode(SimpleNamespace(
        ds_registry=SimpleNamespace(), rbac=SimpleNamespace(get_allowed_tables=lambda _: ["*"])))
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds", intent="q"),
        relevant_tables=[Table(name="T", columns=[Column(name="d", type="DATE")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )
    errors = node(state).get("errors") or []
    return next(e.message for e in errors if e.error_code == ErrorCode.UNSUPPORTED_FUNCTION)


def _offered(message):
    return {n.strip(" .") for n in message.split("Use one of:")[1].split(",")}


def test_every_allowed_date_function_is_a_portable_operation_or_a_legacy_name():
    for name in ALLOWED_FUNCTIONS & DATE_NAMES:
        portable = date_operation(name, [DATE]) is not None or date_operation(name, [DATE, DATE]) is not None
        assert portable or name in LEGACY_FUNCTIONS, (
            f"{name} is allowed, but the planner prompt forbids every date function "
            "except DATE_PART and DATE_TRUNC")


def test_the_prompt_offers_the_portable_date_functions():
    assert "DATE_PART(unit, date)" in PLANNER_SYSTEM_PROMPT
    assert "DATE_TRUNC(unit, date)" in PLANNER_SYSTEM_PROMPT
    assert "Never use any other date or time function" in PLANNER_SYSTEM_PROMPT


def test_date_and_datetime_are_no_longer_allowed():
    assert "DATE" not in ALLOWED_FUNCTIONS
    assert "DATETIME" not in ALLOWED_FUNCTIONS
    offered = _offered(_unsupported_message("DATE"))
    assert "DATE" not in offered and "DATETIME" not in offered


def test_a_legacy_name_still_validates_but_is_never_offered_on_a_retry():
    assert LEGACY_FUNCTIONS <= ALLOWED_FUNCTIONS
    offered = _offered(_unsupported_message("sleep"))
    for name in LEGACY_FUNCTIONS:
        assert name not in offered
    assert "DATE_PART" in offered and "DATE_TRUNC" in offered
