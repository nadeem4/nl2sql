"""Chat builds and edits dashboards (docs/architecture/wave_c.md, section 6; row D4).

The conversation agent is the ``FakeLLMServer`` of ``test_conversations.py``. Both runs a dashboard needs are
replaced by one function returning canned ``QueryResult``s: an ``ask`` step (it has a scope) and the no-execute
check of a chart's plan (it carries ``plan=`` and no scope), so a test can say what was asked and what was
checked. The real validator is held by ``tests/e2e/test_dashboards_fake_llm.py``. Nothing calls a real model.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Dict, List, get_args

import pytest

from nl2sql import NL2SQL
from nl2sql.api.dashboards_api import DashboardsAPI
from nl2sql.api.query_api import QueryAPI, QueryResult, RowSample, SubQueryResult
from nl2sql.chat_tools.dashboard_edit import DashboardEditArgs
from nl2sql.conversations.agent import step_view
from nl2sql.dashboards.models import (
    AddTile, Arrange, Dashboard, DashboardFilter, EDIT_KINDS, MoveDashboard, RemoveTile, RenameDashboard, RenameTile,
    ReplacePlan, SetFilters, SetSchedule, SetTileChart, StepRef, TileArrangement, TileRun,
)
from nl2sql.dashboards.store import DashboardStore
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel

# The fixtures and helpers of the conversation and dashboard tests.
from test_conversations import (ANA, BOB, _block, ask, engine, make, model, reply, server,  # noqa: F401
                                started)
from test_dashboards_d2 import NOW, chart_plan, seed, stored, tile


def plan_json(table: str = "orders") -> Dict[str, Any]:
    plan = PlanModel.model_validate({
        "tables": [{"name": table, "alias": "t1"}],
        "select_items": [{"alias": "n", "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                                                 "args": [{"kind": "column", "alias": "t1", "column_name": "id"}]}}]})
    return plan.model_dump(mode="json")


def answer(queries: int = 1, table: str = "orders", summary: str = "There are many.") -> QueryResult:
    """An ``ask`` step's run: its decomposition, a plan per sub-query, and rows that must never reach a dashboard."""
    sub_queries = [SubQuery(id=f"sq{i}", datasource_id="ds1", intent=f"Count {table} {i}")
                   for i in range(1, queries + 1)]
    decomposition = DecomposerResponse(sub_queries=sub_queries, combine_groups=[CombineGroup(
        group_id="g1", operation="standalone" if queries == 1 else "union",
        inputs=[CombineInput(subquery_id=s.id) for s in sub_queries])])
    return QueryResult(
        sub_queries=[SubQueryResult(id=s.id, datasource_id="ds1", status="ok", sql="SELECT 1", plan=plan_json(table),
                                    rows=RowSample(columns=["city", "n"], rows=[["Paris", 59]], total_rows=1))
                     for s in sub_queries],
        decomposition=decomposition,
        final_answer={"summary": summary, "format_type": "text", "content": summary, "warnings": []},
        result_manifest=[ResultSummary(node_id="sq1", columns=["city", "n"], row_count=1)], status="success")


def rejected(message: str = "Column 'orders.id' does not exist.") -> QueryResult:
    return QueryResult(status="error", trace_id="chk", errors=[{
        "node": "logical_validator", "error_code": "VALIDATION_FAILED", "message": message, "severity": "ERROR"}])


class Runs:
    """Stands in for ``QueryAPI.run_query``: an ask (it has a scope) answers ``respond``; the no-execute check of a
    chart's plan (it has none) answers ``verdict``."""

    def __init__(self, monkeypatch) -> None:
        self.asks: List[Dict[str, Any]] = []
        self.checks: List[Dict[str, Any]] = []
        self.respond: Callable[[str], QueryResult] = lambda question: answer()
        self.verdict: Callable[[Dict[str, Any]], QueryResult] = lambda call: QueryResult(status="plan_only",
                                                                                         trace_id="chk")
        runs = self

        def run_query(api: QueryAPI, question: str, **kwargs: Any) -> QueryResult:
            if "scope" not in kwargs:
                runs.checks.append({"question": question, **kwargs})
                return runs.verdict(runs.checks[-1])
            runs.asks.append({"question": question, **kwargs})
            scope = kwargs["scope"]
            result = runs.respond(question).model_copy(update={"trace_id": scope.trace_id})
            api.close_scope(scope, result)
            return result

        monkeypatch.setattr(QueryAPI, "run_query", run_query)


@pytest.fixture
def runs(monkeypatch):
    return Runs(monkeypatch)


def edit(*edits: Dict[str, Any], **args: Any) -> Dict[str, Any]:
    return {"call": {"name": "dashboard_edit", "args": {"edits": list(edits), **args}}}


def add(title: str, turn: int = 1, step: int = 1) -> Dict[str, Any]:
    return {"kind": "add_tile", "title": title, "from_step": {"turn": turn, "step": step}}


def pin(**args: Any) -> Dict[str, Any]:
    return {"call": {"name": "dashboard_pin", "args": args}}


def drafts(engine: NL2SQL, user=ANA):
    return engine.dashboards.list(user_context=user, status="draft")


def the_dashboard(engine: NL2SQL, conversation_id: str, user=ANA) -> Dashboard:
    conversation = engine.conversations.get(conversation_id, user_context=user)
    assert conversation.dashboard_id, "the conversation has no dashboard"
    return engine.dashboards.get(conversation.dashboard_id, user_context=user)


def tool(engine: NL2SQL, cid: str, name: str, args: Dict[str, Any], user=ANA):
    return engine.conversations.tool(cid, name, args, user_context=user)


def put_stored(engine: NL2SQL, run: TileRun, did: str = "d1") -> None:
    engine._runtime.state.put_tile_runs(did, [run.model_dump(mode="json")])


# ---------------------------------------------------------------- the model builds a dashboard

def test_model_builds_a_dashboard_from_several_ask_steps(engine, model, server, runs):
    cid = started(engine).id
    model.say(ask("How many orders are there per city?"), ask("How many customers are there per country?"),
              edit(add("Orders by city", 1, 1), add("Customers by country", 1, 2)),
              reply("I built a dashboard with both charts."))

    turn = engine.conversations.ask(cid, "Build me a dashboard of orders and customers", user_context=ANA)

    assert turn.status == "answered" and [s.call.name for s in turn.steps] == ["ask", "ask", "dashboard_edit"]
    board = the_dashboard(engine, cid)
    assert (board.status, board.conversation_id, board.user_id, board.tenant_id) == ("draft", cid, "ana", "acme")
    assert [(t.title, t.question) for t in board.tiles] == [
        ("Orders by city", "How many orders are there per city?"),
        ("Customers by country", "How many customers are there per country?")]
    assert board.tiles[0].plan.plans == {"sq1": plan_json()}
    assert board.tiles[0].plan.decomposition == runs.checks[0]["plan"].decomposition.model_dump(mode="json")
    assert turn.steps[2].output.refs == {"dashboard_id": board.id}
    # Each chart was checked, as the caller, running nothing and writing no answer; only the loop called the model.
    assert [(c["execute"], c["answer"], c["user_context"]) for c in runs.checks] == [(False, False, ANA)] * 2
    assert [c["name"] for c in server.calls] == ["ModelStep"] * 4
    assert stored(engine, board.id) == {}, "adding a chart runs nothing: the first refresh stores a result"



def test_add_tile_stores_the_whole_plan_of_a_multi_query_step(engine, model, runs):
    runs.respond = lambda question: answer(queries=2)
    cid = started(engine).id
    model.say(ask("Orders in both years?"), edit(add("Orders both years")), reply("Done."))

    engine.conversations.ask(cid, "Dashboard please", user_context=ANA)

    [only] = the_dashboard(engine, cid).tiles
    assert [s["id"] for s in only.plan.decomposition["sub_queries"]] == ["sq1", "sq2"]
    assert only.plan.decomposition["combine_groups"][0]["operation"] == "union"
    assert only.plan.plans == {"sq1": plan_json(), "sq2": plan_json()}
    [check] = runs.checks
    assert [s.id for s in check["plan"].decomposition.sub_queries] == ["sq1", "sq2"] and set(check["plan"].plans) == {"sq1", "sq2"}


def test_add_tile_stores_a_verified_answer_as_a_verified_chart(engine, model, runs):
    from nl2sql.api.query_api import AnswerSource

    runs.respond = lambda question: QueryResult(
        status="success", sub_queries=[SubQueryResult(id="sq1", datasource_id="ds1", status="ok", sql="SELECT 1",
                                                     plan_source="verified")],
        source=AnswerSource(type="verified", id="revenue", datasource_id="ds1", params={"year": 2024}))
    cid = started(engine).id
    model.say(ask("Revenue 2024?"), edit(add("Revenue")), reply("Done."))

    engine.conversations.ask(cid, "Dashboard please", user_context=ANA)

    [only] = the_dashboard(engine, cid).tiles
    assert only.plan.verified.id == "revenue" and only.plan.verified.params == {"year": 2024}
    assert only.plan.verified_datasource_id == "ds1" and only.plan.decomposition is None


def test_add_tile_refuses_a_plan_the_validator_rejects(engine, model, runs):
    runs.verdict = lambda call: rejected()
    cid = started(engine).id
    model.say(ask("Orders?"), edit(add("Orders")), reply("That did not work."))

    turn = engine.conversations.ask(cid, "Dashboard please", user_context=ANA)

    step = turn.steps[1]
    assert step.status == "error" and step.error["code"] == "TOOL_NOT_APPLICABLE"
    assert "Column 'orders.id' does not exist." in step.error["message"] and "Nothing was saved" in step.error["message"]
    assert drafts(engine) == [], "the draft this call made is gone with it"
    assert engine.conversations.get(cid, user_context=ANA).dashboard_id is None


def test_a_rejected_chart_saves_none_of_the_edits_with_it(engine, model, runs):
    seed(engine, tiles=[tile("t1")])
    runs.verdict = lambda call: rejected()
    cid = started(engine, dashboard_id="d1").id
    model.say(ask("Orders?"), edit({"kind": "rename", "title": "Renamed"}, add("Orders")), reply("No."))

    turn = engine.conversations.ask(cid, "Add it", user_context=ANA)

    assert turn.steps[1].status == "error"
    board = engine.dashboards.get("d1", user_context=ANA)
    assert (board.title, [t.id for t in board.tiles]) == ("Dashboard d1", ["t1"])


@pytest.mark.parametrize("ref, wording", [
    ({"turn": 4, "step": 1}, "There is no turn 4"),
    ({"turn": 1, "step": 3}, "Turn 1 has 1 answer"),
    ({"turn": 1, "step": 2}, "Turn 1 has 1 answer"),
])
def test_add_tile_needs_an_ask_step_of_this_conversation(engine, model, runs, ref, wording):
    cid = started(engine).id
    model.say(ask("Orders?"), edit({"kind": "add_tile", "title": "X", "from_step": ref}), reply("No."))

    turn = engine.conversations.ask(cid, "Dashboard please", user_context=ANA)

    assert turn.steps[1].error["code"] == "TOOL_NOT_APPLICABLE" and wording in turn.steps[1].error["message"]
    assert runs.checks == []


def test_add_tile_refuses_a_step_that_did_not_answer(engine, model, runs):
    runs.respond = lambda question: QueryResult(status="error", errors=[{
        "node": "executor", "error_code": "EXECUTION_ERROR", "message": "It failed.", "severity": "ERROR"}])
    cid = started(engine).id
    model.say(ask("Orders?"), edit(add("Orders")), reply("No."))

    turn = engine.conversations.ask(cid, "Dashboard please", user_context=ANA)

    assert turn.steps[1].error["code"] == "TOOL_NOT_APPLICABLE" and "did not answer" in turn.steps[1].error["message"]


def test_tools_take_plans_only_from_stored_steps_never_from_model_text():
    assert set(AddTile.model_fields) == {"kind", "title", "from_step"}
    assert set(StepRef.model_fields) == {"turn", "step"}
    schema = json.dumps(DashboardEditArgs.model_json_schema())
    assert not any(word in schema for word in ('"plan"', '"plans"', '"decomposition"', '"sql"'))
    with pytest.raises(ValueError):  # a plan smuggled into the edit is refused, not ignored
        DashboardEditArgs.model_validate({"edits": [{**add("X"), "plan": plan_json()}]})


def test_a_stored_dashboard_step_holds_no_rows_and_the_model_sees_none(engine, model, runs):
    cid = started(engine).id
    model.say(ask("Orders?"), edit(add("Orders")), reply("Done."))

    turn = engine.conversations.ask(cid, "Dashboard please", user_context=ANA)

    [_, step] = turn.steps
    assert "rows" not in json.dumps(step.model_dump(mode="json"))
    assert step_view(step)["output"] == {"kind": "json", "title": "New dashboard", "refs": step.output.refs}
    assert "Paris" not in model.prompts[-1], "no row reached the model"


# ---------------------------------------------------------------- no model call, parity, the facade

def test_dashboard_tools_make_no_model_call(engine, server, runs):
    cid = started(engine).id
    first = tool(engine, cid, "dashboard_edit", {"edits": [{"kind": "rename", "title": "Sales"}]})
    second = tool(engine, cid, "dashboard_pin", {})

    assert first.steps[0].status == "ok" and second.steps[0].status == "error", "an empty draft cannot be pinned"
    assert server.calls == [] and runs.asks == []


def test_every_manual_edit_is_available_through_dashboard_edit():
    from nl2sql import DashboardEdit

    members = get_args(get_args(DashboardEdit)[0])  # the union behind the Annotated discriminator
    assert {m.model_fields["kind"].default for m in members} == set(EDIT_KINDS)
    one_of_each = {
        "rename": RenameDashboard(title="T"), "rename_tile": RenameTile(tile_id="t1", title="T"),
        "set_tile_chart": SetTileChart(tile_id="t1", chart="bar"), "remove_tile": RemoveTile(tile_id="t1"),
        "arrange": Arrange(layouts=[TileArrangement(tile_id="t1", x=0, y=0, w=2, h=2)]),
        "set_filters": SetFilters(filters=[]), "set_schedule": SetSchedule(every_minutes=60),
        "move": MoveDashboard(workspace_id="w"), "add_tile": AddTile(title="T", from_step=StepRef(turn=1, step=1)),
        "replace_plan": ReplacePlan(tile_id="t1", from_step=StepRef(turn=1, step=1))}
    assert set(one_of_each) == {m.model_fields["kind"].default for m in members}, "a new edit needs a case here"
    for kind, edit_ in one_of_each.items():
        parsed = DashboardEditArgs.model_validate({"edits": [edit_.model_dump(mode="json")]})
        assert parsed.edits == [edit_], kind

def test_chat_and_facade_edits_give_the_same_dashboard(engine):
    ws = engine.workspaces.create("Sales", user_context=ANA)
    flt = DashboardFilter(id="f1", label="Country", column="ds1.public.customers.country", kind="text", op="in")
    edits = [RenameDashboard(title="Revenue"), RenameTile(tile_id="t1", title="Orders"),
             SetTileChart(tile_id="t1", chart="table"), SetFilters(filters=[flt]), SetSchedule(every_minutes=60),
             MoveDashboard(workspace_id=ws.id), RemoveTile(tile_id="t2")]
    for did in ("by_hand", "by_chat"):
        seed(engine, did)
    by_hand = engine.dashboards.edit("by_hand", edits, user_context=ANA)
    cid = started(engine, dashboard_id="by_chat").id

    tool(engine, cid, "dashboard_edit", {"edits": [e.model_dump(mode="json") for e in edits]})

    by_chat = engine.dashboards.get("by_chat", user_context=ANA)
    def comparable(d: Dashboard) -> Dict[str, Any]:
        return d.model_dump(mode="json", exclude={"id", "title", "created_at", "updated_at", "schedule"}) \
            | {"title": d.title, "every": d.schedule.every_minutes, "identity": d.schedule.identity.user_id}
    assert comparable(by_chat) == comparable(by_hand)
    assert by_chat.title == "Revenue" and [t.id for t in by_chat.tiles] == ["t1"] and by_chat.workspace_id == ws.id


def test_changing_data_is_not_a_manual_facade_method(engine, runs):
    seed(engine)
    named = [n for n, _ in inspect.getmembers(DashboardsAPI, inspect.isfunction) if not n.startswith("_")]
    for name in named:
        parameters = set(inspect.signature(getattr(DashboardsAPI, name)).parameters)
        if name != "edit":
            assert not parameters & {"conversation", "question", "plan", "plans", "decomposition", "from_step", "sql"}, name
    # Without a conversation to read a stored step from, the edits that change data are refused.
    for edit_ in (AddTile(title="X", from_step=StepRef(turn=1, step=1)),):
        with pytest.raises(ValueError, match="only in a conversation"):
            engine.dashboards.edit("d1", [edit_], user_context=ANA)
    assert runs.checks == []


def test_edit_reads_steps_only_from_the_callers_own_conversation(engine, runs):
    seed(engine)
    cid = started(engine, BOB).id
    theirs = engine.conversations.get(cid, user_context=BOB)

    with pytest.raises(ValueError, match="not the caller's"):
        engine.dashboards.edit("d1", [AddTile(title="X", from_step=StepRef(turn=1, step=1))], user_context=ANA,
                               conversation=theirs)


# ---------------------------------------------------------------- the fix flow

def test_replace_plan_fixes_a_needs_fixing_chart(engine, model, runs):
    seed(engine)
    put_stored(engine, TileRun(tile_id="t1", at=NOW, status="needs_fixing", error={
        "code": "TILE_NEEDS_FIXING", "message": "Column 'orders.id' does not exist."}))
    put_stored(engine, TileRun(tile_id="t2", at=NOW, status="ok", trace_id="old"))
    runs.respond = lambda question: answer(table="orders_v2")
    cid = started(engine, dashboard_id="d1").id
    model.say(ask("What is t1, with the new table?"),
              {"call": {"name": "dashboard_edit", "args": {"edits": [
                  {"kind": "replace_plan", "tile_id": "t1", "from_step": {"turn": 1, "step": 1}}]}}},
              reply("Fixed Chart t1."))

    engine.conversations.ask(cid, "Fix the broken chart", user_context=ANA)

    # The model was told which chart is broken and why, before it asked.
    shown = json.loads(_block(model.prompts[0]))["dashboard"]
    assert [(t["id"], t["status"]) for t in shown["tiles"]] == [("t1", "needs_fixing"), ("t2", "ok")]
    assert shown["tiles"][0]["error"] == "Column 'orders.id' does not exist."
    board = engine.dashboards.get("d1", user_context=ANA)
    fixed, untouched = board.tiles
    assert fixed.plan.plans == {"sq1": plan_json("orders_v2")} and fixed.question == "What is t1, with the new table?"
    assert (fixed.title, fixed.layout.x) == ("Chart t1", 0) and untouched == tile("t2", x=2)
    assert list(stored(engine)) == ["t2"], "the broken chart's stored result is discarded, the other's is kept"
    [check] = runs.checks
    assert check["execute"] is False and check["question"] == "What is t1, with the new table?"


def test_replace_plan_refuses_a_plan_the_validator_rejects_and_keeps_the_old_one(engine, model, runs):
    seed(engine)
    runs.verdict = lambda call: rejected("Table 'orders_v2' does not exist.")
    runs.respond = lambda question: answer(table="orders_v2")
    cid = started(engine, dashboard_id="d1").id
    model.say(ask("Q?"), edit({"kind": "replace_plan", "tile_id": "t1", "from_step": {"turn": 1, "step": 1}}),
              reply("No."))

    turn = engine.conversations.ask(cid, "Fix it", user_context=ANA)

    assert turn.steps[1].status == "error" and "orders_v2" in turn.steps[1].error["message"]
    assert engine.dashboards.get("d1", user_context=ANA).tiles[0] == tile("t1")


# ---------------------------------------------------------------- the draft, pin, ownership

def test_dashboard_edit_creates_a_draft_on_first_use(engine, model, runs):
    seen: List[Any] = []
    engine.events.subscribe(seen.append, names=["dashboard.changed"])
    cid = started(engine).id
    assert drafts(engine) == []

    turn = tool(engine, cid, "dashboard_edit", {"title": "Sales", "edits": [
        {"kind": "rename", "title": "Sales overview"}]})

    [info] = drafts(engine)
    board = the_dashboard(engine, cid)
    assert board.id == info.id and board.title == "Sales overview" and board.status == "draft" and board.tiles == []
    assert board.conversation_id == cid and turn.steps[0].output.refs == {"dashboard_id": board.id}
    assert [w.name for w in engine.workspaces.list(user_context=ANA)] == ["General"]
    assert board.workspace_id == engine.workspaces.list(user_context=ANA)[0].id
    assert [e.data["change"] for e in seen] == [["created"], ["rename"]]

    tool(engine, cid, "dashboard_edit", {"edits": [{"kind": "rename", "title": "Again"}]})
    assert [d.id for d in drafts(engine)] == [board.id], "the second call edits the same draft"


def test_a_new_draft_takes_the_given_title_and_workspace(engine):
    ws = engine.workspaces.create("Ops", user_context=ANA)
    cid = started(engine).id

    tool(engine, cid, "dashboard_edit", {"title": "Ops board", "workspace_id": ws.id, "edits": [
        {"kind": "rename_tile", "tile_id": "x", "title": "y"}]})
    assert drafts(engine) == [], "an edit that fails leaves no draft behind"

    tool(engine, cid, "dashboard_edit", {"title": "Ops board", "workspace_id": ws.id, "edits": [
        {"kind": "set_schedule", "every_minutes": None}]})
    board = the_dashboard(engine, cid)
    assert (board.title, board.workspace_id) == ("Ops board", ws.id)


def test_create_draft_is_an_empty_private_draft(engine):
    board = engine.dashboards.create_draft(user_context=ANA, conversation_id="c1")

    assert (board.status, board.tiles, board.conversation_id, board.title) == ("draft", [], "c1", "New dashboard")
    assert (board.tenant_id, board.user_id) == ("acme", "ana")
    assert engine.dashboards.list(user_context=BOB, status="draft") == []
    with pytest.raises(TypeError):
        engine.dashboards.create_draft(user_context=None)
    with pytest.raises(ValueError, match="title"):
        engine.dashboards.create_draft(title="  ", user_context=ANA)


def test_pin_tool_saves_the_whole_dashboard_only_when_called(engine, model, runs):
    cid = started(engine).id
    model.say(ask("Orders?"), ask("Customers?"), edit(add("Orders", 1, 1), add("Customers", 1, 2)),
              reply("Here is a draft. Say the word and I will save it."))
    engine.conversations.ask(cid, "Build a dashboard", user_context=ANA)
    board = the_dashboard(engine, cid)
    assert board.status == "draft" and engine.dashboards.list(user_context=ANA) == [], "nothing is saved yet"

    model.say(pin(title="Orders and customers"), reply("Saved."))
    turn = engine.conversations.ask(cid, "Yes, save it", user_context=ANA)

    saved = engine.dashboards.get(board.id, user_context=ANA)
    assert (saved.status, saved.title, [t.id for t in saved.tiles]) == ("saved", "Orders and customers", ["t1", "t2"])
    [info] = engine.dashboards.list(user_context=ANA)
    assert info.id == board.id and info.tiles == 2
    assert turn.steps[0].output.refs == {"dashboard_id": board.id}


def test_pin_needs_a_dashboard_with_a_chart(engine):
    cid = started(engine).id
    nothing = tool(engine, cid, "dashboard_pin", {})
    assert nothing.steps[0].error["code"] == "TOOL_NOT_APPLICABLE" and "no dashboard" in nothing.steps[0].error["message"]

    tool(engine, cid, "dashboard_edit", {"edits": [{"kind": "rename", "title": "Empty"}]})
    empty = tool(engine, cid, "dashboard_pin", {})
    assert empty.steps[0].error["code"] == "TOOL_NOT_APPLICABLE" and "empty draft" in empty.steps[0].error["message"]


def test_conversation_started_on_a_saved_dashboard_edits_it(engine):
    seed(engine)
    cid = started(engine, dashboard_id="d1").id

    turn = tool(engine, cid, "dashboard_edit", {"edits": [{"kind": "rename", "title": "Edited in chat"}]})

    assert turn.steps[0].status == "ok" and turn.steps[0].output.refs == {"dashboard_id": "d1"}
    assert engine.dashboards.get("d1", user_context=ANA).title == "Edited in chat"
    assert drafts(engine) == [] and engine.conversations.get(cid, user_context=ANA).dashboard_id == "d1"


def test_dashboard_tool_on_another_users_dashboard_is_not_found(engine):
    seed(engine, "b1", user=BOB)
    cid = started(engine).id

    edited = tool(engine, cid, "dashboard_edit", {"dashboard_id": "b1", "edits": [{"kind": "rename", "title": "Mine"}]})
    pinned = tool(engine, cid, "dashboard_pin", {"dashboard_id": "b1"})

    for step in (edited.steps[0], pinned.steps[0]):
        assert step.status == "error" and step.error["code"] == "TOOL_NOT_APPLICABLE"
        assert "No dashboard 'b1' for this caller" in step.error["message"]
    assert engine.dashboards.get("b1", user_context=BOB).title == "Dashboard b1"
    assert drafts(engine) == [] and engine.conversations.get(cid, user_context=ANA).dashboard_id is None


# ---------------------------------------------------------------- what the model is told

def test_the_open_dashboard_is_in_the_conversation_block(engine, model, runs):
    seed(engine)
    put_stored(engine, TileRun(tile_id="t1", at=NOW, status="ok", trace_id="old"))
    cid = started(engine, dashboard_id="d1").id
    model.say(reply("Hi."))

    engine.conversations.ask(cid, "What is on my dashboard?", user_context=ANA)

    shown = json.loads(_block(model.prompts[0]))["dashboard"]
    assert (shown["id"], shown["title"], shown["status"]) == ("d1", "Dashboard d1", "saved")
    assert [(t["id"], t["title"], t["question"], t["chart"], t["status"]) for t in shown["tiles"]] == [
        ("t1", "Chart t1", "What is t1?", "auto", "ok"), ("t2", "Chart t2", "What is t2?", "auto", None)]
    assert "plan" not in json.dumps(shown) and "Paris" not in json.dumps(shown)


def test_a_conversation_without_a_dashboard_shows_none(engine, model):
    cid = started(engine).id
    model.say(reply("Hi."))

    engine.conversations.ask(cid, "Hello", user_context=ANA)

    assert "dashboard" not in json.loads(_block(model.prompts[0]))


def test_the_draft_the_model_just_made_is_in_its_next_step(engine, model, runs):
    cid = started(engine).id
    model.say(ask("Orders?"), edit(add("Orders")), reply("Done."))

    engine.conversations.ask(cid, "Dashboard please", user_context=ANA)

    assert "dashboard" not in json.loads(_block(model.prompts[0]))
    after = json.loads(_block(model.prompts[2]))["dashboard"]
    assert after["status"] == "draft" and [t["title"] for t in after["tiles"]] == ["Orders"]


def test_a_deleted_dashboard_leaves_the_block_without_one(engine, model):
    seed(engine)
    cid = started(engine, dashboard_id="d1").id
    engine.dashboards.delete("d1", user_context=ANA)
    model.say(reply("Hi."))

    engine.conversations.ask(cid, "Hello", user_context=ANA)

    assert "dashboard" not in json.loads(_block(model.prompts[0]))
