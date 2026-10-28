"""``engine.results.chart`` and the ``chart`` chat tool (wave_c.md, section 5): a saved result becomes a ChartSpec
from the kinds the artifact writer recorded, refined by the plan when the run had one sub-query. No model call."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from nl2sql import NL2SQL, QueryResult, ResultNotFound, UserContext
from nl2sql.api.query_api import SubQueryResult
from nl2sql.chat_tools import ToolContext, ToolNotApplicable
from nl2sql.chat_tools.chart import ChartArgs, ChartTool
from nl2sql.conversations.models import Conversation, ToolCall, ToolStep, Turn
from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.execution.dag import ExecutionDAG, LogicalEdge, LogicalNode
from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, GroupByItem, PlanModel, SelectItem
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql.storage.local import LocalResults
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk.contracts import ResultFrame

TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"
USER = UserContext(user_id="u", roles=["admin"])


@pytest.fixture()
def store(tmp_path):
    return ArtifactStore(LocalResults(str(tmp_path)), path_template=TEMPLATE)


def _run(store, rows, request="run-1", tenant="t1", settings=None):
    """The aggregator over one scan of ``rows``: it saves the result and its manifest."""
    ref = store.create_artifact_ref(ResultFrame.from_row_dicts(rows),
                                    {"tenant_id": tenant, "request_id": request, "sub_query_id": "sq1"})
    output = SubgraphOutput(subgraph_id="sg1", sub_query=SubQuery(id="sq1", datasource_id="d", intent="i"),
                            artifact=ref)
    dag = ExecutionDAG(
        nodes=[LogicalNode(node_id="sq1", kind="scan", inputs=[]),
               LogicalNode(node_id="combine_g1", kind="combine", inputs=["sq1"],
                           attributes={"operation": "standalone", "join_keys": []})],
        edges=[LogicalEdge(edge_id="e1", from_id="sq1", to_id="combine_g1")])
    node = EngineAggregatorNode(fake_runtime(settings={"tenant_id": tenant, **(settings or {})}, artifact_store=store))
    return node(GraphState(trace_id=request, user_query="q", execution_dag=dag,
                           artifact_refs={"sq1": ref}, subgraph_outputs={"sg1": output}))


def _engine(store, tenant="t1", settings=None):
    engine = NL2SQL.__new__(NL2SQL)
    engine._runtime = fake_runtime(settings={"tenant_id": tenant, **(settings or {})}, artifact_store=store)
    return engine


MONTHS = [{"month": dt.date(2024, m, 1), "revenue": 100 * m} for m in (1, 2, 3)]


def test_the_artifact_writer_records_the_kinds_in_the_manifest(store):
    rows = [{"day": dt.date(2024, 1, 1), "n": 3, "price": Decimal("2.50"), "who": "a", "ok": True}]
    update = _run(store, rows)
    (summary,) = update["aggregator_response"].results
    assert summary.column_kinds == {"day": "temporal", "n": "number", "price": "number", "who": "text",
                                    "ok": "boolean"}
    assert _engine(store).results.manifest("run-1").results[0].column_kinds == summary.column_kinds


def test_a_trace_id_alone_charts_from_the_kinds(store):
    _run(store, MONTHS)
    spec = _engine(store).results.chart("run-1")
    assert spec.kind == "line" and spec.x.field == "month" and spec.data.trace_id == "run-1"
    assert spec.data.result_id == "combine_g1" and spec.data.truncated is False
    assert spec.title  # no question is known from a trace id; it is still named
    assert spec.takeaway == "Revenue went from 100 in 2024-01-01 to 300 in 2024-03-01 (+200%)."


def test_the_chart_reads_up_to_max_points_rows_and_says_it_was_cut(store):
    rows = [{"day": f"2024-01-{d:02d}", "n": d} for d in range(1, 9)]
    _run(store, rows)
    # "day" is text here, so a bar of 8 categories would be drawn if it fit; with max_points 5 the read is cut.
    spec = _engine(store, settings={"charts": {"max_points": 5}}).results.chart("run-1")
    assert spec.data.truncated is True and len(spec.data.rows) == 5
    assert spec.kind == "table"


def test_decimals_are_plain_numbers_in_the_chart_data(store):
    _run(store, [{"c": "a", "v": Decimal("1.5")}, {"c": "b", "v": Decimal("2.5")}])
    spec = _engine(store).results.chart("run-1")
    assert spec.kind == "bar"
    json.dumps(spec.model_dump(mode="json"))
    assert {r[1] for r in spec.data.rows} == {1.5, 2.5}


def test_a_forced_kind_the_data_cannot_draw_names_the_ones_it_can(store):
    _run(store, [{"c": "a", "v": 1}, {"c": "b", "v": 2}])
    engine = _engine(store)
    with pytest.raises(ValueError, match="bar"):
        engine.results.chart("run-1", kind="line")
    assert engine.results.chart("run-1", kind="table").kind == "table"


def test_an_unknown_result_or_run_is_not_found(store):
    _run(store, MONTHS)
    manifest = _engine(store).results.manifest("run-1")
    assert [r.node_id for r in manifest.results] == ["combine_g1"]
    assert _engine(store).results.chart("run-1", "combine_g1").kind == "line"
    with pytest.raises(ResultNotFound):
        _engine(store).results.chart("run-1", "nope")
    with pytest.raises(ResultNotFound):
        _engine(store).results.chart("no-such-run")


def test_another_tenants_run_is_not_found(store):
    _run(store, MONTHS)
    with pytest.raises(ResultNotFound):
        _engine(store, tenant="t2").results.chart("run-1")
    assert _engine(store, tenant="t2").results.chart("run-1", tenant_id="t1").kind == "line"


def _plan():
    month = Expr(kind="func", func_name="DATE_TRUNC", args=[Expr(kind="literal", value="month"),
                                                           Expr(kind="column", alias="o", column_name="created")])
    return PlanModel(select_items=[
        SelectItem(expr=month, alias="period"),
        SelectItem(expr=Expr(kind="func", func_name="SUM", is_aggregate=True,
                             args=[Expr(kind="column", alias="o", column_name="total")]), alias="revenue")],
        group_by=[GroupByItem(expr=month)])


def _query_result(sub_queries, manifest):
    return QueryResult(trace_id="run-1", sub_queries=sub_queries, result_manifest=manifest)


def test_plan_refines_only_a_single_sub_query(store):
    # "period" is an ISO string (text) in the file; the plan says it is DATE_TRUNC, so temporal, so a line.
    rows = [{"period": f"2024-0{m}-01", "revenue": m} for m in (1, 2, 3)]
    update = _run(store, rows)
    manifest = update["aggregator_response"].results
    one = SubQueryResult(id="sq1", intent="Revenue by month", plan=_plan().model_dump())
    engine = _engine(store)
    assert engine.results.chart(_query_result([one], manifest)).kind == "line"
    # "period" is text in the file, and the same rows are drawn as a bar (3 categories) without the plan
    assert engine.results.chart("run-1").kind == "bar"
    two = [one, SubQueryResult(id="sq2", plan=_plan().model_dump())]
    assert engine.results.chart(_query_result(two, manifest)).kind == "bar"


def test_a_query_result_names_its_chart_by_the_single_sub_querys_intent(store):
    update = _run(store, MONTHS)
    one = SubQueryResult(id="sq1", intent="Revenue by month")
    spec = _engine(store).results.chart(_query_result([one], update["aggregator_response"].results))
    assert spec.title == "Revenue by month"


# ---------------------------------------------------------------- the chart tool

def _context(*turns, engine=None):
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    conversation = Conversation(id="c1", title="t", created_at=now, updated_at=now, turns=list(turns))
    return ToolContext(conversation=conversation, user=USER, engine=engine)


def _turn(index, *steps):
    return Turn(index=index, at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), kind="message", text="q",
                steps=list(steps), status="answered")


def _ask(result, question="Revenue by month"):
    return ToolStep(call=ToolCall(name="ask", args={"question": question}), status="ok", question=question,
                    trace_id=result.trace_id, result=result)


def test_chart_tool_returns_a_chart_output_titled_with_the_question(store):
    update = _run(store, MONTHS)
    result = _query_result([SubQueryResult(id="sq1")], update["aggregator_response"].results)
    context = _context(_turn(1, _ask(result)), engine=_engine(store))
    output = ChartTool()(context, ChartArgs())
    assert output.kind == "chart" and output.title == "Revenue by month"
    assert output.body["kind"] == "line" and output.body["title"] == "Revenue by month"
    assert ChartTool()(context, ChartArgs(kind="table")).body["kind"] == "table"


def test_chart_tool_picks_the_latest_turn_by_default_and_a_turn_by_number(store):
    first = _run(store, MONTHS, request="run-1")
    second = _run(store, [{"c": "a", "v": 1}, {"c": "b", "v": 2}], request="run-2")
    r1 = QueryResult(trace_id="run-1", result_manifest=first["aggregator_response"].results)
    r2 = QueryResult(trace_id="run-2", result_manifest=second["aggregator_response"].results)
    context = _context(_turn(1, _ask(r1)), _turn(2, _ask(r2)), engine=_engine(store))
    assert ChartTool()(context, ChartArgs()).body["kind"] == "bar"
    assert ChartTool()(context, ChartArgs(turn=1)).body["kind"] == "line"


def test_chart_tool_on_a_turn_without_result_is_not_applicable(store):
    context = _context(_turn(1), engine=_engine(store))
    with pytest.raises(ToolNotApplicable):
        ChartTool()(context, ChartArgs())
    with pytest.raises(ToolNotApplicable):
        ChartTool()(_context(engine=_engine(store)), ChartArgs())
    with pytest.raises(ToolNotApplicable):
        ChartTool()(context, ChartArgs(turn=9))


def test_chart_tool_says_when_the_results_have_expired(store):
    result = QueryResult(trace_id="gone", result_manifest=[])
    context = _context(_turn(1, _ask(result)), engine=_engine(store))
    with pytest.raises(ToolNotApplicable, match="no longer"):
        ChartTool()(context, ChartArgs())
