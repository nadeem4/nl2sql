"""Dashboard refresh and open end to end on the demo databases, with no key (wave_c.md, row D2).

A chart keeps the typed plan that produced it. A refresh re-runs it through ``run_query(plan=..., answer=False)``
as the caller: the plan goes through ``plan.after``, the validator and the generator, is never cached, and makes no
model call. A stored result is served only after the same check passes under the caller's current roles.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nl2sql.hooks import Allow, NeedsApproval

from .conftest import fake_llm_section, private_storage, project_config
from .recordings_chinook import RULES_COUNT_CUSTOMERS
from .test_approval_fake_llm import COUNT, Review, _supplied
from .test_result_paging_fake_llm import _env


def _user(*roles):
    from nl2sql import UserContext

    return UserContext(user_id="ana", tenant_id="acme", roles=list(roles))


ANA = _user("analyst")
LOST = _user("viewer")  # the same owner, with a role that cannot read customers


def _build(demo_project, fake_llm, monkeypatch, hooks=(), curated=None):
    import yaml

    from nl2sql import NL2SQL
    from nl2sql.config import load_config

    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
    path = _env(monkeypatch, demo_project, env)
    if curated:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        document["curated"] = curated
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    config = load_config(path)
    return NL2SQL(config.model_copy(update={"hooks": list(hooks)}) if hooks else config), server


@pytest.fixture
def engine(demo_project, fake_llm, monkeypatch):
    built, server = _build(demo_project, fake_llm, monkeypatch)
    yield built, server
    built.close()


def _seed(engine, plan=None, did="d1", chart_plan=None):
    from nl2sql.dashboards.models import ChartPlan, Dashboard, Tile, TileLayout
    from nl2sql.dashboards.store import DashboardStore

    supplied = plan or _supplied()
    chart_plan = chart_plan or ChartPlan(decomposition=supplied.decomposition.model_dump(mode="json"),
                                         plans={k: v.model_dump(mode="json") for k, v in supplied.plans.items()})
    now = datetime.now(timezone.utc)
    tile = Tile(id="t1", title="Customers", question=COUNT, plan=chart_plan, layout=TileLayout(x=0, y=0, w=2, h=1))
    DashboardStore(engine._runtime.state).put(Dashboard(
        id=did, title="Customers", workspace_id="ws", status="saved", tenant_id="acme", user_id="ana",
        tiles=[tile], created_at=now, updated_at=now))


@pytest.mark.e2e
def test_refresh_runs_the_stored_plan_with_no_model_call_and_stores_the_result(engine):
    engine, server = engine
    _seed(engine)

    run = engine.dashboards.refresh("d1", user_context=ANA)

    [tile] = run.tiles
    assert tile.status == "ok", tile.error
    assert tile.chart.kind == "big_number" and tile.chart.data.rows == [[59]] and tile.chart.title == "Customers"
    assert tile.column_kinds == {"customer_count": "number"} and tile.trace_id
    assert server.calls == [], "not one model call: nothing is routed, decomposed, planned, refined or written"
    [stored] = engine.dashboards.open("d1", user_context=ANA).runs
    assert stored == tile


VIEWER = _user("viewer")  # the music catalog and anonymous traffic: no customers, invoices or employees


def _tables(dashboard):
    return {t["name"] for tile in dashboard.tiles for body in tile.plan.plans.values() for t in body["tables"]}


@pytest.mark.e2e
def test_generate_builds_a_starter_dashboard_through_the_real_pipeline_with_no_model_call(engine):
    engine, server = engine

    dashboard, run = engine.dashboards.generate("chinook", user_context=ANA)

    assert dashboard.status == "saved" and dashboard.tiles, run.skipped
    assert all(r.status == "ok" and r.chart is not None for r in run.tiles) and len(run.tiles) == len(dashboard.tiles)
    assert dashboard.tiles[0].question.startswith("How many rows are in")
    assert [t.layout.y for t in dashboard.tiles] == sorted(t.layout.y for t in dashboard.tiles)
    assert "Employee" not in _tables(dashboard), "the analyst role denies the employee table"
    assert server.calls == [], "not one model call"
    opened = engine.dashboards.open(dashboard.id, user_context=ANA)
    assert [r.status for r in opened.runs] == ["ok"] * len(dashboard.tiles)


@pytest.mark.e2e
def test_generate_as_a_narrower_role_charts_only_what_that_role_reads(engine):
    engine, server = engine

    dashboard, run = engine.dashboards.generate("chinook", user_context=VIEWER)

    assert dashboard.tiles and _tables(dashboard) <= {"Album", "Artist", "Genre", "MediaType", "Playlist",
                                                      "PlaylistTrack", "Track"}, _tables(dashboard)
    assert dashboard.user_id == "ana" and server.calls == []
    assert engine.dashboards.suggest_questions("chinook", user_context=VIEWER)[0].startswith("How many rows are in")


@pytest.mark.e2e
def test_stored_results_are_denied_once_access_is_lost(engine):
    engine, server = engine
    _seed(engine)
    engine.dashboards.refresh("d1", user_context=ANA)

    kept = engine.dashboards.open("d1", user_context=ANA).runs[0]
    lost = engine.dashboards.open("d1", user_context=LOST).runs[0]

    assert kept.status == "ok" and kept.chart.data.rows == [[59]]
    assert lost.status == "denied" and lost.chart is None and lost.error["code"] == "SECURITY_VIOLATION"
    assert server.calls == []


@pytest.mark.e2e
def test_open_checks_a_verified_chart_without_running_it(demo_project, fake_llm, monkeypatch):
    """D135: the access check executes nothing, a verified chart's included; it still denies once access is lost."""
    from nl2sql import VerifiedRef
    from nl2sql.dashboards.models import ChartPlan
    from nl2sql.execution.executor import SqlExecutorService

    from .test_curated_fake_llm import CURATED

    engine, server = _build(demo_project, fake_llm, monkeypatch, curated=CURATED)
    try:
        _seed(engine, chart_plan=ChartPlan(verified=VerifiedRef(id="top_customers_by_revenue",
                                                                params={"n": 5, "year": 2012}),
                                           verified_datasource_id="chinook"))
        [refreshed] = engine.dashboards.refresh("d1", user_context=ANA).tiles
        executed = []
        execute = SqlExecutorService.execute
        monkeypatch.setattr(SqlExecutorService, "execute",
                            lambda self, request: executed.append(request) or execute(self, request))

        kept = engine.dashboards.open("d1", user_context=ANA).runs[0]
        lost = engine.dashboards.open("d1", user_context=LOST).runs[0]
    finally:
        engine.close()

    assert refreshed.status == "ok" and len(refreshed.chart.data.rows) == 5
    assert kept == refreshed, "the stored rows are served after the check"
    assert lost.status == "denied" and lost.chart is None and lost.error["code"] == "SECURITY_VIOLATION"
    assert executed == [], "the check ran no query"
    assert server.calls == []


class _Gate:
    """A business rule at ``execute.before`` whose decision the test sets: it allows the refresh, then decides the open."""

    name, points = "gate", frozenset({"execute.before"})

    def __init__(self):
        self.decision = Allow()
        self.seen = []

    def __call__(self, ctx):
        self.seen.append(ctx.sql)
        return self.decision


def _open_a_verified_chart_behind(gate, demo_project, fake_llm, monkeypatch, decision):
    """Refresh a verified chart with ``gate`` allowing, then open it with ``gate`` deciding ``decision``."""
    from nl2sql import VerifiedRef
    from nl2sql.dashboards.models import ChartPlan
    from nl2sql.execution.executor import SqlExecutorService

    from .test_curated_fake_llm import CURATED

    engine, server = _build(demo_project, fake_llm, monkeypatch, hooks=[gate], curated=CURATED)
    try:
        _seed(engine, chart_plan=ChartPlan(verified=VerifiedRef(id="top_customers_by_revenue",
                                                                params={"n": 5, "year": 2012}),
                                           verified_datasource_id="chinook"))
        [refreshed] = engine.dashboards.refresh("d1", user_context=ANA).tiles
        executed = []
        execute = SqlExecutorService.execute
        monkeypatch.setattr(SqlExecutorService, "execute",
                            lambda self, request: executed.append(request) or execute(self, request))
        gate.decision, gate.seen = decision, []
        opened = engine.dashboards.open("d1", user_context=ANA).runs[0]
    finally:
        engine.close()
    assert refreshed.status == "ok", refreshed.error
    return refreshed, opened, executed, server


@pytest.mark.e2e
def test_open_runs_execute_before_for_a_verified_chart_and_a_deny_serves_no_rows(demo_project, fake_llm, monkeypatch):
    """A verified query has no plan: ``execute.before`` is its only check point, so the open asks it too."""
    from nl2sql.hooks import Deny

    gate = _Gate()
    _, opened, executed, server = _open_a_verified_chart_behind(
        gate, demo_project, fake_llm, monkeypatch, Deny(reason="Closed for the audit."))

    assert gate.seen, "the open asked the execute.before hook"
    assert opened.status == "denied" and opened.chart is None and opened.error["code"] == "HOOK_DENIED"
    assert executed == [] and server.calls == []


@pytest.mark.e2e
def test_open_lets_an_approval_requesting_hook_pass_a_verified_charts_check(demo_project, fake_llm, monkeypatch):
    """The check executes nothing, so a hook asking for approval before a run does not block serving the rows."""
    gate = _Gate()
    refreshed, opened, executed, server = _open_a_verified_chart_behind(
        gate, demo_project, fake_llm, monkeypatch, NeedsApproval(reason="Reads a lot."))

    assert gate.seen and opened == refreshed
    assert executed == [] and server.calls == []


@pytest.mark.e2e
def test_a_refresh_as_a_caller_who_lost_access_is_denied(engine):
    engine, _ = engine
    _seed(engine)

    [tile] = engine.dashboards.refresh("d1", user_context=LOST).tiles

    assert tile.status == "denied" and tile.chart is None


@pytest.mark.e2e
def test_a_plan_that_no_longer_validates_is_needs_fixing_and_the_refiner_is_never_asked(engine):
    engine, server = engine
    _seed(engine)
    document = engine._runtime.state.get_dashboard("d1")
    plan = document["tiles"][0]["plan"]["plans"]["sq1"]
    plan["select_items"][0]["expr"]["args"][0]["column_name"] = "NoSuchColumn"  # a column that was dropped
    engine._runtime.state.put_dashboard(document)

    [tile] = engine.dashboards.refresh("d1", user_context=ANA).tiles

    assert tile.status == "needs_fixing" and tile.chart is None
    assert tile.error["code"] == "TILE_NEEDS_FIXING" and "NoSuchColumn" in tile.error["message"]
    assert server.calls == [], "the validator's rejection ends the chart: the refiner would be a model call"


@pytest.mark.e2e
def test_tiles_pass_plan_after_and_the_validator(demo_project, fake_llm, monkeypatch):
    class Seen:
        name, points = "seen", frozenset({"plan.after"})

        def __init__(self):
            self.plans = []

        def __call__(self, ctx):
            self.plans.append(ctx.plan)
            return Allow()

    seen = Seen()
    engine, server = _build(demo_project, fake_llm, monkeypatch, hooks=[seen])
    try:
        _seed(engine)
        [tile] = engine.dashboards.refresh("d1", user_context=ANA).tiles
    finally:
        engine.close()

    assert tile.status == "ok" and [p.reasoning for p in seen.plans] == ["Count rows in Customer."]


@pytest.mark.e2e
def test_stored_plan_is_never_cached(demo_project, fake_llm, monkeypatch, tmp_path):
    from nl2sql import NL2SQL
    from nl2sql.config import load_config
    from nl2sql.testing.fake_llm import FakeLLMServer

    server = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    try:
        path = project_config(demo_project, "cache.yaml", llm=fake_llm_section(server.base_url),
                              storage=private_storage(demo_project, tmp_path / "cache-store"),
                              settings={"pipeline": {"plan_cache": True}})
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        engine = NL2SQL(load_config(path))
        try:
            _seed(engine)
            engine.dashboards.refresh("d1", user_context=ANA)
            first = engine.run_query(COUNT, user_context=ANA)
            second = engine.run_query(COUNT, user_context=ANA)
        finally:
            engine.close()
    finally:
        server.stop()

    assert first.sub_queries[0].plan_source == "llm", "the refresh left nothing in the cache to serve"
    assert second.sub_queries[0].plan_source == "cache", "the cache is on: only the model's own plan was stored"


@pytest.mark.e2e
def test_cost_guard_confirm_makes_a_chart_need_approval_and_a_manual_refresh_with_approvals_runs_it(
        demo_project, fake_llm, monkeypatch):
    from nl2sql import Approval, UserContext

    engine, server = _build(demo_project, fake_llm, monkeypatch, hooks=[Review()])
    try:
        _seed(engine)

        [asked] = engine.dashboards.refresh("d1", user_context=ANA).tiles
        opened = engine.dashboards.open("d1", user_context=ANA)

        assert asked.status == "needs_approval" and asked.chart is None
        [request] = asked.approvals_needed
        assert request.question == COUNT and request.asker_user_id == "ana" and "Customer" in request.sql
        assert opened.runs == [], "nothing was stored by the refresh that asked"

        approval = Approval(request=request, by=UserContext(user_id="root", roles=["admin"]))
        [approved] = engine.dashboards.refresh("d1", user_context=ANA, approvals=[approval]).tiles

        assert approved.status == "ok" and approved.chart.data.rows == [[59]]
        assert server.calls == []
    finally:
        engine.close()
