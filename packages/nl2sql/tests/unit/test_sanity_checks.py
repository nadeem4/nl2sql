"""The answer sanity checks after a sub-query runs (Wave A, section 11, D32 and D34).

A join on a key that is not unique on the other side repeats the rows of the
one side, so ``COUNT``, ``SUM`` or ``AVG`` over one of its columns counts each
row several times. A probe built from the plan (``COUNT(*)`` against
``COUNT(DISTINCT <primary key>)`` over the plan's FROM, joins and WHERE),
rendered by the adapter, confirms it. Under ``COUNT`` it is retried once (the
plan can say ``COUNT(DISTINCT ...)``); under ``SUM`` or ``AVG`` it is a warning.
A result column whose every value is null is a warning. The filter-value check
(``VALUE_NOT_FOUND``) moved here from ``executor/value_check.py``; its own
tests are in ``test_sanity_values.py``.
"""
from __future__ import annotations

import sqlite3

import polars as pl
import pytest

from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql.api.query_api import result_from_state
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError, RunWarning
from nl2sql.execution.contracts import ExecutorResponse
from nl2sql.execution.executor.sql_executor import SqlExecutorService
from nl2sql.hooks import Allow
from nl2sql.hooks.runner import HookBinding, HookRunner
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse, Expr, GroupByItem, JoinSpec, PlanModel, SelectItem, TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.executor.node import ExecutorNode
from nl2sql.pipeline.nodes.executor.sanity import fanout, nulls
from nl2sql.pipeline.nodes.executor.sanity.values import check_values
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph
from nl2sql.testing.runtime import fake_runtime, stub_ds_registry
from nl2sql_sdk import Limits


def _col(alias, name):
    return Expr(kind="column", alias=alias, column_name=name)


def _lit(value):
    return Expr(kind="literal", value=value)


def _bin(op, left, right):
    return Expr(kind="binary", op=op, left=left, right=right)


def _agg(name, alias, column, distinct=False):
    return Expr(kind="func", func_name=name, args=[_col(alias, column)], is_aggregate=True, distinct=distinct)


def _join(left, right, left_col, right_col, join_type="inner"):
    return JoinSpec(left_alias=left, right_alias=right, join_type=join_type,
                    condition=_bin("=", _col(left, left_col), _col(right, right_col)))


CUSTOMER = TableRef(name="Customer", alias="c")
INVOICE = TableRef(name="Invoice", alias="i")
TICKET = TableRef(name="Ticket", alias="t")
# What the schema retriever gives the subgraph: each table's primary key.
TABLES = [
    Table(name="Customer", primary_key=["CustomerId"], columns=[Column(name="CustomerId"), Column(name="Credit")]),
    Table(name="Invoice", primary_key=["InvoiceId"], columns=[Column(name="InvoiceId"), Column(name="CustomerId"),
                                                              Column(name="Total")]),
    Table(name="Ticket", primary_key=["TicketId"], columns=[Column(name="TicketId"), Column(name="CustomerId")]),
]


def _plan(select, tables=(CUSTOMER, INVOICE), joins=None, where=None):
    return PlanModel(tables=list(tables), select_items=[SelectItem(expr=select)],
                     joins=joins if joins is not None else [_join("c", "i", "CustomerId", "CustomerId")],
                     where=where)


@pytest.fixture
def adapter(tmp_path):
    path = tmp_path / "shop.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, Credit REAL)")
        con.execute("CREATE TABLE Invoice (InvoiceId INTEGER PRIMARY KEY, CustomerId INTEGER, Total REAL)")
        con.execute("CREATE TABLE Ticket (TicketId INTEGER PRIMARY KEY, CustomerId INTEGER)")
        con.executemany("INSERT INTO Customer VALUES (?, ?)", [(1, 10.0), (2, 20.0)])
        con.executemany("INSERT INTO Invoice VALUES (?, ?, ?)", [(1, 1, 5.0), (2, 1, 6.0), (3, 2, 7.0)])
        con.executemany("INSERT INTO Ticket VALUES (?, ?)", [(1, 1), (2, 1), (3, 2)])
    return SqliteAdapter("shop", SqliteConnection(database=path.as_posix()), Limits(row_limit=1000))


class _Spy:
    """The adapter, recording every SQL it runs and every tree it renders."""

    def __init__(self, adapter):
        self._adapter = adapter
        self.sql = []
        self.rendered = 0

    def get_dialect(self):
        return self._adapter.get_dialect()

    def render_sql(self, expression):
        self.rendered += 1
        return self._adapter.render_sql(expression)

    def execute(self, request):
        self.sql.append(request.payload["sql"])
        return self._adapter.execute(request)


def _fan_out(plan, adapter, can_retry=True, allowed=lambda _t: True, tables=TABLES):
    return fanout.check_fan_out(plan, adapter, allowed, tables, can_retry)


# --- join fan-out ------------------------------------------------------------

def test_count_fan_out_is_confirmed_by_the_probe_and_retried_once(adapter):
    plan = _plan(_agg("COUNT", "c", "CustomerId"))

    [error] = _fan_out(plan, adapter)
    assert error.error_code == ErrorCode.JOIN_FAN_OUT
    assert error.severity == ErrorSeverity.ERROR and error.is_retryable
    assert "distinct" in error.message.lower() and "Customer" in error.message

    # Without a retry left it is a warning instead.
    [warning] = _fan_out(plan, adapter, can_retry=False)
    assert warning.error_code == ErrorCode.JOIN_FAN_OUT and warning.severity == ErrorSeverity.WARNING

    # Already counting distinct keys: nothing to say, nothing probed.
    spy = _Spy(adapter)
    assert _fan_out(_plan(_agg("COUNT", "c", "CustomerId", distinct=True)), spy) == [] and spy.sql == []


def test_the_subgraph_retries_a_count_fan_out_once():
    calls = {"planner": 0, "executor": 0}

    def executor(state):
        calls["executor"] += 1
        severity = ErrorSeverity.ERROR if calls["executor"] == 1 else ErrorSeverity.WARNING
        response = ExecutorResponse(executor_name="sql_executor", subgraph_name="sql_agent", node_id="sq1",
                                    trace_id="t", tenant_id="t1", metrics={"row_count": 2})
        return {"executor_response": response, "errors": [PipelineError(
            node="executor", message="fan-out", severity=severity, error_code=ErrorCode.JOIN_FAN_OUT,
            details={"attempt": state.retry_count})]}

    def planner(state):
        calls["planner"] += 1
        return {"ast_planner_response": ASTPlannerResponse(plan=_plan(_agg("COUNT", "c", "CustomerId")))}

    graph = _graph_with(planner, executor)
    result = graph.invoke(SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1",
                                                                                   intent="q")))

    assert calls == {"planner": 2, "executor": 2}
    assert result["retry_count"] == 1


def _graph_with(planner, executor, monkeypatch=None):
    import nl2sql.pipeline.subgraphs.sql_agent as sql_agent

    nodes = {
        "SchemaRetrieverNode": lambda s: {"relevant_tables": []},
        "ASTPlannerNode": planner,
        "LogicalValidatorNode": lambda s: {"logical_validator_response": LogicalValidatorResponse(errors=[])},
        "GeneratorNode": lambda s: {"generator_response": GeneratorResponse(sql_draft="SELECT 1")},
        "ExecutorNode": executor,
        "RefinerNode": lambda s: {},
    }
    saved = {name: getattr(sql_agent, name) for name in nodes}
    try:
        for name, node in nodes.items():
            setattr(sql_agent, name, lambda _ctx, node=node: node)
        return build_sql_agent_graph(
            fake_runtime(settings={"pipeline": {"retries": {"base_delay_s": 0, "jitter_s": 0}}}))
    finally:
        for name, node in saved.items():
            setattr(sql_agent, name, node)


def test_sum_fan_out_is_a_warning_only(adapter):
    [warning] = _fan_out(_plan(_agg("SUM", "c", "Credit")), adapter, can_retry=True)

    assert warning.error_code == ErrorCode.JOIN_FAN_OUT and warning.severity == ErrorSeverity.WARNING
    assert "Customer" in warning.message and "Invoice" in warning.message


def test_many_to_many_sum_is_a_join_fan_out(adapter):
    # Invoices and tickets both hang off a customer: each invoice repeats once per ticket.
    plan = _plan(_agg("SUM", "i", "Total"), tables=(CUSTOMER, INVOICE, TICKET),
                 joins=[_join("c", "i", "CustomerId", "CustomerId"), _join("c", "t", "CustomerId", "CustomerId")])

    [warning] = _fan_out(plan, adapter)

    assert warning.error_code == ErrorCode.JOIN_FAN_OUT and warning.severity == ErrorSeverity.WARNING
    assert "Invoice" in warning.message


def test_unique_join_is_not_probed(adapter):
    spy = _Spy(adapter)
    # Each invoice has one customer: summing invoices over that join counts each once.
    plan = _plan(_agg("SUM", "i", "Total"), tables=(INVOICE, CUSTOMER),
                 joins=[_join("i", "c", "CustomerId", "CustomerId")])

    assert _fan_out(plan, spy) == [] and spy.sql == []


def test_a_table_with_no_known_key_is_not_probed(adapter):
    spy = _Spy(adapter)
    tables = [t.model_copy(update={"primary_key": []}) for t in TABLES]

    assert _fan_out(_plan(_agg("COUNT", "c", "CustomerId")), spy, tables=tables) == [] and spy.sql == []


def test_an_unconfirmed_fan_out_says_nothing(adapter):
    # Customer 2 has one invoice: filtered to it, the join repeats nothing.
    plan = _plan(_agg("COUNT", "c", "CustomerId"), where=_bin("=", _col("c", "CustomerId"), _lit(2)))

    assert _fan_out(plan, adapter) == []


def test_probe_is_rendered_by_the_adapter(adapter):
    spy = _Spy(adapter)

    _fan_out(_plan(_agg("SUM", "c", "Credit")), spy)

    [sql] = spy.sql
    assert spy.rendered == 1
    upper = sql.upper()
    assert upper.startswith("SELECT 1") and "NOT C.CUSTOMERID IS NULL" in upper and "HAVING COUNT(*) > 1" in upper
    # The key keeps its case as reflected: a case-sensitive collation finds it.
    assert "CustomerId" in sql and "customerid" not in sql


def test_null_extended_rows_of_an_outer_join_are_not_a_fan_out(tmp_path):
    # The review's repro: no invoice is repeated (each customer has at most one
    # ticket), so COUNT(i.InvoiceId) = 2 is right, though the LEFT joins give 4 rows.
    path = tmp_path / "outer.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, Credit REAL)")
        con.execute("CREATE TABLE Invoice (InvoiceId INTEGER PRIMARY KEY, CustomerId INTEGER, Total REAL)")
        con.execute("CREATE TABLE Ticket (TicketId INTEGER PRIMARY KEY, CustomerId INTEGER)")
        con.executemany("INSERT INTO Customer VALUES (?, ?)", [(1, 1.0), (2, 2.0), (3, 3.0)])
        con.executemany("INSERT INTO Invoice VALUES (?, ?, ?)", [(1, 1, 5.0), (2, 1, 6.0)])
        con.executemany("INSERT INTO Ticket VALUES (?, ?)", [(1, 1), (2, 2)])
    outer = SqliteAdapter("outer", SqliteConnection(database=path.as_posix()), Limits(row_limit=1000))
    plan = _plan(_agg("COUNT", "i", "InvoiceId"), tables=(CUSTOMER, INVOICE, TICKET),
                 joins=[_join("c", "i", "CustomerId", "CustomerId", "left"),
                        _join("c", "t", "CustomerId", "CustomerId", "left")])

    assert _fan_out(plan, outer) == []


def test_a_group_on_the_many_side_is_not_a_fan_out(adapter):
    # The review's repro: one row per ticket, so every count is 1 and right.
    plan = PlanModel(tables=[CUSTOMER, TICKET], joins=[_join("c", "t", "CustomerId", "CustomerId")],
                     select_items=[SelectItem(expr=_col("t", "TicketId")),
                                   SelectItem(expr=_agg("COUNT", "c", "CustomerId"))],
                     group_by=[GroupByItem(expr=_col("t", "TicketId"))])

    assert _fan_out(plan, adapter) == []


def test_a_group_that_still_repeats_rows_is_a_fan_out(adapter):
    # Grouped by customer, each customer's credit is still added once per invoice.
    plan = PlanModel(tables=[CUSTOMER, INVOICE], joins=[_join("c", "i", "CustomerId", "CustomerId")],
                     select_items=[SelectItem(expr=_col("c", "CustomerId")),
                                   SelectItem(expr=_agg("SUM", "c", "Credit"))],
                     group_by=[GroupByItem(expr=_col("c", "CustomerId"))])

    [warning] = _fan_out(plan, adapter)
    assert warning.error_code == ErrorCode.JOIN_FAN_OUT


def test_denied_table_is_never_probed(adapter):
    spy = _Spy(adapter)

    found = _fan_out(_plan(_agg("SUM", "c", "Credit")), spy, allowed=lambda table: table.name != "Invoice")

    assert found == [] and spy.sql == []


# --- all-null columns ----------------------------------------------------------

def test_all_null_column_is_a_warning_only():
    frame = pl.DataFrame({"Country": ["NO", "SE"], "Manager": [None, None]}, schema={"Country": pl.Utf8,
                                                                                      "Manager": pl.Utf8})

    [warning] = nulls.check_nulls(frame.lazy())

    assert warning.error_code == ErrorCode.ALL_NULL_COLUMN and warning.severity == ErrorSeverity.WARNING
    assert "Manager" in warning.message and "NO" not in warning.message
    assert nulls.check_nulls(pl.DataFrame({"a": [1, None]}).lazy()) == []


# --- missing values: sample values first --------------------------------------------

def test_closest_value_comes_from_sample_values_without_a_query(tmp_path):
    path = tmp_path / "music.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE Genre (GenreId INTEGER PRIMARY KEY, Name TEXT)")
        con.executemany("INSERT INTO Genre (Name) VALUES (?)", [("Rock",), ("Jazz",)])
    spy = _Spy(SqliteAdapter("music", SqliteConnection(database=path.as_posix()), Limits(row_limit=1000)))
    plan = PlanModel(tables=[TableRef(name="Genre", alias="g")], select_items=[SelectItem(expr=_col("g", "Name"))],
                     where=_bin("=", _col("g", "Name"), _lit("Jaz")))

    [error] = check_values(plan, spy, lambda _t: True, True, 1000,
                           samples=lambda table, column: ["Rock", "Jazz"] if (table, column) == ("Genre", "Name")
                           else None)

    assert "'Jazz'" in error.message
    assert len(spy.sql) == 1, "only the existence probe ran; the closest value came from the samples"


# --- the executor: the switch, the verified path, the event ---------------------------

def _executor(adapter, monkeypatch, row_count, settings=None, hooks=None):
    def execute(self, request):
        return ExecutorResponse(executor_name="sql_executor", subgraph_name="sql_agent", node_id="sq1",
                                trace_id="t", tenant_id="t1", metrics={"row_count": row_count})

    monkeypatch.setattr(SqlExecutorService, "execute", execute)
    registry = stub_ds_registry(adapter, limits=Limits(row_limit=1000), supports=lambda *_a: True)
    ctx = fake_runtime(settings={"tenant_id": "t1", **(settings or {})}, ds_registry=registry,
                       policies={"default_role": "r", "roles": {"r": {"description": "d", "allow": ["*"]}}},
                       **({"hooks": hooks} if hooks is not None else {}))
    return ExecutorNode(ctx)


def _state(plan):
    return SubgraphExecutionState(
        trace_id="t", subgraph_name="sql_agent", relevant_tables=TABLES,
        sub_query=SubQuery(id="sq1", datasource_id="shop", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
        generator_response=GeneratorResponse(sql_draft="SELECT 1"),
    )


MISSING = PlanModel(tables=[CUSTOMER], select_items=[SelectItem(expr=_col("c", "Credit"))],
                    where=_bin("=", _col("c", "Credit"), _lit("lots")))


def test_sanity_checks_off(adapter, monkeypatch):
    spy = _Spy(adapter)

    result = _executor(spy, monkeypatch, 0, {"pipeline": {"sanity_checks": False}})(_state(MISSING))

    assert result["errors"] == [] and spy.sql == []


def test_no_probe_runs_when_a_hook_gates_the_sql(adapter, monkeypatch):
    # An operator gating SQL at execute.before never has unseen SQL run: the probes are skipped.
    spy = _Spy(adapter)
    gate = HookRunner([HookBinding(name="gate", hook=lambda ctx: Allow(), points=frozenset({"execute.before"}))])

    result = _executor(spy, monkeypatch, 0, hooks=gate)(_state(MISSING))

    assert result["errors"] == [] and spy.sql == []


def test_a_hook_elsewhere_leaves_the_probes_on(adapter, monkeypatch):
    spy = _Spy(adapter)
    other = HookRunner([HookBinding(name="other", hook=lambda ctx: Allow(), points=frozenset({"answer.after"}))])

    _executor(spy, monkeypatch, 0, hooks=other)(_state(MISSING))

    assert spy.sql


def test_each_finding_publishes_sanity_flagged(adapter, monkeypatch):
    published = []
    monkeypatch.setattr("nl2sql.pipeline.nodes.executor.node.publish",
                        lambda name, data=None: published.append((name, data)))

    result = _executor(adapter, monkeypatch, 0)(_state(MISSING))

    [error] = result["errors"]
    assert published == [("sanity.flagged", {"code": "VALUE_NOT_FOUND", "sub_query_id": "sq1",
                                             "retry": error.severity == ErrorSeverity.ERROR})]


def test_verified_query_is_not_checked():
    # A verified query runs in the curated matcher, through the executor
    # service and never the executor node, so no sanity check sees it.
    import inspect

    import nl2sql.pipeline.nodes.curated_matcher.node as curated

    source = inspect.getsource(curated)
    assert "ExecutorNode" not in source and "sanity" not in source


# --- typed warnings (D34) ------------------------------------------------------------------

def test_warnings_are_typed():
    state = {
        "warnings": [RunWarning(code="RESULT_TRUNCATED", node="aggregator", message="cut", sub_query_id="sq1")],
        "errors": [PipelineError(node="executor", message="There is no Genre with Name 'Polka'.",
                                 severity=ErrorSeverity.WARNING, error_code=ErrorCode.VALUE_NOT_FOUND)],
    }

    result = result_from_state(state)

    assert all(isinstance(w, RunWarning) for w in result.warnings)
    assert [(w.code, w.node) for w in result.warnings] == [("RESULT_TRUNCATED", "aggregator"),
                                                          ("VALUE_NOT_FOUND", "executor")]
    assert result.model_dump()["warnings"][0] == {"code": "RESULT_TRUNCATED", "node": "aggregator",
                                                  "message": "cut", "sub_query_id": "sq1", "detail": {}}


def test_a_recovered_error_is_a_warning_marked_recovered():
    error = PipelineError(node="validator", message="no such column", severity=ErrorSeverity.ERROR,
                          error_code=ErrorCode.COLUMN_NOT_FOUND, details={"secret": "x"})

    warning = RunWarning.of(error, "sq1")

    assert warning == RunWarning(code="COLUMN_NOT_FOUND", node="validator", message="no such column",
                                 sub_query_id="sq1", detail={"recovered": True})
    assert RunWarning.of(error.model_copy(update={"severity": ErrorSeverity.WARNING})).detail == {}
