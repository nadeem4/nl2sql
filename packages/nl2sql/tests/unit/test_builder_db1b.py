"""The guided dashboard builder, engine side.

A real engine on a small SQLite database (indexed with a fake embedder), so the glossary, the column roles, the
curated queries and the caller's scope are the real ones. ``run_query`` is replaced by canned results, as in the
starter's tests, so a test can say what was asked; the one model call the builder makes goes to a fake server.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding
from test_answers_db1a import answered, converse, keep_trace, save
from test_dashboards_d3 import Runs, failed, good
from test_review_queue import ADMIN, ANALYST, ROLES

from nl2sql import NL2SQL, CancellationToken, Embeddings, UserContext
from nl2sql.api.query_api import QueryResult
from nl2sql.common.cancellation import RunCancelled
from nl2sql.common.errors import AnswerNotFound, InvalidArgument, NoTiles
from nl2sql.dashboards.builder import TIME_RANGES, BuildReport, BuilderOptions, TileProposal
from nl2sql.dashboards.models import (
    EDIT_KINDS, AddSavedTile, AddVerifiedTile,
)
from nl2sql.llm.override import LLMOverride
from nl2sql.llm.request_key import MissingProviderKey
from nl2sql.testing.config import make_config
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

CURATED = {"datasources": {"shop": {"verified_queries": [
    {"id": "order_count", "description": "How many orders there are.", "questions": ["How many orders are there?"],
     "sql": "SELECT COUNT(*) AS n FROM orders"},
    {"id": "pay_total", "description": "What the company pays.", "questions": ["What is the total pay?"],
     "sql": "SELECT SUM(amount) AS pay FROM salaries"},
    {"id": "orders_since", "description": "Orders since a day.", "questions": ["How many orders since {day}?"],
     "params": {"day": {"type": "date"}}, "sql": "SELECT COUNT(*) AS n FROM orders WHERE placed >= :day"},
    {"id": "orders_last_year", "description": "Orders since a day, default last year.",
     "questions": ["How many orders since {day}?"], "params": {"day": {"type": "date", "default": "2024-01-01"}},
     "sql": "SELECT COUNT(*) AS n FROM orders WHERE placed >= :day"}]}}}
GLOSSARY = {"version": 1, "datasources": {"shop": {
    "columns": {"orders.total": {"role": "money"}, "orders.status": {"role": "enum"}, "orders.placed": {"role": "date"}},
    "terms": [{"term": "Revenue", "columns": ["orders.total"]}, {"term": "Pay", "columns": ["salaries.amount"]},
              {"term": "Loyalty", "definition": "A word with no column."}]}}}
STATEMENTS = (
    "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)",
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL, status TEXT, placed DATE)",
    "CREATE TABLE salaries (id INTEGER PRIMARY KEY, amount REAL)",
)
FAKE_KEY = "sk-" + "fake-builder"  # built at runtime: no key-shaped literal in the source


def _project(folder, server, *, key: bool = True, llm: Any = None, **dashboards):
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(folder / "shop.db") as connection:
        for statement in STATEMENTS:
            connection.execute(statement)
    connection.close()
    (folder / "glossary.yaml").write_text(yaml.safe_dump(GLOSSARY, sort_keys=False), encoding="utf-8")
    if llm is None:
        llm = {"provider": "openai", "model": "gpt-builder-test", "base_url": server.base_url}
        if key:
            llm["api_key"] = FAKE_KEY
    return make_config(
        folder, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
        policies={"roles": ROLES}, curated=CURATED, knowledge=[{"type": "yaml", "path": "glossary.yaml"}], llm=llm,
        answers={"max_per_user": 5}, **({"dashboards": dashboards} if dashboards else {}))


def _engine(config, registry=None) -> NL2SQL:
    return NL2SQL(config.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8),
                                                                       dimension=8)}), registry=registry)


def _replay_engine(folder, server):
    """The keyless hosted demo's shape: the ``replay`` provider (no key), an empty store, no ``llm_override``."""
    from nl2sql.llm.replay import ReplayProvider, ReplayStore
    from nl2sql.registry import Registry

    registry = Registry.default()
    registry.add_llm("replay", ReplayProvider(ReplayStore()), replace=True)
    return _engine(_project(folder, server, llm={"provider": "replay", "model": "recorded"}), registry)


@pytest.fixture
def server():
    fake = FakeLLMServer([Rule("BuilderQuestions", {"questions": [
        "What is the revenue per status?", "How has revenue moved by month?", "Which status is largest?",
        "A fourth question the cap drops?", "A fifth?"]})]).start()
    yield fake
    fake.stop()


@pytest.fixture
def engine(tmp_path, server):
    with _engine(_project(tmp_path, server)) as built:
        assert built.index.build().ok
        yield built


@pytest.fixture
def runs(monkeypatch):
    """Every unexecuted run answers; an executed one (a question put to the model) answers unless it says 'nonsense'."""
    canned = Runs(monkeypatch)

    def respond(call: Dict[str, Any]) -> QueryResult:
        if call["execute"]:
            return failed() if "nonsense" in call["question"] else answered(f"tr-{len(canned.calls)}")
        return failed() if "refused" in call["question"] else good()

    canned.respond = respond
    return canned


def saved_answer(engine, user: UserContext = ANALYST, trace_id: str = "t1"):
    keep_trace(engine, trace_id, user)
    cid = converse(engine, user, trace_id=trace_id, cid=f"c-{trace_id}")
    return save(engine, user, trace_id=trace_id, cid=cid).answer


def propose(engine, user: UserContext = ANALYST, **kwargs) -> List[TileProposal]:
    return engine.dashboards.propose_tiles("shop", user_context=user, **{"sources": ["curated"], **kwargs})


# ---------------------------------------------------------------- the two new edits

def test_the_two_new_edits_are_kinds_of_the_union():
    assert {"add_verified_tile", "add_saved_tile"} <= set(EDIT_KINDS)


def test_add_verified_tile_needs_no_conversation_and_no_model(engine, server, runs):
    draft = engine.dashboards.create_draft(user_context=ANALYST)

    saved = engine.dashboards.edit(draft.id, [AddVerifiedTile(datasource_id="shop", verified_id="order_count",
                                                              title="Orders")], user_context=ANALYST)

    [tile] = saved.tiles
    assert (tile.title, tile.question, tile.plan.verified.id, tile.plan.verified_datasource_id) == (
        "Orders", "How many orders are there?", "order_count", "shop")
    assert server.calls == []


def test_a_verified_tile_with_params_fills_the_question_and_keeps_the_values(engine, runs):
    draft = engine.dashboards.create_draft(user_context=ANALYST)

    saved = engine.dashboards.edit(draft.id, [AddVerifiedTile(
        datasource_id="shop", verified_id="orders_since", title="Recent", params={"day": "2025-01-01"})],
        user_context=ANALYST)

    assert saved.tiles[0].plan.verified.params == {"day": "2025-01-01"}
    assert saved.tiles[0].question == "How many orders since 2025-01-01?"


def test_a_verified_tile_the_caller_may_not_read_is_refused_and_nothing_is_saved(engine):
    draft = engine.dashboards.create_draft(user_context=ANALYST)

    with pytest.raises(InvalidArgument):
        engine.dashboards.edit(draft.id, [AddVerifiedTile(datasource_id="shop", verified_id="pay_total",
                                                          title="Pay")], user_context=ANALYST)

    assert engine.dashboards.get(draft.id, user_context=ANALYST).tiles == []


def _refusal(engine, verified_id: str) -> str:
    draft = engine.dashboards.create_draft(user_context=ANALYST)
    with pytest.raises(InvalidArgument) as refused:
        engine.dashboards.edit(draft.id, [AddVerifiedTile(datasource_id="shop", verified_id=verified_id, title="X")],
                               user_context=ANALYST)
    return str(refused.value)


def test_a_verified_query_outside_the_callers_scope_looks_like_one_that_does_not_exist(engine, runs):
    hidden, missing = _refusal(engine, "pay_total"), _refusal(engine, "no_such")

    assert hidden == missing.replace("no_such", "pay_total")
    assert "salaries" not in hidden and "not allowed" not in hidden and "policy" not in hidden
    assert runs.calls == [], "nothing was run to find out"


def test_chat_cannot_probe_for_a_hidden_verified_query_either(engine, runs):
    cid = engine.conversations.start(user_context=ANALYST, datasource_id="shop").id

    def refusal(verified_id: str) -> str:
        turn = engine.conversations.tool(cid, "dashboard_edit", {"edits": [{
            "kind": "add_verified_tile", "datasource_id": "shop", "verified_id": verified_id, "title": "X"}]},
            user_context=ANALYST)
        assert turn.steps[0].error["code"] == "TOOL_NOT_APPLICABLE"
        return turn.steps[0].error["message"]

    assert refusal("pay_total").replace("pay_total", "no_such") == refusal("no_such")
    assert "salaries" not in refusal("pay_total")


def test_an_unknown_verified_query_is_a_refused_edit(engine):
    draft = engine.dashboards.create_draft(user_context=ANALYST)
    with pytest.raises(InvalidArgument, match="no_such"):
        engine.dashboards.edit(draft.id, [AddVerifiedTile(datasource_id="shop", verified_id="no_such", title="X")],
                               user_context=ANALYST)


def test_add_saved_tile_copies_the_plan_and_is_checked_as_the_caller(engine, runs):
    answer = saved_answer(engine)
    draft = engine.dashboards.create_draft(user_context=ANALYST)

    saved = engine.dashboards.edit(draft.id, [AddSavedTile(saved_id=answer.id, title="Mine")], user_context=ANALYST)

    [tile] = saved.tiles
    assert tile.plan == answer.plan and tile.question == answer.question and tile.chart == answer.chart
    assert [c for c in runs.calls if not c["execute"]], "the plan was checked before it was saved"


def test_another_users_saved_answer_is_not_found_even_for_an_admin(engine):
    answer = saved_answer(engine, ANALYST)
    draft = engine.dashboards.create_draft(user_context=ADMIN)
    with pytest.raises(AnswerNotFound):
        engine.dashboards.edit(draft.id, [AddSavedTile(saved_id=answer.id, title="Not mine")], user_context=ADMIN)


def test_chat_can_use_the_new_edits(engine):
    from nl2sql.chat_tools.dashboard_edit import DashboardEditArgs

    args = DashboardEditArgs.model_validate({"edits": [
        {"kind": "add_verified_tile", "datasource_id": "shop", "verified_id": "order_count", "title": "Orders"},
        {"kind": "add_saved_tile", "saved_id": "abc", "title": "Mine"}]})
    assert [e.kind for e in args.edits] == ["add_verified_tile", "add_saved_tile"]


# ---------------------------------------------------------------- options

def test_options_list_only_what_the_caller_reaches(engine):
    saved_answer(engine, ANALYST)

    options = engine.dashboards.builder_options("shop", user_context=ANALYST)

    assert isinstance(options, BuilderOptions)
    assert [(c.id, c.question) for c in options.curated] == [
        ("order_count", "How many orders are there?"), ("orders_last_year", "How many orders since 2024-01-01?")]
    assert [m.term for m in options.metrics] == ["Revenue"]  # Pay reads salaries; Loyalty names no column
    assert options.metrics[0].columns == ["shop.main.orders.total"]
    assert [(d.label, d.column) for d in options.dimensions] == [("Status", "shop.main.orders.status")]
    assert [(t.label, t.column) for t in options.time_columns] == [("Placed", "shop.main.orders.placed")]
    assert [r.id for r in options.ranges] == [r.id for r in TIME_RANGES] and len(options.ranges) == 5
    assert [s.question for s in options.saved] == ["How many orders are there per customer?"]


def test_an_admin_sees_the_pay_query_and_the_pay_term(engine):
    options = engine.dashboards.builder_options("shop", user_context=ADMIN)
    assert "pay_total" in [c.id for c in options.curated]
    assert {m.term for m in options.metrics} == {"Revenue", "Pay"}


def test_saved_answers_are_the_callers_own(engine):
    saved_answer(engine, ANALYST)
    assert len(engine.dashboards.builder_options("shop", user_context=ANALYST).saved) == 1
    assert engine.dashboards.builder_options("shop", user_context=ADMIN).saved == []


def test_a_datasource_that_is_not_indexed_says_so(tmp_path, server):
    from nl2sql.common.errors import NotIndexed

    with _engine(_project(tmp_path, server)) as fresh:
        with pytest.raises(NotIndexed):
            fresh.dashboards.builder_options("shop", user_context=ANALYST)


def test_curated_list_is_scoped_to_the_caller(engine):
    mine = engine.curated.list(user_context=ANALYST)
    assert {q.id for q in mine} == {"order_count", "orders_since", "orders_last_year"}
    assert {q.id for q in engine.curated.list(user_context=ADMIN)} >= {"pay_total"}
    since = next(q for q in mine if q.id == "orders_since")
    assert since.required_params == ["day"] and since.datasource_id == "shop"
    assert engine.curated.list(user_context=ANALYST, datasource_id="elsewhere") == []


# ---------------------------------------------------------------- propose

def test_curated_and_saved_proposals_call_no_model(engine, server):
    answer = saved_answer(engine)

    proposals = propose(engine, sources=["curated", "saved"], curated_ids=["order_count"], saved_ids=[answer.id])

    assert [(p.source, p.ref, p.question) for p in proposals] == [
        ("curated", "order_count", "How many orders are there?"), ("saved", answer.id, answer.question)]
    assert all(p.ticked and p.chart == "auto" for p in proposals)
    assert server.calls == []


def test_a_curated_query_with_a_required_param_or_out_of_scope_is_not_proposed(engine):
    assert propose(engine, curated_ids=["orders_since", "pay_total", "order_count"])[0].ref == "order_count"
    assert [p.ref for p in propose(engine, curated_ids=["orders_since", "pay_total"])] == []


def test_asked_makes_one_model_call_for_at_most_three_questions(engine, server):
    proposals = propose(engine, sources=["asked"], metrics=["Revenue"], dimensions=["shop.main.orders.status"],
                        time_range="last_30_days", text="focus on the biggest")

    assert [p.source for p in proposals] == ["asked"] * 3 and all(p.ref is None for p in proposals)
    assert proposals[0].question == "What is the revenue per status?"
    assert len(server.calls) == 1
    prompt = str(server.calls[0]["body"])
    for word in ("Revenue", "Status", "last 30 days", "focus on the biggest"):
        assert word in prompt


def test_the_tile_cap_trims_the_proposal_and_the_question_count(tmp_path, server):
    with _engine(_project(tmp_path, server, max_tiles=2)) as capped:
        assert capped.index.build().ok
        proposals = capped.dashboards.propose_tiles(
            "shop", user_context=ANALYST, sources=["curated", "asked"], curated_ids=["order_count"],
            metrics=["Revenue"])
        assert [p.source for p in proposals] == ["curated", "asked"]


def test_asked_with_nothing_to_go_on_makes_no_model_call(engine, server):
    assert propose(engine, sources=["asked"]) == []
    assert propose(engine, sources=["asked"], time_range="last_7_days", text="   ") == []
    assert server.calls == []


def test_asked_without_a_key_is_the_missing_key_error_and_the_rest_still_works(tmp_path, server):
    with _engine(_project(tmp_path, server, key=False)) as keyless:
        assert keyless.index.build().ok
        assert keyless.dashboards.propose_tiles("shop", user_context=ANALYST, sources=["curated"],
                                               curated_ids=["order_count"])
        with pytest.raises(MissingProviderKey):
            keyless.dashboards.propose_tiles("shop", user_context=ANALYST, sources=["asked"], metrics=["Revenue"])
        assert server.calls == []


def test_asked_on_the_replay_provider_is_the_missing_key_error_too(tmp_path, server):
    """the keyless hosted demo has a provider that needs no key, but still no model for new questions."""
    with _replay_engine(tmp_path, server) as demo:
        assert demo.index.build().ok
        with pytest.raises(MissingProviderKey):
            demo.dashboards.propose_tiles("shop", user_context=ANALYST, sources=["asked"], metrics=["Revenue"])
        tiles = tiles_of(("asked", None, "What is the revenue per status?"))
        with pytest.raises(MissingProviderKey):
            demo.dashboards.build("shop", tiles, user_context=ANALYST)
        assert demo.dashboards.list(user_context=ANALYST, status="draft") == []


def test_curated_and_saved_build_with_the_real_run_query_and_no_model_call(tmp_path, server):
    """No stub: the access check runs the real graph on a replay engine whose store is empty (any model call misses)."""
    with _replay_engine(tmp_path, server) as demo:
        assert demo.index.build().ok
        answer = saved_answer(demo)
        tiles = [TileProposal(source="curated", ref="order_count", question="How many orders are there?"),
                 TileProposal(source="saved", ref=answer.id, question=answer.question)]

        report = demo.dashboards.build("shop", tiles, user_context=ANALYST)

        assert len(report.built) == 2 and report.skipped == []
        assert server.calls == []


def test_a_callers_own_key_makes_asked_work(tmp_path, server, monkeypatch):
    """The request's client is built for the provider's own endpoint, so the model call itself is replaced here."""
    from langchain_core.runnables import RunnableLambda

    from nl2sql.dashboards.builder import BuilderQuestions

    monkeypatch.setattr("nl2sql.llm.wires.structured", lambda llm, schema: RunnableLambda(
        lambda _: BuilderQuestions(questions=["One?", "Two?"])))
    with _engine(_project(tmp_path, server, key=False)) as keyless:
        assert keyless.index.build().ok
        override = LLMOverride(keys={"openai": FAKE_KEY})
        proposals = keyless.dashboards.propose_tiles("shop", user_context=ANALYST, sources=["asked"],
                                                     metrics=["Revenue"], llm_override=override)
        assert [p.question for p in proposals] == ["One?", "Two?"]


def test_a_metric_the_caller_cannot_read_is_not_sent_to_the_model(engine, server):
    propose(engine, sources=["asked"], metrics=["Pay", "Revenue"])
    assert "Pay" not in str(server.calls[0]["body"])


def test_propose_runs_nothing(engine, runs):
    propose(engine, curated_ids=["order_count"])
    assert runs.calls == []


# ---------------------------------------------------------------- build

def tiles_of(*pairs) -> List[TileProposal]:
    return [TileProposal(source=s, ref=r, question=q) for s, r, q in pairs]


def test_build_mixes_the_three_sources_into_an_ordinary_saved_dashboard(engine, runs, server):
    answer = saved_answer(engine)
    tiles = tiles_of(("curated", "order_count", "How many orders are there?"), ("saved", answer.id, answer.question),
                     ("asked", None, "What is the revenue per status?"))

    report = engine.dashboards.build("shop", tiles, user_context=ANALYST, title="Shop")

    assert isinstance(report, BuildReport) and report.skipped == []
    d = report.dashboard
    assert (d.title, d.status, d.user_id, len(d.tiles), report.built) == (
        "Shop", "saved", "bob", 3, [t.id for t in d.tiles])
    assert [t.plan.verified is not None for t in d.tiles] == [True, False, False]
    assert d.tiles[1].plan == answer.plan
    assert engine.dashboards.get(d.id, user_context=ANALYST).status == "saved"
    assert server.calls == [], "the model is only reached through the pipeline, which the canned run replaces"


def test_build_refuses_more_than_three_asked_tiles_before_any_model_call(engine, runs, server):
    tiles = tiles_of(*[("asked", None, f"Question {i}?") for i in range(4)])

    with pytest.raises(InvalidArgument, match="At most 3 new questions"):
        engine.dashboards.build("shop", tiles, user_context=ANALYST)

    assert server.calls == []


def test_the_asked_tiles_share_one_builder_conversation_which_is_removed(engine, runs):
    before = len(engine.conversations.list(user_context=ANALYST))
    seen = []
    original = runs.respond

    def respond(call):
        seen.append(call["question"])
        return original(call)

    runs.respond = respond

    report = engine.dashboards.build("shop", tiles_of(("asked", None, "Q one?"), ("asked", None, "Q two?")),
                                     user_context=ANALYST)

    assert len(report.built) == 2 and list(dict.fromkeys(seen)) == ["Q one?", "Q two?"]
    assert len(engine.conversations.list(user_context=ANALYST)) == before


def test_an_unanswerable_question_is_skipped_with_a_code_and_the_rest_is_kept(engine, runs):
    tiles = tiles_of(("asked", None, "Something nonsense?"), ("curated", "order_count", "How many orders are there?"),
                     ("asked", None, "A refused plan?"))

    report = engine.dashboards.build("shop", tiles, user_context=ANALYST)

    assert len(report.built) == 1
    assert [s.question for s in report.skipped] == ["Something nonsense?", "A refused plan?"]
    assert all(s.code and s.message for s in report.skipped)
    assert [s.code for s in report.skipped] == ["EXECUTION_ERROR", "EXECUTION_ERROR"]  # the run's own code


def test_a_skipped_tile_carries_the_reason_code_of_the_refusal(engine, runs):
    def respond(call):
        if call["execute"]:
            return answered("tr-x")
        if call["question"] == "a":
            return QueryResult(status="error", trace_id="chk", errors=[{
                "node": "executor", "error_code": "REPLAY_MISS", "message": "No.", "severity": "ERROR"}])
        if call["question"] == "b":
            return QueryResult(status="error", trace_id="chk", errors=[{
                "node": "logical_validator", "error_code": "VALIDATION_FAILED", "message": "Bad.", "severity": "ERROR"}])
        return good()

    runs.respond = respond
    tiles = tiles_of(("curated", "order_count", "a"), ("curated", "orders_last_year", "b"),
                     ("curated", "order_count", "c"))

    report = engine.dashboards.build("shop", tiles, user_context=ANALYST)

    assert [(s.question, s.code) for s in report.skipped] == [("a", "REPLAY_MISS"), ("b", "TILE_NEEDS_FIXING")]


def test_build_reports_each_tile_as_it_is_added(engine, runs):
    seen = []
    tiles = tiles_of(("curated", "order_count", "How many orders are there?"), ("asked", None, "nonsense?"))

    engine.dashboards.build("shop", tiles, user_context=ANALYST, on_tile=seen.append)

    assert [(p.done, p.total, p.status) for p in seen] == [(1, 2, "ok"), (2, 2, "skipped")]


def test_nothing_built_is_no_tiles_and_nothing_is_left_behind(engine, runs):
    with pytest.raises(NoTiles):
        engine.dashboards.build("shop", tiles_of(("asked", None, "nonsense?")), user_context=ANALYST)

    assert engine.dashboards.list(user_context=ANALYST, status="draft") == []
    assert engine.dashboards.list(user_context=ANALYST) == []
    assert engine.workspaces.list(user_context=ANALYST) == []


def test_a_cancel_saves_nothing(engine, runs):
    token = CancellationToken()
    tiles = tiles_of(("curated", "order_count", "How many orders are there?"), ("asked", None, "Second?"))

    def stop(progress):
        token.cancel()

    with pytest.raises(RunCancelled):
        engine.dashboards.build("shop", tiles, user_context=ANALYST, on_tile=stop, cancellation_token=token)

    assert engine.dashboards.list(user_context=ANALYST, status="draft") == []
    assert engine.dashboards.list(user_context=ANALYST) == []
    assert engine.workspaces.list(user_context=ANALYST) == []


def test_the_cancel_token_reaches_the_question(engine, monkeypatch):
    seen = []

    def ask_only(api, conversation_id, question, **kwargs):
        seen.append(kwargs.get("cancellation_token"))
        raise InvalidArgument("stop here")

    monkeypatch.setattr("nl2sql.conversations.api.Conversations.ask_only", ask_only)
    token = CancellationToken()

    with pytest.raises(NoTiles):
        engine.dashboards.build("shop", tiles_of(("asked", None, "Q?")), user_context=ANALYST,
                                cancellation_token=token)

    assert seen == [token]


def test_importing_the_dashboards_module_does_not_load_the_builder():
    import subprocess
    import sys

    code = "import sys, nl2sql.dashboards.api; sys.exit(1 if 'nl2sql.dashboards.builder' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_build_refuses_more_tiles_than_the_cap_before_anything_runs(tmp_path, server):
    with _engine(_project(tmp_path, server, max_tiles=1)) as capped:
        assert capped.index.build().ok
        two = tiles_of(("curated", "order_count", "a"), ("curated", "orders_last_year", "b"))
        with pytest.raises(InvalidArgument, match="max_tiles"):
            capped.dashboards.build("shop", two, user_context=ANALYST)
        assert capped.dashboards.list(user_context=ANALYST, status="draft") == []


def test_unticked_tiles_are_left_out(engine, runs):
    tiles = [TileProposal(source="curated", ref="order_count", question="a", ticked=False),
             TileProposal(source="curated", ref="orders_last_year", question="b")]
    assert len(engine.dashboards.build("shop", tiles, user_context=ANALYST).built) == 1


def test_a_reworded_title_and_a_chosen_chart_are_kept(engine, runs):
    tiles = [TileProposal(source="curated", ref="order_count", question="How many orders are there?",
                          title="Order count", chart="big_number")]
    [tile] = engine.dashboards.build("shop", tiles, user_context=ANALYST).dashboard.tiles
    assert (tile.title, tile.chart) == ("Order count", "big_number")


def test_a_time_column_adds_the_between_filter(engine, runs):
    report = engine.dashboards.build("shop", tiles_of(("curated", "order_count", "a")), user_context=ANALYST,
                                     time_column="shop.main.orders.placed")
    [f] = report.dashboard.filters
    assert (f.column, f.op, f.kind) == ("shop.main.orders.placed", "between", "temporal")
    with pytest.raises(InvalidArgument):
        engine.dashboards.build("shop", tiles_of(("curated", "order_count", "a")), user_context=ANALYST,
                                time_column="shop.main.salaries.amount")


def test_asked_tiles_without_a_key_fail_before_anything_runs(tmp_path, server):
    with _engine(_project(tmp_path, server, key=False)) as keyless:
        assert keyless.index.build().ok
        with pytest.raises(MissingProviderKey):
            keyless.dashboards.build("shop", tiles_of(("asked", None, "Q?")), user_context=ANALYST)
        assert keyless.dashboards.list(user_context=ANALYST, status="draft") == []


def test_the_caller_is_required(engine):
    for call in (lambda: engine.dashboards.builder_options("shop"),
                 lambda: engine.dashboards.propose_tiles("shop", sources=["curated"]),
                 lambda: engine.dashboards.build("shop", []), lambda: engine.curated.list()):
        with pytest.raises(TypeError):
            call()
