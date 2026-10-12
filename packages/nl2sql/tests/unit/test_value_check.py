"""An empty result whose filter names a value the column does not have is not reported as fact.

"Which customers bought Metal?" planned as ``Genre.Name = 'Metal'`` returns no
rows, because Chinook's genre is 'Heavy Metal' (or the model misspelt 'Jazz').
The answer used to say no customer bought any. Now, when a sub-query returns
no rows, each string literal its WHERE compares a column to is probed
(``SELECT 1 ... LIMIT 1``, built with sqlglot and rendered by the adapter). A
literal that is not there gets the closest existing values: case-insensitive
first, then by similarity over a bounded DISTINCT. With a close value and a
retry left, the planner is told (a retryable ``VALUE_NOT_FOUND`` error);
otherwise the result carries a warning that says so.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nl2sql.adapters.sqlite.adapter import SqliteAdapter
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.settings import settings
from nl2sql.execution.contracts import ExecutorResponse
from nl2sql.execution.executor.sql_executor import SqlExecutorService
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.executor.node import ExecutorNode
from nl2sql.pipeline.nodes.executor.value_check import check_values, filter_literals
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph


def _col(alias, name):
    return Expr(kind="column", alias=alias, column_name=name)


def _lit(value):
    return Expr(kind="literal", value=value)


def _bin(op, left, right):
    return Expr(kind="binary", op=op, left=left, right=right)


def _plan(where, tables=None):
    return PlanModel(
        tables=tables or [TableRef(name="Genre", alias="g")],
        select_items=[SelectItem(expr=_col("g", "Name"))],
        where=where,
    )


@pytest.fixture
def adapter(tmp_path):
    path = tmp_path / "music.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE Genre (GenreId INTEGER PRIMARY KEY, Name TEXT)")
        con.executemany("INSERT INTO Genre (Name) VALUES (?)",
                        [("Rock",), ("Jazz",), ("Heavy Metal",), ("Blues",), ("Latin",)])
    adapter = SqliteAdapter(datasource_id="music", datasource_engine_type="sqlite",
                            connection_args={"type": "sqlite", "database": path.as_posix(),
                                             "options": {"read_only": True}},
                            row_limit=1000)
    return adapter


class _Spy:
    """The adapter, recording every SQL it is asked to run."""

    def __init__(self, adapter):
        self._adapter = adapter
        self.row_limit = adapter.row_limit
        self.sql = []

    def render_sql(self, expression):
        return self._adapter.render_sql(expression)

    def execute(self, request):
        self.sql.append(request.payload["sql"])
        return self._adapter.execute(request)


def _check(plan, adapter, allowed=lambda _table: True, can_retry=True):
    return check_values(plan, adapter, allowed, can_retry)


# --- which literals are checked --------------------------------------------

def test_equality_in_and_like_on_string_literals_are_checked():
    where = _bin("AND",
                 _bin("=", _col("g", "Name"), _lit("Jaz")),
                 _bin("OR",
                      _bin("IN", _col("g", "Name"), Expr(kind="func", func_name="TUPLE",
                                                         args=[_lit("Rock"), _lit("Blus")])),
                      _bin("LIKE", _lit("%Metal%"), _col("g", "Name"))))

    found = [(f.column, f.value, f.like) for f in filter_literals(_plan(where))]

    assert found == [("Name", "Jaz", False), ("Name", "Rock", False), ("Name", "Blus", False),
                     ("Name", "%Metal%", True)]


@pytest.mark.parametrize("where", [
    _bin("=", _col("g", "GenreId"), _lit(3)),
    _bin("=", _col("g", "GenreId"), _lit("3")),
    _bin("!=", _col("g", "Name"), _lit("Rock")),
    Expr(kind="unary", op="NOT", expr=_bin("=", _col("g", "Name"), _lit("Rock"))),
    _bin("=", Expr(kind="func", func_name="LOWER", args=[_col("g", "Name")]), _lit("rock")),
    _bin("=", _col("x", "Name"), _lit("Rock")),
], ids=["number", "numeric string", "not equal", "negated", "inside a function", "unknown alias"])
def test_anything_else_is_left_alone(where):
    assert filter_literals(_plan(where)) == []


# --- what the probe finds ---------------------------------------------------

def test_a_value_that_exists_says_nothing(adapter):
    assert _check(_plan(_bin("=", _col("g", "Name"), _lit("Jazz"))), adapter) == []


def test_a_misspelt_value_asks_the_planner_with_the_closest_values(adapter):
    [error] = _check(_plan(_bin("=", _col("g", "Name"), _lit("Jaz"))), adapter)

    assert error.error_code == ErrorCode.VALUE_NOT_FOUND
    assert error.severity == ErrorSeverity.ERROR and error.is_retryable
    assert "'Jaz'" in error.message and "'Jazz'" in error.message


def test_a_value_in_the_wrong_case_finds_the_stored_spelling_first(adapter):
    [error] = _check(_plan(_bin("=", _col("g", "Name"), _lit("heavy metal"))), adapter)

    assert "'Heavy Metal'" in error.message


def test_a_like_pattern_that_matches_nothing_is_checked_too(adapter):
    [error] = _check(_plan(_bin("LIKE", _col("g", "Name"), _lit("Blus%"))), adapter)

    assert "'Blus%'" in error.message and "'Blues'" in error.message


def test_nothing_close_is_a_warning_that_says_the_value_does_not_exist(adapter):
    [warning] = _check(_plan(_bin("=", _col("g", "Name"), _lit("Polka"))), adapter)

    assert warning.error_code == ErrorCode.VALUE_NOT_FOUND
    assert warning.severity == ErrorSeverity.WARNING
    assert warning.message == "There is no Genre with Name 'Polka'."


def test_without_a_retry_left_a_close_value_is_a_warning_naming_it(adapter):
    [warning] = _check(_plan(_bin("=", _col("g", "Name"), _lit("Jaz"))), adapter, can_retry=False)

    assert warning.severity == ErrorSeverity.WARNING
    assert warning.message == "There is no Genre with Name 'Jaz'; the closest is 'Jazz'."


# --- what the probe may touch -----------------------------------------------

def test_a_table_the_role_may_not_read_is_never_probed(adapter):
    spy = _Spy(adapter)

    errors = _check(_plan(_bin("=", _col("g", "Name"), _lit("Jaz"))), spy, allowed=lambda _table: False)

    assert errors == [] and spy.sql == []


def test_every_probe_is_a_bounded_read(adapter):
    spy = _Spy(adapter)
    spy.row_limit = 3

    _check(_plan(_bin("=", _col("g", "Name"), _lit("Polka"))), spy)

    assert spy.sql, "nothing was probed"
    for sql in spy.sql:
        assert sql.upper().startswith("SELECT")
        limit = int(sql.upper().rsplit("LIMIT", 1)[1])
        assert limit <= 3


def test_a_probe_the_database_refuses_claims_nothing(adapter):
    plan = _plan(_bin("=", _col("g", "Nme"), _lit("Jaz")))

    assert _check(plan, adapter) == []


# --- wired in: the executor runs it, the subgraph retries, the answer says it --

def _executor(adapter, monkeypatch, row_count, allowed=("*",)):
    def execute(self, request):
        return ExecutorResponse(executor_name="sql_executor", subgraph_name="sql_agent", node_id="sq1",
                                trace_id="t", tenant_id="t1", metrics={"row_count": row_count})

    monkeypatch.setattr(SqlExecutorService, "execute", execute)
    registry = SimpleNamespace(supports=lambda *_a: True, get_adapter=lambda _id: adapter)
    ctx = SimpleNamespace(ds_registry=registry, tenant_id="t1",
                          rbac=SimpleNamespace(get_allowed_tables=lambda _user: list(allowed)))
    return ExecutorNode(ctx)


def _sub_state(plan, **kwargs):
    return SubgraphExecutionState(
        trace_id="t", subgraph_name="sql_agent",
        sub_query=SubQuery(id="sq1", datasource_id="music", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
        generator_response=GeneratorResponse(sql_draft="SELECT 1"),
        **kwargs,
    )


JAZ = _plan(_bin("=", _col("g", "Name"), _lit("Jaz")))


def test_the_executor_checks_the_values_of_an_empty_result(adapter, monkeypatch):
    result = _executor(adapter, monkeypatch, row_count=0)(_sub_state(JAZ))

    [error] = result["errors"]
    assert error.error_code == ErrorCode.VALUE_NOT_FOUND and error.severity == ErrorSeverity.ERROR
    assert error.details == {"attempt": 0}  # what the SQL agent routes on
    # The execution itself succeeded: the plan cache and the status read this.
    assert result["executor_response"].errors == []


def test_a_result_with_rows_is_not_probed(adapter, monkeypatch):
    spy = _Spy(adapter)

    result = _executor(spy, monkeypatch, row_count=2)(_sub_state(JAZ))

    assert result["errors"] == [] and spy.sql == []


def test_the_role_s_tables_bound_the_probe(adapter, monkeypatch):
    spy = _Spy(adapter)

    result = _executor(spy, monkeypatch, row_count=0, allowed=["music.Track"])(_sub_state(JAZ))

    assert result["errors"] == [] and spy.sql == []


def test_only_one_retry_is_spent_on_values(adapter, monkeypatch):
    earlier = PipelineError(node="executor", message="No Genre.Name value is 'Jz'", severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.VALUE_NOT_FOUND)

    result = _executor(adapter, monkeypatch, row_count=0)(_sub_state(JAZ, errors=[earlier], retry_count=1))

    [warning] = result["errors"]
    assert warning.severity == ErrorSeverity.WARNING
    assert "the closest is 'Jazz'" in warning.message


def test_the_subgraph_retries_on_a_missing_value_and_ends_on_a_warning(monkeypatch):
    monkeypatch.setattr(settings, "sql_agent_retry_base_delay_sec", 0)
    monkeypatch.setattr(settings, "sql_agent_retry_jitter_sec", 0)
    calls = {"planner": 0, "executor": 0}

    def planner(state):
        calls["planner"] += 1
        return {"ast_planner_response": ASTPlannerResponse(plan=JAZ)}

    def executor(state):
        calls["executor"] += 1
        severity = ErrorSeverity.ERROR if calls["executor"] == 1 else ErrorSeverity.WARNING
        response = ExecutorResponse(executor_name="sql_executor", subgraph_name="sql_agent", node_id="sq1",
                                    trace_id="t", tenant_id="t1", metrics={"row_count": 0})
        return {"executor_response": response, "errors": [PipelineError(
            node="executor", message="Jaz", severity=severity, error_code=ErrorCode.VALUE_NOT_FOUND,
            details={"attempt": state.retry_count})]}

    nodes = {
        "SchemaRetrieverNode": lambda s: {"relevant_tables": []},
        "ASTPlannerNode": planner,
        "LogicalValidatorNode": lambda s: {"logical_validator_response": LogicalValidatorResponse(errors=[])},
        "GeneratorNode": lambda s: {"generator_response": GeneratorResponse(sql_draft="SELECT 1")},
        "ExecutorNode": executor,
        "RefinerNode": lambda s: {},
    }
    for name, node in nodes.items():
        monkeypatch.setattr(f"nl2sql.pipeline.subgraphs.sql_agent.{name}", lambda _ctx, node=node: node)

    result = build_sql_agent_graph(SimpleNamespace()).invoke(
        SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q")))

    assert calls == {"planner": 2, "executor": 2}
    assert result["retry_count"] == 1


def test_the_answer_is_told_that_the_value_does_not_exist():
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = AnswerSynthesizerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = AggregatedResponse(summary="No customers bought Polka.", format_type="text",
                                                        content="None.")
    missing = {"node": "executor", "message": "There is no Genre with Name 'Polka'.",
               "error_code": "VALUE_NOT_FOUND", "severity": "WARNING", "sub_query_id": "sq1"}
    recovered = {**missing, "message": "No Genre.Name value is 'Jaz'", "severity": "ERROR"}

    result = node(GraphState(user_query="Who bought Polka?", warnings=[missing, recovered],
                             aggregator_response=AggregatorResponse(terminal_results={"sq1": []})))

    notes = node.chain.invoke.call_args.args[0]["data_notes"]
    assert "There is no Genre with Name 'Polka'." in notes
    assert "Jaz" not in notes  # a retry recovered from that one
    answer = result["answer_synthesizer_response"].final_answer
    assert answer["warnings"] == ["There is no Genre with Name 'Polka'."]
