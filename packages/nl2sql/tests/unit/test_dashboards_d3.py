"""The starter dashboard (docs/architecture/wave_c.md, section 6; row D3).

``generate`` reads the datasource's indexed snapshot and the glossary, chooses charts from the column roles, builds
each one's typed plan in code, runs it as the caller and keeps the ones that answered. Here the snapshot is hand-built
(the vector store's ``snapshot`` is replaced) and ``run_query`` returns canned results, so a test can say what was
asked, as whom and what came back; the real pipeline is held by ``tests/e2e/test_dashboards_fake_llm.py``.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pytest
from nl2sql_sdk.knowledge import ColumnKnowledge, DatasourceKnowledge
from nl2sql_sdk.schema import (
    ColumnContract, ColumnMetadata, ColumnStatistics, ForeignKeyContract, SchemaContract, SchemaMetadata,
    SchemaSnapshot, TableContract, TableMetadata, TableRef,
)

from nl2sql import NL2SQL, SuppliedPlan, UserContext
from nl2sql.api.query_api import QueryAPI, QueryResult
from nl2sql.api.results_api import ResultsAPI
from nl2sql.charts.spec import ChartData, ChartSpec, Encoding
from nl2sql.common.errors import DashboardNotFound, NoTiles, NotIndexed
from nl2sql.dashboards.layout import arrange
from nl2sql.dashboards.models import ChartPlan, Tile, TileLayout
from nl2sql.dashboards.store import DashboardStore
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.indexing.retrieval import Retriever
from nl2sql.knowledge.merge import Knowledge
from nl2sql.pipeline.nodes.ast_planner.functions import ALLOWED_FUNCTIONS
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse
from nl2sql.testing.config import make_config
from nl2sql.testing.fake_llm import FakeLLMServer

ANA = UserContext(user_id="ana", tenant_id="acme", roles=["analyst"])
BOB = UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])
LIMITED = UserContext(user_id="lee", tenant_id="acme", roles=["limited"])
NOTHING = UserContext(user_id="nia", tenant_id="acme", roles=["blind"])
ROLES = {"analyst": {"description": "Reads", "allow": ["*"]},
         "limited": {"description": "Reads orders only", "allow": ["ds1"], "deny": ["ds1.main.customers"]},
         "blind": {"description": "Reaches ds1, no table", "allow": ["ds1"],
                   "deny": ["ds1.main.customers", "ds1.main.orders"]}}


# ------------------------------------------------------------------ a hand-built snapshot

def _stats(distinct: int, *, nulls: float = 0.0, high: Any = None) -> ColumnStatistics:
    return ColumnStatistics(null_percentage=nulls, distinct_count=distinct, max_value=high)


def _table(name: str, rows: int, columns: List[tuple], fks: Optional[List[tuple]] = None):
    """``columns``: ``(name, type, primary key?, ColumnStatistics or None)``."""
    ref = TableRef(schema_name="main", table_name=name)
    contract = TableContract(
        table=ref, columns={c[0]: ColumnContract(name=c[0], data_type=c[1], is_primary_key=c[2]) for c in columns},
        foreign_keys=[ForeignKeyContract(constrained_columns=[fk[0]],
                                         referred_table=TableRef(schema_name="main", table_name=fk[1]),
                                         referred_columns=[fk[2]]) for fk in fks or []])
    metadata = TableMetadata(table=ref, row_count=rows, columns={
        c[0]: ColumnMetadata(statistics=c[3]) for c in columns if c[3] is not None})
    return contract.full_name, contract, metadata


def snapshot_of(*tables) -> SchemaSnapshot:
    return SchemaSnapshot(
        contract=SchemaContract(datasource_id="ds1", engine_type="sqlite", tables={t[0]: t[1] for t in tables}),
        metadata=SchemaMetadata(datasource_id="ds1", engine_type="sqlite", tables={t[0]: t[2] for t in tables}))


def customers():
    return _table("customers", 59, [
        ("CustomerId", "INTEGER", True, None), ("Country", "TEXT", False, _stats(5)),
        ("Name", "TEXT", False, _stats(59))])


def orders():
    return _table("orders", 1000, [
        ("OrderId", "INTEGER", True, None), ("CustomerId", "INTEGER", False, None),
        ("Revenue", "DECIMAL(10,2)", False, _stats(900)), ("Quantity", "INTEGER", False, _stats(20, nulls=10.0)),
        ("OrderDate", "DATE", False, _stats(300, high="2024-06-15")), ("Status", "TEXT", False, _stats(4)),
        ("Score", "INTEGER", False, _stats(100, nulls=20.0)), ("Notes", "TEXT", False, _stats(800))], fks=[("CustomerId", "customers", "CustomerId")])


SNAPSHOT = snapshot_of(customers(), orders())


# ------------------------------------------------------------------ the canned runs

def good(trace: str = "tr-1") -> QueryResult:
    return QueryResult(status="success", trace_id=trace, result_manifest=[
        ResultSummary(node_id="sq1", columns=["a", "n"], row_count=3, column_kinds={"a": "text", "n": "number"})])


def empty(trace: str = "tr-0") -> QueryResult:
    return QueryResult(status="success", trace_id=trace, result_manifest=[
        ResultSummary(node_id="sq1", columns=["a", "n"], row_count=0, column_kinds={"a": "text", "n": "number"})])


def failed() -> QueryResult:
    return QueryResult(status="error", trace_id="tr-x", errors=[
        {"node": "executor", "error_code": "EXECUTION_ERROR", "message": "It failed.", "severity": "ERROR"}])


class Runs:
    """Stands in for ``QueryAPI.run_query`` and ``ResultsAPI.chart``: records each call, answers ``respond(call)``."""

    def __init__(self, monkeypatch) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.respond: Callable[[Dict[str, Any]], QueryResult] = lambda call: good()
        runs = self

        def run_query(api: QueryAPI, question: str, **kwargs: Any) -> QueryResult:
            runs.calls.append({"question": question, **kwargs})
            return runs.respond(runs.calls[-1])

        def chart(api: ResultsAPI, result: QueryResult, result_id=None, *, kind=None, user_context=None) -> ChartSpec:
            return ChartSpec(kind="bar", title="Result", reason="a category and a number",
                             x=Encoding(field="a", kind="text", title="A"), y=[Encoding(field="n", kind="number", title="N")],
                             data=ChartData(trace_id=result.trace_id, result_id="sq1", columns=["a", "n"],
                                            rows=[["x", 1]], truncated=False))

        monkeypatch.setattr(QueryAPI, "run_query", run_query)
        monkeypatch.setattr(ResultsAPI, "chart", chart)

    def executed(self) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["execute"]]


# ------------------------------------------------------------------ the engine

@pytest.fixture
def server():
    fake = FakeLLMServer([]).start()
    yield fake
    fake.stop()


@pytest.fixture
def engine(tmp_path, monkeypatch, server):
    monkeypatch.setenv("DASHBOARD_FAKE_KEY", "sk-fake")
    config = make_config(tmp_path, policies={"roles": ROLES},
                         llm={"provider": "openai", "model": "gpt-dashboard-test", "api_key": "${env:DASHBOARD_FAKE_KEY}",
                              "base_url": server.base_url})
    built = NL2SQL(config)
    monkeypatch.setattr(Retriever, "snapshot", lambda self, datasource_id: SNAPSHOT)
    yield built
    built.close()


@pytest.fixture
def runs(monkeypatch):
    return Runs(monkeypatch)


def index(engine: NL2SQL, monkeypatch, snapshot: Optional[SchemaSnapshot]) -> None:
    monkeypatch.setattr(Retriever, "snapshot", lambda self, datasource_id: snapshot)


def say(engine: NL2SQL, monkeypatch, **columns: str) -> None:
    """The glossary says these roles: ``say(engine, mp, **{"orders.Revenue": "identifier"})``."""
    knowledge = DatasourceKnowledge(columns={name: ColumnKnowledge(role=role) for name, role in columns.items()})
    monkeypatch.setattr(Knowledge, "get", lambda self, datasource_id: knowledge)


def questions(dashboard) -> List[str]:
    return [t.question for t in dashboard.tiles]


# ------------------------------------------------------------------ what it chooses

def test_the_four_templates_give_these_questions_in_this_order(engine, runs):
    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    assert questions(dashboard) == [
        "How many rows are in orders?", "What is the total revenue?", "What is the total quantity?",
        "What is the total revenue by month over the last 12 months of data?",
        "What is the total quantity by month over the last 12 months of data?",
        "Which 10 status have the highest total revenue?", "Which 10 status have the highest total quantity?",
        "How many orders rows are there per status?", "How many customers rows are there per country?"]
    assert [t.title for t in dashboard.tiles][:3] == ["Number of orders", "Total revenue", "Total quantity"]
    assert "Top 10 status by revenue" in [t.title for t in dashboard.tiles]
    assert "Orders by status" in [t.title for t in dashboard.tiles]


def test_the_plans_are_typed_and_use_only_allowed_functions(engine, runs):
    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    for tile in dashboard.tiles:
        ChartPlan.model_validate(tile.plan.model_dump())
        DecomposerResponse.model_validate(tile.plan.decomposition)
        for body in tile.plan.plans.values():
            plan = PlanModel.model_validate(body)
            names = {n for n in _function_names(plan.model_dump())}
            assert names <= ALLOWED_FUNCTIONS, names
            assert [t.name for t in plan.tables][0] in {"orders", "customers"}


def _function_names(node: Any):
    if isinstance(node, dict):
        if node.get("kind") == "func":
            yield node["func_name"].upper()
        for value in node.values():
            yield from _function_names(value)
    elif isinstance(node, list):
        for value in node:
            yield from _function_names(value)


def test_the_trend_reads_the_last_twelve_months_of_the_snapshots_data(engine, runs):
    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    trend = next(t for t in dashboard.tiles if t.title == "Revenue by month")
    [body] = trend.plan.plans.values()
    where = body["where"]
    assert where["op"] == ">=" and where["left"]["column_name"] == "OrderDate" and where["right"]["value"] == "2023-06-15"
    assert body["group_by"] and body["order_by"][0]["direction"] == "asc"


def test_same_snapshot_and_glossary_give_the_same_dashboard(engine, runs):
    first, _ = engine.dashboards.generate("ds1", user_context=ANA)
    second, _ = engine.dashboards.generate("ds1", user_context=ANA)

    assert first.id != second.id
    assert [t.model_dump() for t in first.tiles] == [t.model_dump() for t in second.tiles]
    assert first.filters == second.filters and first.title == second.title


def test_generate_makes_no_model_call(engine, runs, server):
    engine.dashboards.generate("ds1", user_context=ANA)
    engine.dashboards.suggest_questions("ds1", user_context=ANA)

    assert server.calls == [], "not one model call"
    assert runs.calls and all(c["answer"] is False and isinstance(c["plan"], SuppliedPlan) for c in runs.calls)
    assert all(c["user_context"] == ANA for c in runs.calls)


def test_a_glossary_role_overrides_the_rule(engine, runs, monkeypatch):
    # the rule reads Revenue as money; the glossary says it is an identifier. The rule reads Score as nothing;
    # the glossary says it is a quantity.
    say(engine, monkeypatch, **{"orders.Revenue": "identifier", "orders.Score": "quantity"})

    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    asked = questions(dashboard)
    assert not any("revenue" in q for q in asked)
    assert "What is the total score?" in asked and "What is the total quantity?" in asked


def test_an_identifier_is_never_a_measure(engine, runs, monkeypatch):
    say(engine, monkeypatch, **{"orders.Quantity": "identifier"})  # even a column the rule would call a quantity

    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    text = " ".join(questions(dashboard)).lower()
    assert "quantity" not in text and "customerid" not in text and "orderid" not in text
    assert "customer id" not in text and "order id" not in text


def test_no_measure_gives_counts_and_distributions(engine, runs, monkeypatch):
    no_measure = snapshot_of(customers(), _table("orders", 1000, [
        ("OrderId", "INTEGER", True, None), ("Status", "TEXT", False, _stats(4))]))
    index(engine, monkeypatch, no_measure)

    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    assert questions(dashboard) == ["How many rows are in orders?", "How many orders rows are there per status?",
                                    "How many customers rows are there per country?"]


def test_nothing_to_chart_names_what_is_missing(engine, runs, monkeypatch):
    index(engine, monkeypatch, snapshot_of())

    with pytest.raises(NoTiles, match="no table"):
        engine.dashboards.generate("ds1", user_context=ANA)
    assert runs.calls == []


def test_a_denied_table_is_never_charted_and_never_run(engine, runs):
    dashboard, run = engine.dashboards.generate("ds1", user_context=LIMITED)

    assert not any("customers" in q or "country" in q for q in questions(dashboard))
    assert dashboard.user_id == "lee" and all(c["user_context"] == LIMITED for c in runs.calls)
    for tile in dashboard.tiles:
        assert {t["name"] for body in tile.plan.plans.values() for t in body["tables"]} == {"orders"}
    assert engine.dashboards.suggest_questions("ds1", user_context=LIMITED)[0] == "How many rows are in orders?"


def test_a_caller_who_cannot_reach_the_datasource_learns_nothing_about_it(engine, runs, monkeypatch):
    read: List[str] = []
    monkeypatch.setattr(Retriever, "snapshot", lambda self, datasource_id: read.append(datasource_id) or SNAPSHOT)

    with pytest.raises(NotIndexed, match="nl2sql index --datasource other"):  # the limited role reaches only ds1
        engine.dashboards.generate("other", user_context=LIMITED)
    with pytest.raises(NotIndexed, match="nl2sql index --datasource other"):
        engine.dashboards.suggest_questions("other", user_context=LIMITED)

    assert read == [] and runs.calls == [], "the snapshot was not even read"


def test_a_caller_who_reaches_the_datasource_but_no_table_gets_nothing(engine, runs):
    with pytest.raises(NoTiles, match="no table"):
        engine.dashboards.generate("ds1", user_context=NOTHING)
    assert runs.calls == []


def test_not_indexed_names_the_fix(engine, runs, monkeypatch):
    index(engine, monkeypatch, None)

    with pytest.raises(NotIndexed, match=r"Index it first: nl2sql index --datasource ds1"):
        engine.dashboards.generate("ds1", user_context=ANA)
    with pytest.raises(NotIndexed, match="nl2sql index"):
        engine.dashboards.suggest_questions("ds1", user_context=ANA)


def test_max_tiles_caps_the_dashboard(engine, runs):
    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA, max_tiles=4)

    assert len(dashboard.tiles) == 4 and len(runs.calls) == 4


# ------------------------------------------------------------------ what it keeps

def test_failed_or_empty_charts_are_listed_as_skipped(engine, runs):
    def respond(call):
        if call["question"] == "What is the total quantity?":
            return failed()
        if call["question"].startswith("Which 10 status"):
            return empty()
        return good()

    runs.respond = respond
    dashboard, run = engine.dashboards.generate("ds1", user_context=ANA)

    assert len(dashboard.tiles) == 9 - 3
    skipped = {s.title: s for s in run.skipped}
    assert set(skipped) == {"Total quantity", "Top 10 status by revenue", "Top 10 status by quantity"}
    assert skipped["Total quantity"].status == "error" and "It failed" in skipped["Total quantity"].reason
    assert skipped["Top 10 status by revenue"].status == "empty"
    assert {r.tile_id for r in run.tiles} == {t.id for t in dashboard.tiles} and run.trigger == "generate"
    stored = {r.tile_id for r in DashboardStore(engine._runtime.state).runs(dashboard.id)}
    assert stored == {t.id for t in dashboard.tiles}, "a skipped chart leaves no stored result"


def test_a_dashboard_where_nothing_answered_is_not_saved(engine, runs):
    runs.respond = lambda call: failed()

    with pytest.raises(NoTiles, match="answered"):
        engine.dashboards.generate("ds1", user_context=ANA)

    assert engine.dashboards.list(user_context=ANA) == [] and engine.dashboards.list(user_context=ANA, status="draft") == []


# ------------------------------------------------------------------ where it lands

def test_generated_dashboard_is_saved_in_the_default_workspace(engine, runs):
    dashboard, run = engine.dashboards.generate("ds1", user_context=ANA)

    [workspace] = engine.workspaces.list(user_context=ANA)
    assert workspace.name == "General"
    assert dashboard.status == "saved" and dashboard.workspace_id == workspace.id and dashboard.title == "ds1 overview"
    assert dashboard.user_id == "ana" and dashboard.tenant_id == "acme"
    assert engine.dashboards.get(dashboard.id, user_context=ANA) == dashboard
    assert [i.id for i in engine.dashboards.list(user_context=ANA)] == [dashboard.id]
    with pytest.raises(DashboardNotFound):
        engine.dashboards.get(dashboard.id, user_context=BOB)
    assert len(run.tiles) == len(dashboard.tiles)


def test_generate_into_a_named_workspace_and_title(engine, runs):
    workspace = engine.workspaces.create("Sales", user_context=ANA)

    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA, title="Orders at a glance",
                                              workspace_id=workspace.id)

    assert dashboard.workspace_id == workspace.id and dashboard.title == "Orders at a glance"
    assert [w.name for w in engine.workspaces.list(user_context=ANA)] == ["Sales"], "General was not made"


def test_the_filters_are_the_time_column_and_the_distribution_dimensions(engine, runs):
    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    assert [(f.column, f.kind, f.op) for f in dashboard.filters] == [
        ("ds1.main.orders.OrderDate", "temporal", "between"),
        ("ds1.main.orders.Status", "text", "in"), ("ds1.main.customers.Country", "text", "in")]
    assert len({f.id for f in dashboard.filters}) == 3


def test_generated_dashboard_can_be_edited_manually(engine, runs, server):
    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)
    first, second = dashboard.tiles[0], dashboard.tiles[1]

    engine.dashboards.rename_tile(dashboard.id, first.id, "Orders", user_context=ANA)
    engine.dashboards.remove_tile(dashboard.id, second.id, user_context=ANA)
    engine.dashboards.set_schedule(dashboard.id, 60, user_context=ANA)
    edited = engine.dashboards.get(dashboard.id, user_context=ANA)
    refreshed = engine.dashboards.refresh(dashboard.id, user_context=ANA)

    assert edited.tiles[0].title == "Orders" and second.id not in {t.id for t in edited.tiles}
    assert edited.schedule is not None and len(refreshed.tiles) == len(edited.tiles)
    assert server.calls == []


def test_events_say_what_was_generated(engine, runs):
    seen: List[Any] = []
    engine.events.subscribe(seen.append, names=["dashboard.generated", "dashboard.refreshed"])
    runs.respond = lambda call: failed() if call["question"] == "What is the total quantity?" else good()

    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    generated = next(e for e in seen if e.name == "dashboard.generated")
    assert generated.data == {"dashboard_id": dashboard.id, "datasource_id": "ds1", "tiles": 8, "skipped": 1}
    assert next(e for e in seen if e.name == "dashboard.refreshed").data["trigger"] == "generate"


# ------------------------------------------------------------------ suggest_questions

def test_suggest_questions_runs_nothing(engine, runs):
    asked = engine.dashboards.suggest_questions("ds1", user_context=ANA, n=3)

    assert asked == ["How many rows are in orders?", "What is the total revenue?", "What is the total quantity?"]
    assert runs.calls == []
    assert engine.dashboards.list(user_context=ANA) == [] and engine.workspaces.list(user_context=ANA) == []


def test_suggest_questions_are_the_first_charts_generate_would_choose(engine, runs):
    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    assert engine.dashboards.suggest_questions("ds1", user_context=ANA, n=20) == questions(dashboard)
    assert engine.dashboards.suggest_questions("ds1", user_context=ANA, n=2) == questions(dashboard)[:2]


# ------------------------------------------------------------------ the layout

def _tile(tid: str) -> Tile:
    plan = ChartPlan(decomposition={"sub_queries": []}, plans={"sq1": {}})
    return Tile(id=tid, title=tid, question=tid, plan=plan, layout=TileLayout(x=0, y=0, w=1, h=1))


def test_layout_puts_big_numbers_first():
    tiles = [_tile(t) for t in ("bar1", "line1", "num1", "table1", "num2", "line2", "bar2")]
    kinds = {"bar1": "bar", "line1": "line", "num1": "big_number", "table1": "table", "num2": "big_number",
             "line2": "line", "bar2": "bar"}

    placed = {t.id: t.layout for t in arrange(tiles, kinds)}

    assert [t.id for t in arrange(tiles, kinds)] == ["num1", "num2", "line1", "line2", "bar1", "bar2", "table1"]
    assert (placed["num1"].x, placed["num1"].y, placed["num1"].w, placed["num1"].h) == (0, 0, 1, 1)
    assert (placed["num2"].x, placed["num2"].y) == (1, 0)
    assert (placed["line1"].x, placed["line1"].y, placed["line1"].w, placed["line1"].h) == (0, 1, 4, 2)
    assert (placed["line2"].x, placed["line2"].y, placed["line2"].w) == (0, 3, 2)
    assert (placed["bar1"].x, placed["bar1"].y, placed["bar1"].w) == (2, 3, 2), "a bar fills the row beside line2"
    assert (placed["bar2"].x, placed["bar2"].y) == (0, 5)
    assert (placed["table1"].x, placed["table1"].y, placed["table1"].w) == (0, 7, 4)


def test_the_generated_layout_follows_the_charts_that_were_drawn(engine, runs):
    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    big = [t for t in dashboard.tiles if t.layout.h == 1]
    assert big == [], "the canned chart is a bar: nothing was drawn as a big number"
    assert all(t.layout.w == 2 for t in dashboard.tiles)
    assert [(t.layout.x, t.layout.y) for t in dashboard.tiles][:3] == [(0, 0), (2, 0), (0, 2)]


# ------------------------------------------------------------------ review fixes

def test_a_generate_that_answers_nothing_leaves_no_workspace_it_made(engine, runs):
    runs.respond = lambda call: failed()

    with pytest.raises(NoTiles):
        engine.dashboards.generate("ds1", user_context=ANA)

    assert engine.workspaces.list(user_context=ANA) == []


def test_a_generate_that_answers_nothing_keeps_a_workspace_that_was_there(engine, runs):
    general = engine.workspaces.create("General", user_context=ANA)
    runs.respond = lambda call: failed()

    with pytest.raises(NoTiles):
        engine.dashboards.generate("ds1", user_context=ANA)

    assert [w.id for w in engine.workspaces.list(user_context=ANA)] == [general.id]


def test_suggest_questions_wants_at_least_one(engine, runs):
    for n in (0, -1):
        with pytest.raises(ValueError, match="at least 1"):
            engine.dashboards.suggest_questions("ds1", user_context=ANA, n=n)


def test_a_draft_that_is_deleted_is_not_announced_as_refreshed(engine, runs):
    seen: List[Any] = []
    engine.events.subscribe(seen.append, names=["dashboard.refreshed", "dashboard.generated"])
    runs.respond = lambda call: failed()

    with pytest.raises(NoTiles):
        engine.dashboards.generate("ds1", user_context=ANA)

    assert seen == []


def test_a_date_role_on_a_text_column_makes_no_trend(engine, runs, monkeypatch):
    snapshot = snapshot_of(_table("orders", 1000, [
        ("OrderId", "INTEGER", True, None), ("Revenue", "DECIMAL(10,2)", False, _stats(900)),
        ("PlacedOn", "TEXT", False, _stats(300))]))
    index(engine, monkeypatch, snapshot)
    say(engine, monkeypatch, **{"orders.PlacedOn": "date"})

    dashboard, _ = engine.dashboards.generate("ds1", user_context=ANA)

    assert not any("by month" in q for q in questions(dashboard))


# ------------------------------------------------------------------ every template renders

@pytest.mark.parametrize("dialect", ["postgres", "tsql", "mysql"])
def test_every_template_plan_validates_and_renders_without_a_star(dialect):
    from types import SimpleNamespace

    from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse
    from nl2sql.pipeline.nodes.generator.node import GeneratorNode
    from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
    from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
    from nl2sql.pipeline.state import SubgraphExecutionState
    from nl2sql.testing.runtime import fake_runtime, stub_ds_registry
    from nl2sql_sdk import Limits

    from nl2sql.dashboards.roles import Source, column_roles
    from nl2sql.dashboards.templates import candidates

    source = Source("ds1", SNAPSHOT, dialect, lambda schema, table: True, DatasourceKnowledge(), 20, 12)
    found = candidates("ds1", column_roles(source))
    assert len(found) == 9
    tables = [Table(name=c.table.table_name, columns=[Column(name=n, type=col.data_type)
                                                      for n, col in c.columns.items()])
              for c in SNAPSHOT.contract.tables.values()]
    adapter = SimpleNamespace(get_dialect=lambda: dialect)
    validator = LogicalValidatorNode(fake_runtime(ds_registry=SimpleNamespace(), policies={"allow_all": True}))
    generator = GeneratorNode(fake_runtime(ds_registry=stub_ds_registry(adapter, limits=Limits(row_limit=1000))))
    for candidate in found:
        for sub_query_id, body in candidate.plan.plans.items():
            state = SubgraphExecutionState(
                trace_id="t", sub_query=DecomposerResponse.model_validate(candidate.plan.decomposition).sub_queries[0],
                relevant_tables=tables, user_context=UserContext(roles=["admin"]),
                ast_planner_response=ASTPlannerResponse(plan=PlanModel.model_validate(body)))
            assert not validator(state).get("errors"), (candidate.title, validator(state)["errors"])
            made = generator(state)
            assert not made.get("errors"), (candidate.title, made["errors"])
            sql = made["generator_response"].sql_draft
            assert sql and '"*"' not in sql and "[*]" not in sql and "`*`" not in sql, (dialect, sql)