"""Dashboard filters (docs/architecture/wave_c.md, section 6 "Filters", D117; row D2b).

A filter value becomes a typed predicate added to each stored plan that reads the filter's table, before ``plan.after``,
the validator and the generator. The run itself is canned here (see ``test_dashboards_d2``); the real pipeline is held
by ``tests/e2e/test_dashboards_fake_llm.py``.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List

import pytest
import sqlglot
from sqlglot import exp

from nl2sql.api.results_api import ResultsAPI
from nl2sql.common.errors import DashboardNotFound
from nl2sql.dashboards.models import DashboardFilter
from nl2sql.dashboards.store import DashboardStore
from nl2sql.execution.artifacts.results import ResultPage
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse
from nl2sql.testing.runtime import fake_runtime, stub_ds_registry
from nl2sql_sdk import Limits

# The service tests reuse D2's canned runs and engine fixtures (this directory is on ``sys.path``).
from test_dashboards_d2 import (  # noqa: F401  (fixtures)
    ANA, BOB, chart_plan, engine, good, listen, make, put_run, runs, seed, server, stored, tile,
)

CITY = DashboardFilter(id="city", label="City", column="ds1.main.orders.city", kind="text", op="in")
WHEN = DashboardFilter(id="when", label="Date", column="ds1.main.orders.created", kind="temporal", op="between")
AMOUNT = DashboardFilter(id="amount", label="Amount", column="ds1.main.orders.amount", kind="number", op="between")
OTHER = DashboardFilter(id="region", label="Region", column="ds1.main.customers.region", kind="text", op="in")
ELSEWHERE = DashboardFilter(id="site", label="Site", column="ds2.main.orders.site", kind="text", op="in")


def show(e: Expr) -> str:
    """An expression as infix text, parenthesised at every operator, for comparing a predicate's shape."""
    if e.kind == "literal":
        return repr(e.value)
    if e.kind == "column":
        return f"{e.alias}.{e.column_name}"
    assert e.kind == "binary", e.kind
    return f"({show(e.left)} {e.op} {show(e.right)})"


def sent(runs, index: int = 0, sub_query: str = "sq1") -> PlanModel:
    """The plan of ``sub_query`` as the run received it."""
    return runs.calls[index]["plan"].plans[sub_query]


# ------------------------------------------------------------------ the predicate

def test_filter_is_a_predicate_before_the_validator(engine, runs):
    seed(engine, filters=[CITY], tiles=[tile("t1")])

    run = engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    # The plan handed to run_query already carries the predicate, so plan.after, the validator and the generator
    # all see it; the stored plan is untouched and a filter is never text added to the question.
    assert show(sent(runs).where) == "(t1.city = 'Paris')"
    assert runs.calls[0]["question"] == "What is t1?"
    [stored_tile] = DashboardStore(engine._runtime.state).get("d1", ("acme", "ana")).tiles
    assert stored_tile.plan.plans["sq1"].get("where") is None
    assert [t.filtered for t in run.tiles] == [True] and run.filters == {"city": ["Paris"]}


def test_between_is_two_comparisons_and_in_is_ors(engine, runs):
    seed(engine, filters=[CITY, WHEN, AMOUNT], tiles=[tile("t1")])

    engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris", "Oslo", "Rome"]})
    engine.dashboards.refresh("d1", user_context=ANA, filters={"when": ["2024-01-01", "2024-06-30T12:00:00Z"]})
    engine.dashboards.refresh("d1", user_context=ANA, filters={"amount": [1, 2.5]})
    engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"], "amount": [0, 9]})

    assert show(sent(runs, 0).where) == "(((t1.city = 'Paris') OR (t1.city = 'Oslo')) OR (t1.city = 'Rome'))"
    assert show(sent(runs, 1).where) == "((t1.created >= '2024-01-01') AND (t1.created <= '2024-06-30T12:00:00Z'))"
    assert show(sent(runs, 2).where) == "((t1.amount >= 1) AND (t1.amount <= 2.5))"
    assert show(sent(runs, 3).where) == "((t1.city = 'Paris') AND ((t1.amount >= 0) AND (t1.amount <= 9)))"


@pytest.mark.parametrize("values, expected", [
    (["2024-01-01", "2024-01-31"], "((t1.created >= '2024-01-01') AND (t1.created < '2024-02-01'))"),
    (["2024-01-31", "2024-01-31"], "((t1.created >= '2024-01-31') AND (t1.created < '2024-02-01'))"),
    (["2024-02-28", "2024-02-29"], "((t1.created >= '2024-02-28') AND (t1.created < '2024-03-01'))"),
    (["2023-12-01", "2023-12-31"], "((t1.created >= '2023-12-01') AND (t1.created < '2024-01-01'))"),
    (["2024-01-01", "2024-01-31T10:30:00Z"], "((t1.created >= '2024-01-01') AND (t1.created <= '2024-01-31T10:30:00Z'))"),
    (["2024-01-01T08:00", "2024-01-31"], "((t1.created >= '2024-01-01T08:00') AND (t1.created < '2024-02-01'))"),
])
def test_a_date_only_upper_bound_includes_that_whole_day(engine, runs, values, expected):
    seed(engine, filters=[WHEN], tiles=[tile("t1")])

    engine.dashboards.refresh("d1", user_context=ANA, filters={"when": values})

    assert show(sent(runs).where) == expected


def test_the_predicate_is_joined_to_the_plans_own_where_with_and(engine, runs):
    body = chart_plan().plans["sq1"]
    body["where"] = {"kind": "binary", "op": "=", "left": {"kind": "column", "alias": "t1", "column_name": "open"},
                     "right": {"kind": "literal", "value": True}}
    plan = chart_plan().model_copy(update={"plans": {"sq1": body}})
    seed(engine, filters=[CITY], tiles=[tile("t1", plan=plan)])

    engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    assert show(sent(runs).where) == "((t1.open = True) AND (t1.city = 'Paris'))"


def test_filter_skips_queries_that_do_not_read_its_table(engine, runs):
    seed(engine, filters=[CITY, OTHER, ELSEWHERE], tiles=[tile("t1")])

    run = engine.dashboards.refresh("d1", user_context=ANA,
                                    filters={"city": ["Paris"], "region": ["EU"], "site": ["A"]})

    # Only the filter on `orders` of `ds1` reached the plan; a table it does not read, or another datasource, did not.
    assert show(sent(runs).where) == "(t1.city = 'Paris')"
    assert [t.filtered for t in run.tiles] == [False], "not every active filter reached the chart"


def test_a_chart_no_filter_reaches_runs_unfiltered_and_says_so(engine, runs):
    seed(engine, filters=[OTHER], tiles=[tile("t1")])

    run = engine.dashboards.refresh("d1", user_context=ANA, filters={"region": ["EU"]})

    assert sent(runs).where is None and run.tiles[0].status == "ok" and run.tiles[0].filtered is False


def test_a_verified_chart_cannot_be_filtered_and_runs_unfiltered(engine, runs):
    from nl2sql.dashboards.models import ChartPlan
    from nl2sql.hooks.approval import VerifiedRef

    plan = ChartPlan(verified=VerifiedRef(id="top", params={}), verified_datasource_id="ds1")
    seed(engine, filters=[CITY], tiles=[tile("t1", plan=plan)])

    run = engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    assert runs.calls[0]["plan"].verified is not None and run.tiles[0].filtered is False


def test_a_filter_matches_the_schema_and_ignores_case(engine, runs):
    plan = chart_plan()
    plan.plans["sq1"]["tables"][0].update(name="Orders", schema_name="MAIN")
    seed(engine, filters=[CITY], tiles=[tile("t1", plan=plan)])

    run = engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    assert show(sent(runs).where) == "(t1.city = 'Paris')" and run.tiles[0].filtered is True


def test_a_filter_on_another_schema_does_not_reach_a_same_named_table(engine, runs):
    plan = chart_plan()
    plan.plans["sq1"]["tables"][0].update(schema_name="archive")
    seed(engine, filters=[CITY], tiles=[tile("t1", plan=plan)])

    run = engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    assert sent(runs).where is None and run.tiles[0].filtered is False


def test_each_alias_of_the_table_is_filtered(engine, runs):
    plan = chart_plan()
    plan.plans["sq1"]["tables"].append({"name": "orders", "schema_name": "main", "alias": "t2"})
    seed(engine, filters=[CITY], tiles=[tile("t1", plan=plan)])

    engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    assert show(sent(runs).where) == "((t1.city = 'Paris') AND (t2.city = 'Paris'))"


def test_a_sub_query_of_another_datasource_is_not_filtered(engine, runs):
    plan = chart_plan(queries=2)
    plan.decomposition["sub_queries"][1]["datasource_id"] = "ds2"
    seed(engine, filters=[CITY], tiles=[tile("t1", plan=plan)])

    run = engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    assert sent(runs, sub_query="sq1").where is not None and sent(runs, sub_query="sq2").where is None
    assert run.tiles[0].filtered is False, "one of the chart's queries ran unfiltered"


def test_a_chart_is_filtered_only_when_every_query_of_it_was(engine, runs):
    one_side = chart_plan(queries=2)
    one_side.plans["sq2"]["tables"][0]["name"] = "customers"  # this side does not read the filter's table
    seed(engine, filters=[CITY], tiles=[tile("t1", plan=chart_plan(queries=2)), tile("t2", x=2, plan=one_side)])

    run = engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    assert [t.filtered for t in run.tiles] == [True, False]


def test_a_table_with_no_schema_is_not_filtered(engine, runs):
    plan = chart_plan()
    del plan.plans["sq1"]["tables"][0]["schema_name"]
    seed(engine, filters=[CITY], tiles=[tile("t1", plan=plan)])

    run = engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    assert sent(runs).where is None and run.tiles[0].filtered is False, "its schema is not known to be the filter's"


def test_a_filtered_refresh_does_not_replace_the_stored_results(engine, runs):
    seed(engine, filters=[CITY], tiles=[tile("t1")])
    before = put_run(engine, "t1")

    run = engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    assert run.tiles[0].status == "ok" and stored(engine)["t1"] == before, "a viewer's filter is not the dashboard's"


def test_an_unfiltered_refresh_of_a_dashboard_with_filters_is_unchanged(engine, runs):
    seed(engine, filters=[CITY], tiles=[tile("t1")])

    run = engine.dashboards.refresh("d1", user_context=ANA)
    empty = engine.dashboards.refresh("d1", user_context=ANA, filters={})

    assert all(c["plan"].plans["sq1"].where is None for c in runs.calls)
    assert run.tiles[0].filtered is False and run.filters == {} and empty.filters == {}
    assert stored(engine)["t1"].status == "ok"


def test_the_event_names_the_filters_applied_and_never_their_values(engine, runs):
    seed(engine, filters=[CITY, WHEN], tiles=[tile("t1")])
    seen = listen(engine, "dashboard.refreshed")

    engine.dashboards.refresh("d1", user_context=ANA, filters={"when": ["2024-01-01", "2024-02-01"], "city": ["Paris"]})

    [event] = seen
    assert event.data["filters"] == ["city", "when"], "in the dashboard's order"
    assert "Paris" not in str(event.data)


# ------------------------------------------------------------------ the checks

@pytest.mark.parametrize("filters, message", [
    ({"nope": ["x"]}, "No filter 'nope'"),
    ({"city": []}, "City.*1 to 100"),
    ({"city": ["x"] * 101}, "City.*1 to 100"),
    ({"city": [3]}, "City.*text"),
    ({"city": "Paris"}, "City.*list"),
    ({"when": ["2024-01-01"]}, "Date.*exactly two"),
    ({"when": ["2024-01-01", "soon"]}, "Date.*ISO"),
    ({"when": ["2024-01-01", 5]}, "Date.*ISO"),
    ({"when": ["2024-01-01", "20240131"]}, "Date.*ISO"),
    ({"when": ["2024-1-1", "2024-01-31"]}, "Date.*ISO"),
    ({"when": ["2024-01-01", "2024-01-31 12:00"]}, "Date.*ISO"),
    ({"when": ["2024-01-01", "2024-13-01"]}, "Date.*ISO"),
    ({"when": ["2024-01-01", "2024-01-31T12"]}, "Date.*ISO"),
    ({"when": ["2024-01-01", "2024-01-31T12:00:00+0200"]}, "Date.*ISO"),
    ({"amount": [1, "9"]}, "Amount.*number"),
    ({"amount": [True, 9]}, "Amount.*number"),
    ({"amount": [1, float("nan")]}, "Amount.*number"),
    ({"city": ["Paris"], "amount": ["x", 1]}, "Amount.*number"),
])
def test_wrong_typed_filter_value_is_refused_before_running(engine, runs, filters, message):
    seed(engine, filters=[CITY, WHEN, AMOUNT])

    with pytest.raises(ValueError, match=message):
        engine.dashboards.refresh("d1", user_context=ANA, filters=filters)

    assert runs.calls == [], "nothing ran"
    assert stored(engine) == {}


@pytest.mark.parametrize("stamp", ["2024-01-31", "2024-01-31T12:00", "2024-01-31T12:00:00", "2024-01-31T12:00:00.250000",
                                   "2024-01-31T12:00:00Z", "2024-01-31T12:00:00+02:00", "2024-01-31T12:00-05:30"])
def test_the_documented_iso_forms_are_accepted(engine, runs, stamp):
    seed(engine, filters=[WHEN], tiles=[tile("t1")])

    engine.dashboards.refresh("d1", user_context=ANA, filters={"when": ["2024-01-01", stamp]})

    assert len(runs.calls) == 1


def test_a_filtered_refresh_takes_no_lock_and_an_unfiltered_one_does(engine, runs):
    seed(engine, filters=[CITY], tiles=[tile("t1")])

    with engine._runtime.state.lock("dashboard-refresh:d1", 60):  # a scheduled or manual refresh is running
        run = engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})
        with pytest.raises(ValueError, match="being refreshed"):
            engine.dashboards.refresh("d1", user_context=ANA)
        with pytest.raises(ValueError, match="being refreshed"):
            engine.dashboards.refresh("d1", user_context=ANA, filters={})

    assert run.tiles[0].status == "ok" and len(runs.calls) == 1


def test_a_boolean_filter_takes_booleans_and_text_takes_only_text(engine, runs):
    paid = DashboardFilter(id="paid", label="Paid", column="ds1.main.orders.paid", kind="boolean", op="in")
    seed(engine, filters=[paid], tiles=[tile("t1")])

    engine.dashboards.refresh("d1", user_context=ANA, filters={"paid": [True]})
    with pytest.raises(ValueError, match="Paid.*boolean"):
        engine.dashboards.refresh("d1", user_context=ANA, filters={"paid": ["yes"]})

    assert show(sent(runs).where) == "(t1.paid = True)" and len(runs.calls) == 1


def test_unknown_filter_id_is_an_error(engine, runs):
    seed(engine, filters=[CITY])

    with pytest.raises(ValueError, match="No filter 'colour'.*'city'"):
        engine.dashboards.refresh("d1", user_context=ANA, filters={"colour": ["red"]})
    with pytest.raises(ValueError, match="No filter 'colour'"):
        engine.dashboards.filter_values("d1", "colour", user_context=ANA)

    assert runs.calls == []


def test_a_filter_cannot_widen_access(engine, runs):
    """A filter only adds a predicate: the plan still goes to run_query as the caller, whose scope the validator
    applies, so a table the caller cannot read is denied whatever the filter says."""
    seed(engine, filters=[CITY], tiles=[tile("t1")])

    engine.dashboards.refresh("d1", user_context=ANA, filters={"city": ["Paris"]})

    [call] = runs.calls
    assert call["user_context"] == ANA and call["answer"] is False and call["execute"] is True
    assert call["plan"].plans["sq1"].tables == PlanModel.model_validate(chart_plan().plans["sq1"]).tables


def test_another_owner_cannot_filter_a_dashboard(engine, runs):
    seed(engine, filters=[CITY])

    with pytest.raises(DashboardNotFound):
        engine.dashboards.refresh("d1", user_context=BOB, filters={"city": ["Paris"]})
    with pytest.raises(DashboardNotFound):
        engine.dashboards.filter_values("d1", "city", user_context=BOB)

    assert runs.calls == []


# ------------------------------------------------------------------ filter_values

@pytest.fixture
def paged(monkeypatch):
    """``ResultsAPI.page`` returns the rows a test sets, and records what was asked."""
    state = SimpleNamespace(rows=[["Oslo"], ["Paris"]], asked=[], readers=[])

    def page(api, run_id, result_id, offset=0, limit=100, *, user_context):
        state.asked.append((run_id, result_id, limit))
        state.readers.append(user_context)
        return ResultPage(request_id=run_id, result_id=result_id, columns=["value"], rows=state.rows, limit=limit)

    monkeypatch.setattr(ResultsAPI, "page", page)
    return state


def test_filter_values_runs_as_the_caller(engine, runs, server, paged):
    seed(engine, filters=[CITY, AMOUNT])

    values = engine.dashboards.filter_values("d1", "city", user_context=ANA, limit=20)

    assert values == ["Oslo", "Paris"]
    [call] = runs.calls
    assert call["user_context"] == ANA and call["answer"] is False and call["execute"] is True
    [sq] = call["plan"].decomposition.sub_queries
    assert sq.datasource_id == "ds1" and call["plan"].decomposition.combine_groups[0].operation == "standalone"
    plan = call["plan"].plans[sq.id]
    assert [(t.schema_name, t.name) for t in plan.tables] == [("main", "orders")]
    assert plan.distinct is True and plan.limit == 20 and plan.where is None
    assert [show(i.expr) for i in plan.select_items] == ["t1.city"] and plan.order_by[0].direction == "asc"
    assert paged.asked == [("tr-1", "sq1", 20)] and paged.readers == [ANA], "the rows are read as the caller"
    assert server.calls == [], "no model call"


def test_filter_values_of_a_between_filter_is_its_minimum_and_maximum(engine, runs, paged):
    seed(engine, filters=[AMOUNT])
    paged.rows = [[3, 90]]

    values = engine.dashboards.filter_values("d1", "amount", user_context=ANA)

    assert values == [3, 90]
    [sq] = runs.calls[0]["plan"].decomposition.sub_queries
    plan = runs.calls[0]["plan"].plans[sq.id]
    assert [(i.expr.func_name, show(i.expr.args[0])) for i in plan.select_items] == [
        ("MIN", "t1.amount"), ("MAX", "t1.amount")]
    assert all(i.expr.is_aggregate for i in plan.select_items) and plan.group_by == []


def test_filter_values_of_an_empty_table_is_empty(engine, runs, paged):
    seed(engine, filters=[AMOUNT, CITY])
    runs.respond = lambda call: good(rows=0)
    paged.rows = []

    assert engine.dashboards.filter_values("d1", "city", user_context=ANA) == []
    paged.rows = [[None, None]]
    assert engine.dashboards.filter_values("d1", "amount", user_context=ANA) == []


def test_filter_values_for_a_caller_without_access_raises_and_returns_nothing(engine, runs, paged):
    from test_dashboards_d2 import failed

    seed(engine, filters=[CITY])
    runs.respond = lambda call: failed("SECURITY_VIOLATION", node="logical_validator", message="No access to orders.")

    with pytest.raises(ValueError, match="City.*No access to orders"):
        engine.dashboards.filter_values("d1", "city", user_context=ANA)
    assert paged.asked == []


def test_filter_values_limits_are_checked(engine, runs, paged):
    seed(engine, filters=[CITY])

    for bad in (0, -1, 1001):
        with pytest.raises(ValueError, match="limit"):
            engine.dashboards.filter_values("d1", "city", user_context=ANA, limit=bad)
    assert runs.calls == []


# ------------------------------------------------------------------ rendering

def _sql(plan: PlanModel, dialect: str) -> str:
    adapter = SimpleNamespace(get_dialect=lambda: dialect)
    ctx = fake_runtime(ds_registry=stub_ds_registry(adapter, limits=Limits(row_limit=1000)))
    state = SubgraphExecutionState(
        trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan))
    result = GeneratorNode(ctx)(state)
    assert not result.get("errors"), result.get("errors")
    return result["generator_response"].sql_draft


@pytest.mark.parametrize("dialect", ["postgres", "tsql", "mysql"])
def test_filters_render_as_bound_literals_in_each_dialect(engine, runs, dialect):
    evil = "O'Brien'; DROP TABLE orders; --"
    paid = DashboardFilter(id="paid", label="Paid", column="ds1.main.orders.paid", kind="boolean", op="in")
    seed(engine, filters=[CITY, WHEN, AMOUNT, paid], tiles=[tile("t1")])
    engine.dashboards.refresh("d1", user_context=ANA, filters={
        "city": ["Paris", evil], "when": ["2024-01-01", "2024-12-30"], "amount": [1, 2.5], "paid": [True]})

    sql = _sql(sent(runs), dialect)
    tree = sqlglot.parse(sql, read=dialect)

    assert len(tree) == 1, "one statement: a value never ends it"
    [select] = tree
    where = select.args["where"].this
    strings = [lit.this for lit in where.find_all(exp.Literal) if lit.is_string]
    assert sorted(strings) == sorted(["Paris", evil, "2024-01-01", "2024-12-31"]), "each value is one string literal"
    assert len(list(where.find_all(exp.GTE))) == 2 and len(list(where.find_all(exp.LT))) == 1
    assert len(list(where.find_all(exp.LTE))) == 1 and len(list(where.find_all(exp.Or))) == 1 and where.find(exp.And)
    text = sql.upper().replace("[", "").replace("]", "")
    if dialect == "tsql":  # T-SQL has no TRUE or FALSE
        assert "TRUE" not in text and "T1.PAID = 1" in text
    else:
        assert "T1.PAID = TRUE" in text.replace('"', "").replace("`", "")
