"""Refresh, stored results and schedules (docs/architecture/wave_c.md, section 6; row D2).

A chart's refresh is a run of its stored plan through ``QueryAPI.run_query(plan=..., answer=False)``; here that call
is replaced by a function returning canned results, so a test can say what was asked, as whom, and what came back.
The real pipeline (the validator, ``plan.after``, the cache) is held by ``tests/e2e/test_dashboards_fake_llm.py``.
The fake model server records every call, so a test can say that a refresh made none.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List

import pytest

from nl2sql import NL2SQL, SuppliedPlan, UserContext
from nl2sql.api.query_api import QueryAPI, QueryResult
from nl2sql.api.results_api import ResultsAPI
from nl2sql.charts.spec import ChartData, ChartSpec, Encoding
from nl2sql.common.errors import DashboardNotFound, RunWarning
from nl2sql.dashboards.models import ChartPlan, Dashboard, Tile, TileLayout, TileRun
from nl2sql.dashboards.store import DashboardStore
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.explain.models import Explanation, ExplainedStep
from nl2sql.hooks.approval import Approval, ApprovalRequest, VerifiedRef
from nl2sql.limits import RunStop
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery
from nl2sql.testing.config import make_config
from nl2sql.testing.fake_llm import FakeLLMServer

ANA = UserContext(user_id="ana", tenant_id="acme", roles=["analyst", "sales"])
BOB = UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])
APPROVER = UserContext(user_id="lead", tenant_id="acme", roles=["approver"])
NOW = datetime.now(timezone.utc)
ROLES = {"analyst": {"description": "Reads", "allow": ["*"]}, "sales": {"description": "Sells", "allow": ["*"]}}
KINDS = {"city": "text", "n": "number"}


# ------------------------------------------------------------------ the canned runs

def good(rows: int = 2, trace: str = "tr-1") -> QueryResult:
    step = ExplainedStep(sub_query_id="sq1", datasource_id="ds1", source="supplied", text="Counts the orders.")
    return QueryResult(status="success", trace_id=trace, explanation=Explanation(steps=[step]),
                       result_manifest=[ResultSummary(node_id="sq1", columns=["city", "n"], row_count=rows,
                                                      column_kinds=KINDS)])


def failed(code: str, node: str = "executor", message: str = "It failed.") -> QueryResult:
    return QueryResult(status="error", trace_id="tr-x",
                       errors=[{"node": node, "error_code": code, "message": message, "severity": "ERROR"}])


def _partly_denied() -> QueryResult:
    """A two-sub-query run where a hook denied one sub-query: it is dropped as a warning, the run succeeds."""
    result = good()
    warning = RunWarning(code="HOOK_DENIED", node="sql_agent", sub_query_id="sq2",
                         message="Sub-query 'sq2' (Count orders 2) was not run: Not for you.")
    return result.model_copy(update={"warnings": [warning]})


def _denied_and_asking() -> QueryResult:
    """A two-sub-query run: one sub-query refused for access, the other waiting for approval."""
    return QueryResult(status="needs_approval", trace_id="tr-m", approvals_needed=[request()], errors=[
        {"node": "logical_validator", "error_code": "SECURITY_VIOLATION", "message": "No access.", "severity": "ERROR"},
        {"node": "hooks", "error_code": "NEEDS_APPROVAL", "message": "Reads a lot.", "severity": "ERROR"}])


def spec(trace: str) -> ChartSpec:
    return ChartSpec(kind="bar", title="Result", reason="one category and one number",
                     x=Encoding(field="city", kind="text", title="City"),
                     y=[Encoding(field="n", kind="number", title="N")],
                     data=ChartData(trace_id=trace, result_id="sq1", columns=["city", "n"],
                                    rows=[["Paris", 3], ["Oslo", 1]], truncated=False))


class Runs:
    """Stands in for ``QueryAPI.run_query`` and ``ResultsAPI.chart``: records each call, answers ``respond(call)``."""

    def __init__(self, monkeypatch) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.charts: List[Any] = []
        self.readers: List[UserContext] = []  # whom each chart's results were read as
        self.respond: Callable[[Dict[str, Any]], QueryResult] = lambda call: good()
        self.gate: Callable[[], None] = lambda: None
        runs = self

        def run_query(api: QueryAPI, question: str, **kwargs: Any) -> QueryResult:
            runs.calls.append({"question": question, **kwargs})
            runs.gate()
            return runs.respond(runs.calls[-1])

        def chart(api: ResultsAPI, result: QueryResult, result_id=None, *, user_context, kind=None) -> ChartSpec:
            runs.charts.append(kind)
            runs.readers.append(user_context)
            return spec(result.trace_id)

        monkeypatch.setattr(QueryAPI, "run_query", run_query)
        monkeypatch.setattr(ResultsAPI, "chart", chart)

    def refreshing(self) -> List[Dict[str, Any]]:
        """The calls that ran for real (a check does not execute a generated plan)."""
        return [c for c in self.calls if c["execute"]]

    def checking(self) -> List[Dict[str, Any]]:
        return [c for c in self.calls if not c["execute"]]


# ------------------------------------------------------------------ the engine

@pytest.fixture
def server():
    fake = FakeLLMServer([]).start()
    yield fake
    fake.stop()


@pytest.fixture
def make(tmp_path, monkeypatch, server):
    monkeypatch.setenv("DASHBOARD_FAKE_KEY", "sk-fake")
    engines: List[NL2SQL] = []

    def _make(**sections: Any) -> NL2SQL:
        sections.setdefault("policies", {"roles": ROLES})
        config = make_config(tmp_path / f"engine{len(engines)}",
                             llm={"provider": "openai", "model": "gpt-dashboard-test",
                                  "api_key": "${env:DASHBOARD_FAKE_KEY}", "base_url": server.base_url}, **sections)
        engines.append(NL2SQL(config))
        return engines[-1]

    yield _make
    for engine in engines:
        engine.close()


@pytest.fixture
def engine(make):
    return make()


@pytest.fixture
def runs(monkeypatch):
    return Runs(monkeypatch)


def chart_plan(queries: int = 1) -> ChartPlan:
    sub_queries = [SubQuery(id=f"sq{i}", datasource_id="ds1", intent=f"Count orders {i}") for i in range(1, queries + 1)]
    plan = PlanModel.model_validate({
        "tables": [{"name": "orders", "schema_name": "main", "alias": "t1"}],
        "select_items": [{"alias": "n", "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                                                 "args": [{"kind": "column", "alias": "t1", "column_name": "id"}]}}]})
    operation = "standalone" if queries == 1 else "union"
    decomposition = DecomposerResponse(sub_queries=sub_queries, combine_groups=[CombineGroup(
        group_id="g1", operation=operation, inputs=[CombineInput(subquery_id=s.id) for s in sub_queries])])
    return ChartPlan(decomposition=decomposition.model_dump(mode="json"),
                     plans={s.id: plan.model_dump(mode="json") for s in sub_queries})


def tile(tid: str, *, x: int = 0, y: int = 0, plan: ChartPlan = None, chart: str = "auto") -> Tile:
    return Tile(id=tid, title=f"Chart {tid}", question=f"What is {tid}?", plan=plan or chart_plan(), chart=chart,
                layout=TileLayout(x=x, y=y, w=2, h=2))


def seed(engine: NL2SQL, did: str = "d1", *, user: UserContext = ANA, tiles: Any = None, **fields: Any) -> Dashboard:
    if tiles is None:
        tiles = [tile("t1"), tile("t2", x=2)]
    dashboard = Dashboard(id=did, title=f"Dashboard {did}", workspace_id="ws", status="saved",
                          tenant_id=user.tenant_id, user_id=user.user_id, tiles=tiles, created_at=NOW,
                          updated_at=NOW, **fields)
    DashboardStore(engine._runtime.state).put(dashboard)
    return dashboard


def stored(engine: NL2SQL, did: str = "d1") -> Dict[str, TileRun]:
    return {r.tile_id: r for r in DashboardStore(engine._runtime.state).runs(did)}


def put_run(engine: NL2SQL, tile_id: str, *, age: timedelta = timedelta(0), did: str = "d1") -> TileRun:
    run = TileRun(tile_id=tile_id, at=NOW - age, status="ok", trace_id="old", column_kinds=KINDS, chart=spec("old"))
    engine._runtime.state.put_tile_runs(did, [run.model_dump(mode="json")])
    return run


def listen(engine: NL2SQL, name: str) -> List[Any]:
    seen: List[Any] = []
    engine.events.subscribe(seen.append, names=[name])
    return seen


def request(user: UserContext = ANA, question: str = "What is t1?") -> ApprovalRequest:
    return ApprovalRequest.build(question=question, asker=user, hook="cost", datasource_id="ds1", sql="SELECT 1",
                                 reason="Reads a lot", approver_roles=["approver"])


# ------------------------------------------------------------------ refresh

def test_refresh_makes_no_model_call(engine, runs, server):
    seed(engine)

    run = engine.dashboards.refresh("d1", user_context=ANA)

    assert [t.status for t in run.tiles] == ["ok", "ok"] and run.trigger == "manual"
    assert server.calls == [], "not one model call"
    assert all(call["answer"] is False and isinstance(call["plan"], SuppliedPlan) for call in runs.calls)


def test_refresh_reruns_every_stored_query_and_the_combine_step(engine, runs):
    seed(engine, tiles=[tile("t1", plan=chart_plan(queries=2))])

    engine.dashboards.refresh("d1", user_context=ANA)

    [call] = runs.calls
    supplied = call["plan"]
    assert call["question"] == "What is t1?" and call["execute"] is True
    assert [s.id for s in supplied.decomposition.sub_queries] == ["sq1", "sq2"]
    assert supplied.decomposition.combine_groups[0].operation == "union"
    assert set(supplied.plans) == {"sq1", "sq2"}


def test_refresh_one_chart_leaves_the_others_untouched(engine, runs):
    seed(engine)
    before = put_run(engine, "t1")

    run = engine.dashboards.refresh("d1", tile_id="t2", user_context=ANA)

    assert [call["question"] for call in runs.calls] == ["What is t2?"]
    assert [t.tile_id for t in run.tiles] == ["t2"]
    assert stored(engine)["t1"] == before and stored(engine)["t2"].status == "ok"
    with pytest.raises(ValueError, match="No chart 'nope'"):
        engine.dashboards.refresh("d1", tile_id="nope", user_context=ANA)


def test_results_are_stored_with_a_timestamp_and_the_column_kinds(engine, runs):
    seed(engine)
    started = datetime.now(timezone.utc)

    first = engine.dashboards.refresh("d1", user_context=ANA)
    kept = stored(engine)
    engine.dashboards.refresh("d1", user_context=ANA)
    again = stored(engine)

    assert set(kept) == {"t1", "t2"} and first.tiles == [kept["t1"], kept["t2"]]
    t1 = kept["t1"]
    assert started <= t1.at <= datetime.now(timezone.utc)
    assert t1.column_kinds == KINDS, "a result last drawn as a table can still be forced to a bar"
    assert t1.trace_id == "tr-1" and t1.explanation == "Counts the orders." and t1.chart.title == "Chart t1"
    assert t1.chart.data.rows == [["Paris", 3], ["Oslo", 1]] and t1.filtered is False
    assert again["t1"].at > t1.at and len(again) == 2, "each run replaces the last result"


def test_a_forced_chart_kind_is_drawn_and_one_the_data_cannot_draw_falls_back_to_auto(engine, runs, monkeypatch):
    seed(engine, tiles=[tile("t1", chart="line")])
    drawn: List[Any] = []

    def chart(api, result, result_id=None, *, user_context, kind=None):
        drawn.append(kind)
        if kind == "line":
            raise ValueError("A line needs a temporal column.")
        return spec(result.trace_id)

    monkeypatch.setattr(ResultsAPI, "chart", chart)

    run = engine.dashboards.refresh("d1", user_context=ANA)

    assert drawn == ["line", None] and run.tiles[0].status == "ok" and run.tiles[0].chart is not None


def test_a_result_with_no_rows_is_empty(engine, runs):
    seed(engine)
    runs.respond = lambda call: good(rows=0)

    run = engine.dashboards.refresh("d1", user_context=ANA)

    assert [t.status for t in run.tiles] == ["empty", "empty"]


def test_invalid_stored_plan_is_needs_fixing_without_refiner_and_others_still_run(engine, runs):
    seed(engine)
    runs.respond = lambda call: (failed("COLUMN_NOT_FOUND", "logical_validator", "Column 'id' no longer exists.")
                                 if call["question"] == "What is t1?" else good())

    run = engine.dashboards.refresh("d1", user_context=ANA)

    broken, working = run.tiles
    assert (broken.status, working.status) == ("needs_fixing", "ok")
    assert broken.error == {"code": "TILE_NEEDS_FIXING", "message": "Column 'id' no longer exists."}
    assert broken.chart is None, "a chart that cannot be right never looks live"
    assert stored(engine)["t1"].chart is None and stored(engine)["t2"].chart is not None
    assert all(call["plan"] is not None for call in runs.calls), "a stored plan is always supplied, never replanned"


def test_a_stored_plan_that_no_longer_parses_is_needs_fixing_and_runs_nothing(engine, runs):
    broken = chart_plan().model_copy(update={"plans": {"sq1": {"tables": "not a list"}}})
    seed(engine, tiles=[tile("t1", plan=broken)])

    run = engine.dashboards.refresh("d1", user_context=ANA)

    assert run.tiles[0].status == "needs_fixing" and run.tiles[0].error["code"] == "TILE_NEEDS_FIXING"
    assert runs.calls == []


def test_a_denied_chart_has_no_rows_and_a_failed_one_says_why(engine, runs):
    seed(engine)
    runs.respond = lambda call: (failed("SECURITY_VIOLATION", "logical_validator", "No access to orders.")
                                 if call["question"] == "What is t1?" else failed("DB_EXECUTION_ERROR"))

    denied, error = engine.dashboards.refresh("d1", user_context=ANA).tiles

    assert (denied.status, error.status) == ("denied", "error")
    assert denied.chart is None and denied.error["code"] == "SECURITY_VIOLATION"
    assert error.error["code"] == "DB_EXECUTION_ERROR"


def test_refresh_runs_as_the_caller(engine, runs):
    seed(engine)

    engine.dashboards.refresh("d1", user_context=ANA)

    assert {call["user_context"].user_id for call in runs.calls} == {"ana"}
    assert {tuple(call["user_context"].roles) for call in runs.calls} == {("analyst", "sales")}
    assert runs.readers and {user.user_id for user in runs.readers} == {"ana"}, "the results are read as the asker"
    with pytest.raises(DashboardNotFound):
        engine.dashboards.refresh("d1", user_context=BOB)
    with pytest.raises(TypeError, match="user_context is required"):
        engine.dashboards.refresh("d1", user_context=None)


def test_cost_guard_confirm_makes_a_chart_need_approval_and_keeps_its_result(engine, runs):
    seed(engine)
    kept = put_run(engine, "t1")
    ask = request()
    runs.respond = lambda call: (QueryResult(status="needs_approval", trace_id="tr-n", approvals_needed=[ask])
                                 if call["question"] == "What is t1?" else good())

    asking, working = engine.dashboards.refresh("d1", user_context=ANA).tiles

    assert asking.status == "needs_approval" and asking.chart is None and asking.approvals_needed == [ask]
    assert working.status == "ok"
    assert stored(engine)["t1"] == kept, "the chart keeps its last result"


def test_manual_refresh_with_approvals_runs_the_chart(engine, runs):
    seed(engine)
    approval = Approval(request=request(), by=APPROVER)

    engine.dashboards.refresh("d1", tile_id="t1", approvals=[approval], user_context=ANA)

    [call] = runs.calls
    assert call["approvals"] == [approval] and call["question"] == "What is t1?"


def test_stopped_chart_ends_the_refresh(engine, runs):
    seed(engine)
    kept = put_run(engine, "t1")
    stop = RunStop(limit="max_llm_calls", value=1, spent=1)
    runs.respond = lambda call: QueryResult(status="stopped", trace_id="tr-s", stop=stop)

    run = engine.dashboards.refresh("d1", user_context=ANA)

    assert [t.status for t in run.tiles] == ["stopped", "stopped"]
    assert len(runs.calls) == 1, "the remaining charts are not run"
    assert stored(engine) == {"t1": kept}, "both keep their last stored result"


def test_a_verified_chart_refreshes_through_supplied_plan(engine, runs):
    plan = ChartPlan(verified=VerifiedRef(id="top_cities", params={"year": 2024}), verified_datasource_id="ds1")
    seed(engine, tiles=[tile("t1", plan=plan)])

    run = engine.dashboards.refresh("d1", user_context=ANA)

    [call] = runs.calls
    assert call["plan"].verified == VerifiedRef(id="top_cities", params={"year": 2024})
    assert call["plan"].verified_datasource_id == "ds1" and call["execute"] is True
    assert run.tiles[0].status == "ok"


def test_a_verified_chart_plan_names_its_datasource():
    with pytest.raises(ValueError, match="verified_datasource_id"):
        ChartPlan(verified=VerifiedRef(id="top_cities"))


def test_a_check_failure_from_a_hook_denied_sub_query_is_denied_and_never_stored_as_ok(engine, runs):
    """A hook's deny scoped to one sub-query drops it as a warning: the run is a success with part of the data."""
    seed(engine, tiles=[tile("t1", plan=chart_plan(queries=2))])
    runs.respond = lambda call: _partly_denied()

    [chart] = engine.dashboards.refresh("d1", user_context=ANA).tiles

    assert chart.status == "denied" and chart.chart is None and chart.error["code"] == "HOOK_DENIED"
    assert stored(engine)["t1"].status == "denied" and stored(engine)["t1"].chart is None


def test_refresh_publishes_the_counts_per_status(engine, runs):
    seed(engine)
    seen = listen(engine, "dashboard.refreshed")
    runs.respond = lambda call: failed("SECURITY_VIOLATION") if call["question"] == "What is t1?" else good()

    engine.dashboards.refresh("d1", user_context=ANA)

    [event] = seen
    assert event.data == {"dashboard_id": "d1", "trigger": "manual", "counts": {"denied": 1, "ok": 1}, "filters": []}


# ------------------------------------------------------------------ the lock

def test_a_second_process_skips_a_dashboard_being_refreshed(engine, runs):
    seed(engine, schedule=_due_schedule())
    with engine._runtime.state.lock("dashboard-refresh:d1", 60):
        with pytest.raises(ValueError, match="being refreshed"):
            engine.dashboards.refresh("d1", user_context=ANA)
        assert engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2)) == []
    assert runs.calls == []
    assert engine.dashboards.refresh("d1", user_context=ANA).tiles, "the lock is released afterwards"


def test_an_edit_cannot_land_while_a_refresh_is_running(engine, runs):
    """A refresh that finishes after ``set_filters`` would store results that ignore the new filters."""
    seed(engine)
    outcome: List[Any] = []

    def gate():
        try:
            engine.dashboards.rename("d1", "Changed", user_context=ANA)
        except ValueError as error:
            outcome.append(str(error))

    runs.gate = gate

    engine.dashboards.refresh("d1", user_context=ANA)

    assert outcome and "being refreshed" in outcome[0]
    assert engine.dashboards.get("d1", user_context=ANA).title == "Dashboard d1"
    runs.gate = lambda: None
    assert engine.dashboards.rename("d1", "Changed", user_context=ANA).title == "Changed"


def test_delete_and_pin_wait_for_a_refresh_too(engine, runs):
    seed(engine)
    with engine._runtime.state.lock("dashboard-refresh:d1", 60):
        with pytest.raises(ValueError, match="being refreshed"):
            engine.dashboards.delete("d1", user_context=ANA)


# ------------------------------------------------------------------ open

def test_open_serves_stored_results_after_the_access_check(engine, runs):
    seed(engine, tiles=[tile("t1"), tile("t2", x=2, plan=ChartPlan(verified=VerifiedRef(id="v"),
                                                                    verified_datasource_id="ds1"))])
    old = [put_run(engine, "t1", age=timedelta(hours=2)), put_run(engine, "t2", age=timedelta(hours=1))]

    view = engine.dashboards.open("d1", user_context=ANA)

    assert view.runs == old, "the stored rows are served as they were stored"
    assert view.refreshed_at == old[0].at and view.due == []
    generated, verified = runs.calls
    assert generated["execute"] is False and generated["plan"].decomposition is not None, \
        "a generated plan is checked without running"
    assert verified["execute"] is False and verified["plan"].verified is not None, \
        "a verified query is checked by the matcher without running it (D135)"
    assert all(c["answer"] is False and c["user_context"] == ANA for c in runs.calls)


@pytest.mark.parametrize("shape", ["denied_and_asking", "partly_denied"])
def test_open_serves_no_rows_when_any_part_of_a_chart_is_denied(engine, runs, shape):
    seed(engine, tiles=[tile("t1", plan=chart_plan(queries=2))])
    put_run(engine, "t1")
    runs.respond = lambda call: _denied_and_asking() if shape == "denied_and_asking" else _partly_denied()

    [served] = engine.dashboards.open("d1", user_context=ANA).runs

    assert served.status == "denied" and served.chart is None and served.column_kinds == {}


def test_open_serves_a_chart_waiting_for_approval_only_when_nothing_else_failed(engine, runs):
    seed(engine)
    kept = put_run(engine, "t1")
    put_run(engine, "t2")
    asking = QueryResult(status="needs_approval", trace_id="tr-n", approvals_needed=[request()], errors=[
        {"node": "hooks", "error_code": "NEEDS_APPROVAL", "message": "Reads a lot.", "severity": "ERROR"}])
    broken = asking.model_copy(update={"errors": [
        *asking.errors,
        {"node": "logical_validator", "error_code": "COLUMN_NOT_FOUND", "message": "Gone.", "severity": "ERROR"}]})
    runs.respond = lambda call: asking if call["question"] == "What is t1?" else broken

    one, two = engine.dashboards.open("d1", user_context=ANA).runs

    assert one == kept, "waiting for approval, nothing else wrong: the stored rows are served"
    assert two.chart is None and two.status != "ok"


def test_open_never_serves_a_result_stored_by_another_process_after_its_check(engine, runs):
    seed(engine, schedule=_due_schedule(every=60))
    put_run(engine, "t1")

    def another_process_stores_t2():
        put_run(engine, "t2")

    runs.gate = another_process_stores_t2
    with engine._runtime.state.lock("dashboard-refresh:d1", 60):  # the refresh is skipped: the lock is held
        view = engine.dashboards.open("d1", user_context=ANA)

    assert [r.tile_id for r in view.runs] == ["t1"], "t2 was never checked, so its rows are not served"
    assert view.due == ["t2"]


def test_stored_dashboard_results_are_denied_after_access_is_lost(engine, runs):
    seed(engine)
    put_run(engine, "t1")
    put_run(engine, "t2")
    runs.respond = lambda call: (good() if "sales" in call["user_context"].roles
                                 else failed("SECURITY_VIOLATION", "logical_validator", "No access to orders."))

    kept = engine.dashboards.open("d1", user_context=ANA)
    lost = engine.dashboards.open("d1", user_context=UserContext(user_id="ana", tenant_id="acme", roles=["analyst"]))

    assert [r.status for r in kept.runs] == ["ok", "ok"] and kept.runs[0].chart is not None
    assert [r.status for r in lost.runs] == ["denied", "denied"]
    assert all(r.chart is None and r.error["code"] == "SECURITY_VIOLATION" for r in lost.runs), "no rows"


def test_an_invalid_stored_plan_is_needs_fixing_on_open_and_a_failed_check_serves_nothing(engine, runs):
    seed(engine)
    put_run(engine, "t1")
    put_run(engine, "t2")
    runs.respond = lambda call: (failed("COLUMN_NOT_FOUND", "logical_validator", "Gone.")
                                 if call["question"] == "What is t1?" else failed("PIPELINE_TIMEOUT"))

    view = engine.dashboards.open("d1", user_context=ANA)

    assert [r.status for r in view.runs] == ["needs_fixing", "error"]
    assert all(r.chart is None for r in view.runs)


def test_a_chart_with_no_stored_result_is_not_in_the_view(engine, runs):
    seed(engine)
    put_run(engine, "t2")

    view = engine.dashboards.open("d1", user_context=ANA)

    assert [r.tile_id for r in view.runs] == ["t2"] and len(runs.calls) == 1


def test_open_refreshes_only_charts_older_than_the_schedule(engine, runs):
    seed(engine, schedule=_due_schedule(every=60))
    put_run(engine, "t1", age=timedelta(hours=2))
    recent = put_run(engine, "t2", age=timedelta(minutes=5))

    view = engine.dashboards.open("d1", user_context=ANA)

    assert [c["question"] for c in runs.refreshing()] == ["What is t1?"]
    by_tile = {r.tile_id: r for r in view.runs}
    assert by_tile["t2"] == recent and by_tile["t1"].at > NOW and view.due == []
    assert stored(engine)["t1"].at == by_tile["t1"].at
    assert view.refreshed_at == recent.at


def test_open_can_leave_the_refresh_to_the_app(engine, runs):
    seed(engine, schedule=_due_schedule(every=60))
    put_run(engine, "t1", age=timedelta(hours=2))
    put_run(engine, "t2", age=timedelta(minutes=5))

    view = engine.dashboards.open("d1", user_context=ANA, refresh_due=False)

    assert view.due == ["t1"] and runs.refreshing() == []
    assert engine.dashboards.refresh("d1", tile_id=view.due[0], user_context=ANA).tiles[0].status == "ok"


def test_a_dashboard_without_a_schedule_has_nothing_due(engine, runs):
    seed(engine)
    put_run(engine, "t1", age=timedelta(days=30))

    view = engine.dashboards.open("d1", user_context=ANA)

    assert view.due == [] and runs.refreshing() == []


def test_a_chart_never_run_is_due_when_there_is_a_schedule(engine, runs):
    seed(engine, schedule=_due_schedule(every=60))
    put_run(engine, "t2")

    view = engine.dashboards.open("d1", user_context=ANA)

    assert [c["question"] for c in runs.refreshing()] == ["What is t1?"]
    assert {r.tile_id for r in view.runs} == {"t1", "t2"}


def test_open_never_approves(engine, runs):
    seed(engine, schedule=_due_schedule(every=60))
    kept = put_run(engine, "t1", age=timedelta(hours=2))
    put_run(engine, "t2")
    runs.respond = lambda call: (QueryResult(status="needs_approval", trace_id="tr-n", approvals_needed=[request()])
                                 if call["execute"] else good())

    view = engine.dashboards.open("d1", user_context=ANA)

    assert all(not call["approvals"] for call in runs.calls)
    assert view.due == ["t1"] and {r.tile_id: r for r in view.runs}["t1"] == kept, "it keeps its last result"


def test_open_skips_the_refresh_of_a_dashboard_being_refreshed(engine, runs):
    seed(engine, schedule=_due_schedule(every=60))
    put_run(engine, "t1", age=timedelta(hours=2))
    put_run(engine, "t2")

    with engine._runtime.state.lock("dashboard-refresh:d1", 60):
        view = engine.dashboards.open("d1", user_context=ANA)

    assert view.due == ["t1"] and runs.refreshing() == []


def test_open_publishes_a_refresh_only_when_one_ran(engine, runs):
    seed(engine, schedule=_due_schedule(every=60))
    put_run(engine, "t1", age=timedelta(hours=2))
    put_run(engine, "t2")
    seen = listen(engine, "dashboard.refreshed")

    engine.dashboards.open("d1", user_context=ANA)
    engine.dashboards.open("d1", user_context=ANA)

    assert [e.data["trigger"] for e in seen] == ["open"]


def test_open_of_another_users_dashboard_is_not_found(engine, runs):
    seed(engine)

    with pytest.raises(DashboardNotFound):
        engine.dashboards.open("d1", user_context=BOB)
    with pytest.raises(TypeError, match="user_context is required"):
        engine.dashboards.open("d1", user_context=None)
    assert runs.calls == []


# ------------------------------------------------------------------ schedules

def _due_schedule(every: int = 60, roles=("analyst", "sales"), set_at: datetime = NOW, **fields: Any):
    from nl2sql.dashboards.models import Schedule, ScheduleIdentity

    return Schedule(every_minutes=every, identity=ScheduleIdentity(user_id="ana", tenant_id="acme", roles=list(roles)),
                    set_at=set_at, next_at=set_at + timedelta(minutes=every), state="active", **fields)


def test_run_scheduled_runs_only_due_dashboards_as_their_owners(engine, runs):
    seed(engine, "d1", schedule=_due_schedule(every=60))
    seed(engine, "d2", user=BOB, schedule=_due_schedule(every=240))
    seed(engine, "d3")
    seen = listen(engine, "dashboard.refreshed")
    later = NOW + timedelta(minutes=90)

    done = engine.dashboards.run_scheduled(now=later)

    assert [d.dashboard_id for d in done] == ["d1"] and done[0].trigger == "schedule"
    assert [call["user_context"] for call in runs.calls] == [UserContext(user_id="ana", tenant_id="acme",
                                                                         roles=["analyst", "sales"])] * 2
    assert [e.data["trigger"] for e in seen] == ["schedule"]
    schedule = engine.dashboards.get("d1", user_context=ANA).schedule
    assert schedule.state == "active" and schedule.next_at == later + timedelta(minutes=60)
    assert set(stored(engine, "d1")) == {"t1", "t2"} and stored(engine, "d2") == {}
    assert engine.dashboards.run_scheduled(now=later) == [], "not due again until its next time"


def test_a_schedule_runs_as_the_snapshot_not_as_whoever_is_now_the_owner(engine, runs):
    seed(engine, schedule=_due_schedule(roles=("analyst",)))

    engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2))

    assert {tuple(call["user_context"].roles) for call in runs.calls} == {("analyst",)}


def test_schedule_stops_with_a_visible_error_when_a_check_fails(engine, runs):
    seed(engine, "d1", schedule=_due_schedule(roles=("analyst", "retired")))
    seed(engine, "d2", schedule=_due_schedule())
    seen = listen(engine, "dashboard.schedule_stopped")
    later = NOW + timedelta(hours=2)

    done = engine.dashboards.run_scheduled(now=later)

    first = engine.dashboards.get("d1", user_context=ANA).schedule
    assert first.state == "stopped" and first.next_at is None
    assert first.error["code"] == "SCHEDULE_ROLE_REMOVED" and "retired" in first.error["message"]
    assert [d.dashboard_id for d in done] == ["d2"], "the other dashboard still ran"
    assert not [c for c in runs.calls if "retired" in c["user_context"].roles], "nothing ran as the stopped one"
    assert [e.data for e in seen] == [{"dashboard_id": "d1", "reason": "SCHEDULE_ROLE_REMOVED"}]
    view = engine.dashboards.open("d1", user_context=ANA, refresh_due=False)
    assert view.dashboard.schedule.error["code"] == "SCHEDULE_ROLE_REMOVED", "open shows why"


def test_a_stopped_schedule_is_not_retried(engine, runs):
    seed(engine, schedule=_due_schedule(roles=("retired",)))
    later = NOW + timedelta(hours=2)
    engine.dashboards.run_scheduled(now=later)

    assert engine.dashboards.run_scheduled(now=later + timedelta(days=1)) == []
    assert runs.calls == []


def test_a_chart_the_owner_can_no_longer_read_stops_the_schedule(engine, runs):
    seed(engine, schedule=_due_schedule())
    runs.respond = lambda call: (failed("SECURITY_VIOLATION", "logical_validator", "No access to orders.")
                                 if call["question"] == "What is t2?" else good())
    seen = listen(engine, "dashboard.schedule_stopped")

    [done] = engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2))

    schedule = engine.dashboards.get("d1", user_context=ANA).schedule
    assert [t.status for t in done.tiles] == ["ok", "denied"]
    assert schedule.state == "stopped" and schedule.error["code"] == "SCHEDULE_ACCESS_DENIED"
    assert "Chart t2" in schedule.error["message"]
    assert [e.data["reason"] for e in seen] == ["SCHEDULE_ACCESS_DENIED"]


def test_a_chart_denied_while_another_part_waits_for_approval_stops_the_schedule(engine, runs):
    seed(engine, tiles=[tile("t1", plan=chart_plan(queries=2))], schedule=_due_schedule())
    put_run(engine, "t1")
    runs.respond = lambda call: _denied_and_asking()

    [done] = engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2))

    assert done.tiles[0].status == "denied" and done.tiles[0].error["code"] == "SECURITY_VIOLATION"
    assert stored(engine)["t1"].status == "denied" and stored(engine)["t1"].chart is None, "the old rows are gone"
    assert engine.dashboards.get("d1", user_context=ANA).schedule.error["code"] == "SCHEDULE_ACCESS_DENIED"


def test_a_deny_from_another_hook_stops_the_schedule_with_its_own_code_and_message(engine, runs):
    seed(engine, schedule=_due_schedule())
    runs.respond = lambda call: (failed("HOOK_DENIED", "hooks", "Closed for the audit.")
                                 if call["question"] == "What is t2?" else good())
    seen = listen(engine, "dashboard.schedule_stopped")

    engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2))

    schedule = engine.dashboards.get("d1", user_context=ANA).schedule
    assert schedule.state == "stopped" and schedule.error["code"] == "SCHEDULE_HOOK_DENIED"
    assert "Chart t2" in schedule.error["message"] and "Closed for the audit." in schedule.error["message"]
    assert "can no longer read" not in schedule.error["message"]
    assert [e.data["reason"] for e in seen] == ["SCHEDULE_HOOK_DENIED"]


def test_run_scheduled_refuses_a_naive_now(engine, runs):
    seed(engine, schedule=_due_schedule())

    with pytest.raises(ValueError, match="timezone"):
        engine.dashboards.run_scheduled(now=datetime.now())
    assert runs.calls == []


def test_a_failed_refresh_keeps_the_last_good_result_and_returns_the_error(engine, runs):
    seed(engine, schedule=_due_schedule())
    kept = put_run(engine, "t1")
    runs.respond = lambda call: failed("EXECUTION_FAILED", "executor", "The database is down.")

    manual = engine.dashboards.refresh("d1", user_context=ANA)
    [scheduled] = engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2))

    for run in (manual, scheduled):
        assert [t.status for t in run.tiles] == ["error", "error"]
        assert run.tiles[0].error == {"code": "EXECUTION_FAILED", "message": "The database is down."}
    assert stored(engine) == {"t1": kept}, "a transient failure never wipes good rows"


def test_schedule_stops_when_the_identity_snapshot_expires(engine, runs):
    seed(engine, schedule=_due_schedule(set_at=NOW - timedelta(days=31)))
    seen = listen(engine, "dashboard.schedule_stopped")

    assert engine.dashboards.run_scheduled(now=NOW) == []

    schedule = engine.dashboards.get("d1", user_context=ANA).schedule
    assert schedule.state == "stopped" and schedule.error["code"] == "SCHEDULE_IDENTITY_EXPIRED"
    assert "30 days" in schedule.error["message"] and runs.calls == []
    assert [e.data["reason"] for e in seen] == ["SCHEDULE_IDENTITY_EXPIRED"]


def test_setting_the_schedule_again_saves_a_fresh_snapshot_and_runs_again(engine, runs):
    seed(engine, schedule=_due_schedule(set_at=NOW - timedelta(days=31)))
    engine.dashboards.run_scheduled(now=NOW)

    again = engine.dashboards.set_schedule("d1", 60, user_context=ANA).schedule

    assert again.state == "active" and again.error is None and again.identity.roles == ["analyst", "sales"]
    assert [d.dashboard_id for d in engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2))] == ["d1"]


def test_scheduled_refresh_never_approves(engine, runs):
    seed(engine, schedule=_due_schedule())
    runs.respond = lambda call: QueryResult(status="needs_approval", trace_id="tr-n", approvals_needed=[request()])

    [done] = engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2))

    assert all(not call["approvals"] for call in runs.calls)
    assert [t.status for t in done.tiles] == ["needs_approval", "needs_approval"]


def test_schedule_skips_a_chart_waiting_for_approval(engine, runs):
    seed(engine, schedule=_due_schedule())
    kept = put_run(engine, "t1")
    runs.respond = lambda call: (QueryResult(status="needs_approval", trace_id="tr-n", approvals_needed=[request()])
                                 if call["question"] == "What is t1?" else good())
    later = NOW + timedelta(hours=2)

    engine.dashboards.run_scheduled(now=later)

    schedule = engine.dashboards.get("d1", user_context=ANA).schedule
    assert schedule.state == "active" and schedule.next_at == later + timedelta(minutes=60), "the schedule goes on"
    assert stored(engine)["t1"] == kept and stored(engine)["t2"].status == "ok"


def test_allow_all_has_no_roles_to_lose(make, runs):
    engine = make(policies={"allow_all": True})
    seed(engine, schedule=_due_schedule(roles=()))

    assert [d.dashboard_id for d in engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2))] == ["d1"]


def test_run_scheduled_survives_a_chart_that_cannot_be_drawn(engine, runs, monkeypatch):
    seed(engine, schedule=_due_schedule())

    def broken(api, result, result_id=None, *, user_context, kind=None):
        raise RuntimeError("No parquet.")

    monkeypatch.setattr(ResultsAPI, "chart", broken)

    [done] = engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2))

    assert [t.status for t in done.tiles] == ["error", "error"] and "No parquet." in done.tiles[0].error["message"]


def test_run_scheduled_runs_in_the_same_lock_a_refresh_holds(engine, runs):
    seed(engine, schedule=_due_schedule())
    gate = threading.Event()
    seen: List[Any] = []

    def hold():
        if not seen:
            seen.append(engine.dashboards.run_scheduled(now=NOW + timedelta(hours=2)))
            gate.set()

    runs.gate = hold

    engine.dashboards.refresh("d1", user_context=ANA)

    assert gate.is_set() and seen == [[]], "a scheduled run inside a manual refresh is skipped, not queued"
