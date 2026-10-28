"""Workspaces, the dashboard store and manual edits (docs/architecture/wave_c.md, section 6; row D1).

Nothing here runs a chart: D1 stores dashboards and edits them by hand. Dashboards are put straight into the
state store (a draft comes from chat in D4, the starter from D3). The engine has a fake model server that
records every call, so a test can say that an edit made none.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, List

import pytest

from nl2sql import NL2SQL, UserContext
from nl2sql.api.dashboards_api import DashboardsAPI
from nl2sql.api.workspaces_api import WorkspacesAPI
from nl2sql.charts.spec import ChartData, ChartSpec, Encoding
from nl2sql.common.errors import DashboardNotFound, ScheduleTooFrequent, WorkspaceNotEmpty, WorkspaceNotFound
from nl2sql.dashboards.models import (
    Arrange, ChartPlan, Dashboard, DashboardFilter, EDIT_KINDS, RemoveTile, RenameDashboard, RenameTile, SetFilters,
    Tile, TileArrangement, TileLayout, TileRun,
)
from nl2sql.dashboards.store import DashboardStore
from nl2sql.hooks.approval import VerifiedRef
from nl2sql.testing.config import make_config
from nl2sql.testing.fake_llm import FakeLLMServer

ANA = UserContext(user_id="ana", tenant_id="acme", roles=["analyst", "sales"])
BOB = UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])
NOW = datetime.now(timezone.utc)


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


def tile(tid: str, *, x: int = 0, y: int = 0, w: int = 2, h: int = 2) -> Tile:
    return Tile(id=tid, title=f"Chart {tid}", question=f"What is {tid}?",
                plan=ChartPlan(verified=VerifiedRef(id=f"v_{tid}")), layout=TileLayout(x=x, y=y, w=w, h=h))


def seed(engine: NL2SQL, did: str = "d1", *, user: UserContext = ANA, status: str = "draft", workspace: str = "ws",
         tiles: Any = None, days_ago: float = 0, **fields: Any) -> Dashboard:
    """A dashboard in the state store, owned by ``user`` (not through the facade: D3 and D4 build them)."""
    if tiles is None:
        tiles = [tile("t1"), tile("t2", x=2)]
    stamp = NOW - timedelta(days=days_ago)
    dashboard = Dashboard(id=did, title=f"Dashboard {did}", workspace_id=workspace, status=status,
                          tenant_id=user.tenant_id, user_id=user.user_id, tiles=tiles, created_at=stamp,
                          updated_at=stamp, **fields)
    DashboardStore(engine._runtime.state).put(dashboard)
    return dashboard


def workspace(engine: NL2SQL, name: str = "Sales", user: UserContext = ANA):
    return engine.workspaces.create(name, user_context=user)


def stored_runs(engine: NL2SQL, did: str = "d1") -> List[str]:
    return [doc["tile_id"] for doc in engine._runtime.state.get_tile_runs(did)]


def put_run(engine: NL2SQL, tile_id: str, *, chart: ChartSpec = None, did: str = "d1") -> None:
    run = TileRun(tile_id=tile_id, at=NOW, status="ok", chart=chart)
    engine._runtime.state.put_tile_runs(did, [run.model_dump(mode="json")])


def bar() -> ChartSpec:
    return ChartSpec(kind="bar", title="Orders by city", reason="one category and one number",
                     x=Encoding(field="city", kind="text", title="City"),
                     y=[Encoding(field="n", kind="number", title="N")],
                     data=ChartData(trace_id="tr", result_id="r1", columns=["city", "n"],
                                    rows=[["Paris", 3], ["Oslo", 1]], truncated=False))


def listen(engine: NL2SQL, name: str) -> List[Any]:
    seen: List[Any] = []
    engine.events.subscribe(seen.append, names=[name])
    return seen


# ---------------------------------------------------------------- workspaces

def test_workspace_create_rename_list(engine):
    seen = listen(engine, "workspace.changed")
    sales = workspace(engine, "Sales")
    ops = workspace(engine, "Ops")
    assert (sales.tenant_id, sales.user_id) == ("acme", "ana")

    renamed = engine.workspaces.rename(sales.id, "Revenue", user_context=ANA)

    assert renamed.name == "Revenue" and renamed.id == sales.id
    assert [w.name for w in engine.workspaces.list(user_context=ANA)] == ["Revenue", "Ops"]
    assert [e.data["change"] for e in seen] == ["created", "created", "renamed"]
    assert {e.data["by"] for e in seen} == {"ana"}
    with pytest.raises(ValueError, match="already has a workspace named 'ops'"):
        engine.workspaces.create("ops", user_context=ANA)
    with pytest.raises(ValueError, match="already has a workspace"):
        engine.workspaces.rename(sales.id, "Ops", user_context=ANA)
    with pytest.raises(ValueError, match="name"):
        engine.workspaces.create("   ", user_context=ANA)
    assert engine.workspaces.rename(ops.id, "Ops", user_context=ANA).name == "Ops", "its own name is not a clash"
    assert workspace(engine, "Sales", BOB), "the same name for another owner is fine"


def test_workspace_delete_needs_it_empty(engine):
    seen = listen(engine, "workspace.changed")
    full, empty = workspace(engine, "Full"), workspace(engine, "Empty")
    seed(engine, workspace=full.id, status="draft")

    with pytest.raises(WorkspaceNotEmpty):
        engine.workspaces.delete(full.id, user_context=ANA)
    engine.workspaces.delete(empty.id, user_context=ANA)

    assert [w.name for w in engine.workspaces.list(user_context=ANA)] == ["Full"]
    assert seen[-1].data["change"] == "deleted" and seen[-1].data["workspace_id"] == empty.id
    engine.dashboards.delete("d1", user_context=ANA)
    engine.workspaces.delete(full.id, user_context=ANA)
    assert engine.workspaces.list(user_context=ANA) == []


def test_other_users_workspace_or_dashboard_is_not_found(engine):
    mine = workspace(engine)
    seed(engine, workspace=mine.id)

    assert engine.workspaces.list(user_context=BOB) == []
    for call in (lambda: engine.workspaces.rename(mine.id, "X", user_context=BOB),
                 lambda: engine.workspaces.delete(mine.id, user_context=BOB)):
        with pytest.raises(WorkspaceNotFound):
            call()
    for call in (lambda: engine.dashboards.get("d1", user_context=BOB),
                 lambda: engine.dashboards.delete("d1", user_context=BOB),
                 lambda: engine.dashboards.rename("d1", "X", user_context=BOB),
                 lambda: engine.dashboards.pin("d1", user_context=BOB),
                 lambda: engine.dashboards.set_schedule("d1", 60, user_context=BOB)):
        with pytest.raises(DashboardNotFound):
            call()
    with pytest.raises(DashboardNotFound):
        engine.dashboards.get("nope", user_context=ANA)
    theirs = workspace(engine, "Theirs", BOB)
    with pytest.raises(WorkspaceNotFound):
        engine.dashboards.move("d1", theirs.id, user_context=ANA)
    with pytest.raises(WorkspaceNotFound):
        engine.dashboards.pin("d1", workspace_id=theirs.id, user_context=ANA)
    assert engine.dashboards.list(user_context=BOB, status="draft") == []


# ---------------------------------------------------------------- the store, pin and move

def test_get_list_and_delete(engine):
    sales = workspace(engine)
    other = workspace(engine, "Other")
    seed(engine, "old", status="saved", workspace=sales.id, days_ago=2)
    seed(engine, "new", status="saved", workspace=other.id, days_ago=1)
    seed(engine, "wip", status="draft", workspace=sales.id)
    put_run(engine, "t1", did="old")
    seen = listen(engine, "dashboard.changed")

    assert [i.id for i in engine.dashboards.list(user_context=ANA)] == ["new", "old"], "saved, newest first"
    assert [i.id for i in engine.dashboards.list(user_context=ANA, status="draft")] == ["wip"]
    assert [i.id for i in engine.dashboards.list(user_context=ANA, workspace_id=sales.id)] == ["old"]
    info = engine.dashboards.list(user_context=ANA, workspace_id=sales.id)[0]
    assert (info.title, info.tiles, info.status, info.workspace_id) == ("Dashboard old", 2, "saved", sales.id)
    assert info.refreshed_at is not None and engine.dashboards.list(user_context=ANA)[0].refreshed_at is None
    assert engine.dashboards.get("wip", user_context=ANA).status == "draft"
    with pytest.raises(ValueError, match="status"):
        engine.dashboards.list(user_context=ANA, status="everything")

    engine.dashboards.delete("old", user_context=ANA)

    assert stored_runs(engine, "old") == []
    assert [i.id for i in engine.dashboards.list(user_context=ANA)] == ["new"]
    assert (seen[-1].data["dashboard_id"], seen[-1].data["change"], seen[-1].data["by"]) == ("old", ["deleted"], "ana")


def test_pin_saves_a_draft_whole(engine):
    seen = listen(engine, "dashboard.changed")
    seed(engine, "d1", workspace="gone")
    before = engine.dashboards.get("d1", user_context=ANA)

    pinned = engine.dashboards.pin("d1", user_context=ANA, title="  Revenue  ")

    assert pinned.status == "saved" and pinned.title == "Revenue"
    assert pinned.tiles == before.tiles, "pinned whole: every chart, unchanged"
    [general] = engine.workspaces.list(user_context=ANA)
    assert general.name == "General" and pinned.workspace_id == general.id, "created on first use"
    assert pinned.updated_at > before.updated_at
    assert [e.data["change"] for e in seen] == [["pinned"]] and seen[0].data["by"] == "ana"
    with pytest.raises(ValueError, match="already saved"):
        engine.dashboards.pin("d1", user_context=ANA)

    seed(engine, "d2", workspace="gone")
    assert engine.dashboards.pin("d2", user_context=ANA).workspace_id == general.id, "General is reused"
    assert len(engine.workspaces.list(user_context=ANA)) == 1


def test_pin_goes_to_the_workspace_given_or_the_one_the_draft_is_in(engine):
    sales, ops = workspace(engine, "Sales"), workspace(engine, "Ops")
    seed(engine, "a", workspace=sales.id)
    seed(engine, "b", workspace=sales.id)

    assert engine.dashboards.pin("a", user_context=ANA).workspace_id == sales.id
    assert engine.dashboards.pin("b", workspace_id=ops.id, user_context=ANA).workspace_id == ops.id
    assert [w.name for w in engine.workspaces.list(user_context=ANA)] == ["Sales", "Ops"], "no General needed"


def test_an_empty_draft_cannot_be_pinned(engine):
    seed(engine, tiles=[])

    with pytest.raises(ValueError, match="empty"):
        engine.dashboards.pin("d1", user_context=ANA)
    assert engine.dashboards.get("d1", user_context=ANA).status == "draft"


def test_no_method_pins_a_single_tile():
    assert [n for n in inspect.signature(DashboardsAPI.pin).parameters if "tile" in n] == []
    assert not [n for n in dir(DashboardsAPI) if "pin" in n and n != "pin"]


def test_dashboard_lives_in_one_workspace_and_can_move(engine):
    seen = listen(engine, "dashboard.changed")
    sales, ops = workspace(engine, "Sales"), workspace(engine, "Ops")
    seed(engine, status="saved", workspace=sales.id)

    moved = engine.dashboards.move("d1", ops.id, user_context=ANA)

    assert moved.workspace_id == ops.id
    assert engine.dashboards.get("d1", user_context=ANA).workspace_id == ops.id
    assert [i.id for i in engine.dashboards.list(user_context=ANA, workspace_id=sales.id)] == []
    assert [i.id for i in engine.dashboards.list(user_context=ANA, workspace_id=ops.id)] == ["d1"]
    assert seen[-1].data["change"] == ["move"]
    with pytest.raises(WorkspaceNotFound):
        engine.dashboards.move("d1", "nope", user_context=ANA)


# ---------------------------------------------------------------- manual edits

def test_rename_dashboard_and_tile(engine):
    seed(engine)

    engine.dashboards.rename("d1", "  Revenue  ", user_context=ANA)
    result = engine.dashboards.rename_tile("d1", "t2", "Orders", user_context=ANA)

    assert result.title == "Revenue"
    assert [t.title for t in result.tiles] == ["Chart t1", "Orders"]
    assert engine.dashboards.get("d1", user_context=ANA) == result
    for bad in (lambda: engine.dashboards.rename("d1", " ", user_context=ANA),
                lambda: engine.dashboards.rename_tile("d1", "t2", "", user_context=ANA)):
        with pytest.raises(ValueError, match="title"):
            bad()
    with pytest.raises(ValueError, match="'zz'"):
        engine.dashboards.rename_tile("d1", "zz", "X", user_context=ANA)


def test_arrange_reorders_and_resizes(engine):
    seed(engine, tiles=[tile("t1", x=0, y=0), tile("t2", x=2, y=0), tile("t3", x=0, y=2)])

    result = engine.dashboards.arrange("d1", [TileArrangement(tile_id="t3", x=0, y=0, w=4, h=1),
                                              TileArrangement(tile_id="t1", x=0, y=1, w=2, h=3)],
                                       user_context=ANA)

    assert [t.id for t in result.tiles] == ["t3", "t2", "t1"], "kept in layout order: by row, then column"
    layouts = {t.id: t.layout for t in result.tiles}
    assert layouts["t3"] == TileLayout(x=0, y=0, w=4, h=1) and layouts["t1"] == TileLayout(x=0, y=1, w=2, h=3)
    assert layouts["t2"] == TileLayout(x=2, y=0, w=2, h=2), "a chart not named stays where it is"
    with pytest.raises(ValueError, match="'zz'"):
        engine.dashboards.arrange("d1", [TileArrangement(tile_id="zz", x=0, y=0, w=1, h=1)], user_context=ANA)
    with pytest.raises(ValueError, match="more than once"):
        engine.dashboards.arrange("d1", [TileArrangement(tile_id="t1", x=0, y=0, w=1, h=1),
                                         TileArrangement(tile_id="t1", x=1, y=0, w=1, h=1)], user_context=ANA)
    with pytest.raises(ValueError):
        TileArrangement(tile_id="t1", x=3, y=0, w=2, h=1)  # leaves the 4-column grid


def test_remove_tile(engine):
    seed(engine)

    result = engine.dashboards.remove_tile("d1", "t1", user_context=ANA)

    assert [t.id for t in result.tiles] == ["t2"]
    with pytest.raises(ValueError, match="'t1'"):
        engine.dashboards.remove_tile("d1", "t1", user_context=ANA)


def test_set_tile_chart_the_data_cannot_draw_is_an_error(engine):
    seed(engine)
    assert engine.dashboards.set_tile_chart("d1", "t1", "line", user_context=ANA).tiles[0].chart == "line", \
        "no result stored yet: nothing to check against"
    put_run(engine, "t1", chart=bar())

    with pytest.raises(ValueError, match=r"cannot be drawn as 'line'; it can be drawn as: bar, table"):
        engine.dashboards.set_tile_chart("d1", "t1", "line", user_context=ANA)
    assert engine.dashboards.get("d1", user_context=ANA).tiles[0].chart == "line", "refused: unchanged"
    assert engine.dashboards.set_tile_chart("d1", "t1", "table", user_context=ANA).tiles[0].chart == "table"
    assert engine.dashboards.set_tile_chart("d1", "t1", "bar", user_context=ANA).tiles[0].chart == "bar"
    assert engine.dashboards.set_tile_chart("d1", "t1", "auto", user_context=ANA).tiles[0].chart == "auto"
    assert engine.dashboards.set_tile_chart("d1", "t2", "big_number", user_context=ANA).tiles[1].chart == \
        "big_number", "t2 has no stored result"


def test_set_filters_stores_the_definitions(engine):
    seed(engine)
    region = DashboardFilter(id="f1", label="Region", column="shop.main.orders.region", kind="text", op="in")
    when = DashboardFilter(id="f2", label="Date", column="shop.main.orders.created", kind="temporal", op="between")

    result = engine.dashboards.set_filters("d1", [region, when], user_context=ANA)

    assert result.filters == [region, when] and engine.dashboards.get("d1", user_context=ANA).filters == [region, when]
    assert engine.dashboards.set_filters("d1", [], user_context=ANA).filters == []
    for bad, message in (([region, region], "twice"),
                         ([region.model_copy(update={"column": "orders.region"})], "datasource.schema.table.column"),
                         ([when.model_copy(update={"kind": "text"})], "between")):
        with pytest.raises(ValueError, match=message):
            engine.dashboards.set_filters("d1", bad, user_context=ANA)


def test_edits_apply_all_or_none(engine):
    seed(engine)
    seen = listen(engine, "dashboard.changed")
    before = engine.dashboards.get("d1", user_context=ANA)

    with pytest.raises(ValueError, match="'zz'"):
        engine.dashboards.edit("d1", [RenameDashboard(title="New"), RemoveTile(tile_id="t1"),
                                      RenameTile(tile_id="zz", title="X")], user_context=ANA)

    assert engine.dashboards.get("d1", user_context=ANA) == before, "the first two edits were not kept"
    assert seen == []

    after = engine.dashboards.edit("d1", [RenameDashboard(title="New"), RemoveTile(tile_id="t1"),
                                          RenameTile(tile_id="t2", title="Two")], user_context=ANA)

    assert (after.title, [(t.id, t.title) for t in after.tiles]) == ("New", [("t2", "Two")])
    assert [e.data["change"] for e in seen] == [["rename", "remove_tile", "rename_tile"]], "kinds, never values"
    assert engine.dashboards.edit("d1", [], user_context=ANA) == after and len(seen) == 1, "nothing to do"


def test_edits_see_the_dashboard_the_earlier_edits_made(engine):
    seed(engine)

    after = engine.dashboards.edit("d1", [RemoveTile(tile_id="t1"), Arrange(layouts=[
        TileArrangement(tile_id="t2", x=0, y=0, w=4, h=2)])], user_context=ANA)
    assert [t.layout.w for t in after.tiles] == [4]
    with pytest.raises(ValueError, match="'t2'"):
        engine.dashboards.edit("d1", [RemoveTile(tile_id="t2"), RenameTile(tile_id="t2", title="X")],
                               user_context=ANA)


def test_the_chat_only_edits_arrive_with_d4(engine):
    from nl2sql.dashboards.models import AddTile, StepRef

    seed(engine)
    with pytest.raises(NotImplementedError, match=r"add_tile.*arrives with D4"):
        engine.dashboards.edit("d1", [AddTile(title="X", from_step=StepRef(turn=1, step=1))], user_context=ANA)


def test_edit_discards_only_the_results_it_invalidates(engine):
    seed(engine)

    def put_both():
        for tile_id in ("t1", "t2"):
            put_run(engine, tile_id)

    put_both()
    engine.dashboards.rename("d1", "X", user_context=ANA)
    engine.dashboards.rename_tile("d1", "t1", "X", user_context=ANA)
    engine.dashboards.set_tile_chart("d1", "t1", "table", user_context=ANA)
    engine.dashboards.arrange("d1", [TileArrangement(tile_id="t1", x=0, y=3, w=2, h=2)], user_context=ANA)
    engine.dashboards.set_schedule("d1", 60, user_context=ANA)
    assert stored_runs(engine) == ["t1", "t2"], "a stored result stays valid across these"

    engine.dashboards.remove_tile("d1", "t1", user_context=ANA)
    assert stored_runs(engine) == ["t2"]

    seed(engine)  # both charts again
    put_both()
    flt = DashboardFilter(id="f1", label="R", column="a.b.c.d", kind="text", op="in")
    engine.dashboards.set_filters("d1", [flt], user_context=ANA)
    assert stored_runs(engine) == [], "a filter changes what every chart shows"

    put_run(engine, "t2")
    engine.dashboards.set_filters("d1", [flt], user_context=ANA)
    assert stored_runs(engine) == ["t2"], "the same filters change nothing"


# ---------------------------------------------------------------- schedules

def test_schedule_below_the_minimum_is_refused(make):
    engine = make(dashboards={"min_refresh_minutes": 30})
    seed(engine)

    with pytest.raises(ScheduleTooFrequent, match="30"):
        engine.dashboards.set_schedule("d1", 29, user_context=ANA)

    assert engine.dashboards.get("d1", user_context=ANA).schedule is None
    assert engine.dashboards.set_schedule("d1", 30, user_context=ANA).schedule.every_minutes == 30


def test_set_schedule_saves_the_callers_identity(engine):
    seen = listen(engine, "dashboard.schedule_set")
    seed(engine)
    before = datetime.now(timezone.utc)

    schedule = engine.dashboards.set_schedule("d1", 60, user_context=ANA).schedule

    assert schedule.identity.model_dump() == {"user_id": "ana", "tenant_id": "acme", "roles": ["analyst", "sales"]}
    assert schedule.state == "active" and schedule.error is None
    assert before <= schedule.set_at and schedule.next_at == schedule.set_at + timedelta(minutes=60)
    assert engine.dashboards.get("d1", user_context=ANA).schedule == schedule
    assert [(e.data["dashboard_id"], e.data["every_minutes"], e.data["by"]) for e in seen] == [("d1", 60, "ana")]
    assert engine._runtime.state.list_scheduled_dashboards()[0]["id"] == "d1"

    engine.dashboards.set_schedule("d1", None, user_context=ANA)

    assert engine.dashboards.get("d1", user_context=ANA).schedule is None
    assert engine._runtime.state.list_scheduled_dashboards() == []
    assert seen[-1].data["every_minutes"] is None


def test_set_schedule_changes_only_the_schedule_and_runs_nothing(engine, server):
    seed(engine, status="saved")
    put_run(engine, "t1")

    engine.dashboards.set_schedule("d1", 60, user_context=ANA)

    assert stored_runs(engine) == ["t1"] and server.calls == []


# ---------------------------------------------------------------- no model, no question, no plan

def test_manual_edits_make_no_model_call(engine, server):
    ops = workspace(engine, "Ops")
    seed(engine)
    ws = "d1"

    engine.dashboards.rename(ws, "A", user_context=ANA)
    engine.dashboards.rename_tile(ws, "t1", "B", user_context=ANA)
    engine.dashboards.set_tile_chart(ws, "t1", "bar", user_context=ANA)
    engine.dashboards.arrange(ws, [TileArrangement(tile_id="t1", x=0, y=4, w=1, h=1)], user_context=ANA)
    engine.dashboards.set_filters(ws, [], user_context=ANA)
    engine.dashboards.set_schedule(ws, 60, user_context=ANA)
    engine.dashboards.remove_tile(ws, "t2", user_context=ANA)
    engine.dashboards.move(ws, ops.id, user_context=ANA)
    engine.dashboards.pin(ws, user_context=ANA)
    engine.dashboards.delete(ws, user_context=ANA)

    assert server.calls == []


def test_manual_edit_methods_take_no_question_or_plan():
    named = ["rename", "rename_tile", "set_tile_chart", "remove_tile", "arrange", "set_filters", "set_schedule",
             "move", "pin", "edit"]
    for name in named:
        parameters = set(inspect.signature(getattr(DashboardsAPI, name)).parameters)
        assert not parameters & {"question", "plan", "plans", "decomposition", "from_step", "sql"}, name
    # and the edits a facade method builds are exactly the ones that change no data
    chat_only = {"add_tile", "replace_plan"}
    assert set(EDIT_KINDS) - chat_only == {"rename", "rename_tile", "set_tile_chart", "remove_tile", "arrange",
                                           "set_filters", "set_schedule", "move"}
    assert all(hasattr(DashboardsAPI, kind) for kind in set(EDIT_KINDS) - chat_only)


def test_dashboard_and_workspace_methods_require_a_caller():
    for cls in (DashboardsAPI, WorkspacesAPI):
        for name, method in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("_") or name == "run_scheduled":
                continue
            parameter = inspect.signature(method).parameters["user_context"]
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default is parameter.empty, name


def test_a_missing_caller_is_refused_not_treated_as_anonymous(engine):
    seed(engine, status="saved")
    calls = [
        lambda: engine.workspaces.create("X", user_context=None),
        lambda: engine.workspaces.rename("ws", "X", user_context=None),
        lambda: engine.workspaces.list(user_context=None),
        lambda: engine.workspaces.delete("ws", user_context=None),
        lambda: engine.dashboards.get("d1", user_context=None),
        lambda: engine.dashboards.list(user_context=None),
        lambda: engine.dashboards.delete("d1", user_context=None),
        lambda: engine.dashboards.pin("d1", user_context=None),
        lambda: engine.dashboards.rename("d1", "X", user_context=None),
        lambda: engine.dashboards.move("d1", "ws", user_context=None),
        lambda: engine.dashboards.set_schedule("d1", 60, user_context=None),
    ]
    for call in calls:
        with pytest.raises(TypeError, match="user_context is required"):
            call()


def test_pin_with_a_blank_title_is_refused_like_rename(engine):
    seed(engine)

    with pytest.raises(ValueError, match="title"):
        engine.dashboards.pin("d1", user_context=ANA, title="  ")

    assert engine.dashboards.get("d1", user_context=ANA).status == "draft"


def test_a_failed_drop_leaves_the_dashboard_and_its_results_as_they_were(engine, monkeypatch):
    seed(engine)
    put_run(engine, "t1")

    def boom(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(engine._runtime.state, "delete_tile_runs", boom)
    flt = DashboardFilter(id="f1", label="R", column="a.b.c.d", kind="text", op="in")

    with pytest.raises(RuntimeError, match="store down"):
        engine.dashboards.set_filters("d1", [flt], user_context=ANA)

    assert engine.dashboards.get("d1", user_context=ANA).filters == [], "the filters were not saved"
    assert stored_runs(engine) == ["t1"]


def test_a_table_result_can_be_forced_to_a_bar_from_its_stored_column_kinds(engine):
    seed(engine)
    table = bar().model_copy(update={"kind": "table", "x": None, "y": [], "reason": "forced"})
    run = TileRun(tile_id="t1", at=NOW, status="ok", chart=table, column_kinds={"city": "text", "n": "number"})
    engine._runtime.state.put_tile_runs("d1", [run.model_dump(mode="json")])

    assert engine.dashboards.set_tile_chart("d1", "t1", "bar", user_context=ANA).tiles[0].chart == "bar"
    with pytest.raises(ValueError, match="cannot be drawn as 'line'"):
        engine.dashboards.set_tile_chart("d1", "t1", "line", user_context=ANA)


def test_a_stored_result_without_column_kinds_falls_back_to_its_encodings(engine):
    old = TileRun.model_validate({"tile_id": "t1", "at": NOW.isoformat(), "status": "ok"})
    assert old.column_kinds == {}, "rows stored before the field existed still load"


# ---------------------------------------------------------------- retention

def test_purge_deletes_old_drafts_and_keeps_saved_dashboards(make):
    engine = make(conversations={"max_age_days": 7})
    seed(engine, "old_draft", status="draft", days_ago=8)
    seed(engine, "fresh_draft", status="draft", days_ago=1)
    seed(engine, "old_saved", status="saved", days_ago=400)
    put_run(engine, "t1", did="old_draft")
    seen = listen(engine, "retention.purged")

    counts = engine.storage.purge()

    assert counts["dashboards"] == 1 and seen[0].data["counts"]["dashboards"] == 1
    assert {i.id for i in engine.dashboards.list(user_context=ANA, status="draft")} == {"fresh_draft"}
    assert {i.id for i in engine.dashboards.list(user_context=ANA)} == {"old_saved"}
    assert stored_runs(engine, "old_draft") == []


def test_a_null_max_age_keeps_drafts(make):
    engine = make(conversations={"max_age_days": None})
    seed(engine, "ancient", status="draft", days_ago=4000)

    assert "dashboards" not in engine.storage.purge()
    assert engine.dashboards.get("ancient", user_context=ANA)
