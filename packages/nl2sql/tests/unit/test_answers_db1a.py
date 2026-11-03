"""Saved answers in the engine.

The engine is Q1's: a real engine on a small SQLite database, indexed with a fake embedder, so nothing is
downloaded and no model is called. A run is a trace written to the engine's trace store, and a conversation is
stored by hand with one ``ask`` step, as a chat turn leaves it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
from nl2sql.storage.sqlite import LockHeld
from test_review_queue import ADMIN, ANALYST, _engine, _project

from nl2sql import UserContext
from nl2sql.answers.models import SavedAnswer
from nl2sql.api.query_api import AnswerSource, QueryAPI, QueryResult, SubQueryResult
from nl2sql.common.errors import (
    AnswerLimitReached, AnswerNotFound, ConversationNotFound, NL2SQLError, NotSavable,
)
from nl2sql.conversations.models import Conversation, ToolCall, ToolStep, Turn
from nl2sql.conversations.store import ConversationStore
from nl2sql.dashboards.models import StepRef
from nl2sql.execution.artifacts.results import ResultOwner
from nl2sql.hooks.approval import VerifiedRef
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
QUESTION = "How many orders are there per customer?"
SQL = "SELECT COUNT(*) AS n FROM orders"
LLM = {"provider": "openai", "model": "gpt-fake", "api_key": "sk-fake-answers", "base_url": "http://127.0.0.1:9"}


def plan_json(table: str = "orders", column: str = "id") -> Dict[str, Any]:
    return PlanModel.model_validate({
        "tables": [{"name": table, "alias": "t1"}],
        "select_items": [{"alias": "n", "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                                                 "args": [{"kind": "column", "alias": "t1",
                                                           "column_name": column}]}}]}).model_dump(mode="json")


def answered(trace_id: str = "t1", *, table: str = "orders", column: str = "id", verified: bool = False) -> QueryResult:
    """The stored result of an ``ask`` step (no rows)."""
    decomposition = DecomposerResponse(
        sub_queries=[SubQuery(id="sq1", datasource_id="shop", intent="Count orders")],
        combine_groups=[CombineGroup(group_id="g1", operation="standalone",
                                     inputs=[CombineInput(subquery_id="sq1")])])
    source = AnswerSource(type="verified", id="order_count", datasource_id="shop") if verified else AnswerSource()
    return QueryResult(trace_id=trace_id, status="success", decomposition=decomposition, source=source,
                       sub_queries=[SubQueryResult(id="sq1", datasource_id="shop", status="ok", sql=SQL,
                                                   plan=plan_json(table, column))])


@pytest.fixture
def engine(tmp_path):
    with _engine(_project(tmp_path, llm=LLM, answers={"max_per_user": 3})) as engine:
        assert engine.index.build().ok
        yield engine


def keep_trace(engine, trace_id: str, user: UserContext = ANALYST, *, sql: Any = SQL, status: str = "success",
               source: str = "generated") -> None:
    """A kept trace of the run, as a run with traces on leaves it."""
    statements = sql if isinstance(sql, list) else [sql]
    result = {"trace_id": trace_id, "status": status, "source": {"type": source},
              "sub_queries": [{"id": f"sq{i}", "sql": s, "datasource_id": "shop"} for i, s in enumerate(statements)]}
    engine._runtime.traces.write(trace_id, {
        "trace_format_version": 1, "trace_id": trace_id, "nodes": [], "result": result,
        "request": {"question": QUESTION, "roles": list(user.roles)},
        "owner": ResultOwner.of(user, engine._runtime.tenant_id).model_dump()})


def converse(engine, user: UserContext = ANALYST, *, trace_id: str = "t1", step_status: str = "ok",
             result: QueryResult = None, cid: str = "c1") -> str:
    """A stored conversation of ``user`` whose first turn holds one ``ask`` step."""
    step = ToolStep(call=ToolCall(name="ask", args={"question": QUESTION}), status=step_status, question=QUESTION,
                    trace_id=trace_id, result=result if result is not None else answered(trace_id))
    turn = Turn(index=1, at=NOW, kind="message", text=QUESTION, steps=[step], status="answered")
    ConversationStore(engine._runtime.state).put(Conversation(
        id=cid, tenant_id=user.tenant_id or engine._runtime.tenant_id, user_id=user.user_id, title=QUESTION,
        created_at=NOW, updated_at=NOW, turns=[turn]))
    return cid


def save(engine, user: UserContext = ANALYST, *, trace_id: str = "t1", cid: str = "c1", **kwargs: Any):
    return engine.answers.save(trace_id=trace_id, conversation_id=cid, step=StepRef(step=1), user_context=user,
                               **kwargs)


def ready(engine, trace_id: str = "t1", user: UserContext = ANALYST, **kwargs: Any) -> str:
    keep_trace(engine, trace_id, user)
    return converse(engine, user, trace_id=trace_id, cid=f"c-{trace_id}", **kwargs)


# ---------------------------------------------------------------- save

def test_save_stores_the_plan_rates_the_run_up_and_files_the_proposal(engine):
    cid = ready(engine)

    report = save(engine, cid=cid)

    answer = report.answer
    assert isinstance(answer, SavedAnswer) and len(answer.id) >= 16
    assert (answer.question, answer.datasource_id, answer.trace_id) == (QUESTION, "shop", "t1")
    assert (answer.user_id, answer.tenant_id, answer.chart) == ("bob", engine._runtime.tenant_id, "auto")
    assert answer.plan.plans == {"sq1": plan_json()} and answer.plan.decomposition["sub_queries"][0]["id"] == "sq1"
    assert "rows" not in answer.model_dump_json(), "a saved answer holds a plan, never rows"
    [rating] = engine.feedback.list()
    assert (rating["trace_id"], rating["rating"]) == ("t1", "up")
    [proposal] = engine.proposals.list(user_context=ADMIN, kind="verified_query")
    assert (proposal.origin, proposal.change["sql"].strip()) == ("thumbs_up", SQL)
    assert (report.proposed, report.proposal_id, report.not_proposed_reason) == (True, proposal.id, None)


def test_the_note_is_kept_on_the_rating(engine):
    cid = ready(engine)
    save(engine, cid=cid, note="exactly what I needed")
    assert engine.feedback.list()[0]["note"] == "exactly what I needed"


def test_saving_the_same_answer_twice_is_one_answer_and_one_proposal(engine):
    cid = ready(engine)

    first, second = save(engine, cid=cid), save(engine, cid=cid)

    assert second.answer.id == first.answer.id and second.proposal_id == first.proposal_id
    assert len(engine.answers.list(user_context=ANALYST)) == 1
    assert len(engine.proposals.list(user_context=ADMIN, kind="verified_query")) == 1


@pytest.mark.parametrize("trace, step, reason", [
    ({"sql": [SQL, "SELECT 2"]}, {}, "multiple_statements"),
    ({"source": "verified"}, {"verified": True}, "verified"),
    ({"status": "error"}, {}, "failed"),
])
def test_yes_saves_even_when_no_proposal_can_be_filed(engine, trace, step, reason):
    keep_trace(engine, "t1", **trace)
    converse(engine, result=answered("t1", **step))

    report = save(engine)

    assert report.proposed is False and report.proposal_id is None and report.not_proposed_reason == reason
    assert [a.id for a in engine.answers.list(user_context=ANALYST)] == [report.answer.id]
    assert engine.proposals.list(user_context=ADMIN, kind="verified_query") == []
    assert [r["rating"] for r in engine.feedback.list()] == ["up"]


def test_a_verified_answer_keeps_its_verified_plan(engine):
    keep_trace(engine, "t1", source="verified")
    converse(engine, result=answered("t1", verified=True))

    plan = save(engine).answer.plan

    assert plan.verified == VerifiedRef(id="order_count", params={}) and plan.verified_datasource_id == "shop"


def test_a_run_without_a_kept_trace_is_still_saved(engine):
    converse(engine)  # no trace was kept (settings.traces.mode)

    report = save(engine)

    assert report.proposed is False and report.not_proposed_reason == "no_trace" and report.answer.id
    assert engine.feedback.list() == []


@pytest.mark.parametrize("status", ["error", "needs_clarification", "stopped"])
def test_a_step_that_did_not_answer_is_not_savable(engine, status):
    keep_trace(engine, "t1")
    converse(engine, step_status=status)
    with pytest.raises(NotSavable):
        save(engine)
    assert engine.answers.list(user_context=ANALYST) == []


def test_a_trace_id_that_is_not_the_steps_run_is_not_savable(engine):
    keep_trace(engine, "other")
    converse(engine)
    with pytest.raises(NotSavable, match="not the run"):
        save(engine, trace_id="other")
    assert engine.feedback.list() == []


def test_a_missing_step_is_not_savable_and_a_foreign_conversation_is_not_found(engine):
    keep_trace(engine, "t1")
    converse(engine)
    with pytest.raises(NotSavable):
        engine.answers.save(trace_id="t1", conversation_id="c1", step=StepRef(step=2), user_context=ANALYST)
    with pytest.raises(ConversationNotFound):
        save(engine, UserContext(user_id="eve", roles=["analyst"]))


def test_the_caller_is_required(engine):
    with pytest.raises(TypeError):
        engine.answers.list(user_context=None)
    with pytest.raises(TypeError):
        engine.answers.save(trace_id="t1", conversation_id="c1", step=StepRef(step=1), user_context=UserContext())


# ---------------------------------------------------------------- the cap

def test_the_cap_refuses_the_next_save_by_name_and_a_delete_frees_a_place(engine):
    ids = [save(engine, cid=ready(engine, f"t{n}"), trace_id=f"t{n}").answer.id for n in range(3)]
    cid = ready(engine, "t9")

    with pytest.raises(AnswerLimitReached, match="3") as full:
        save(engine, cid=cid, trace_id="t9")
    assert (full.value.code, full.value.status) == ("ANSWER_LIMIT_REACHED", 409)
    assert len(engine.feedback.list()) == 3, "a refused save rates nothing"

    engine.answers.delete(ids[0], user_context=ANALYST)
    assert save(engine, cid=cid, trace_id="t9").answer.id


def test_the_cap_is_per_user_and_defaults_to_100(engine):
    for n in range(3):
        save(engine, cid=ready(engine, f"t{n}"), trace_id=f"t{n}")
    other = UserContext(user_id="cy", roles=["analyst"])
    assert save(engine, other, cid=ready(engine, "c1", other), trace_id="c1").answer.user_id == "cy"
    assert engine.config.answers.max_per_user == 3
    from nl2sql.config import Answers
    assert Answers().max_per_user == 100


# ---------------------------------------------------------------- owner-only access

def test_an_answer_is_its_owners_alone_an_admin_included(engine):
    mine = save(engine, cid=ready(engine)).answer
    admin = UserContext(user_id="ada", roles=["admin"])
    stranger = UserContext(user_id="bob", tenant_id="other", roles=["analyst"])

    assert engine.answers.get(mine.id, user_context=ANALYST) == mine
    assert [a.id for a in engine.answers.list(user_context=ANALYST)] == [mine.id]
    for other in (ADMIN, admin, stranger):
        assert engine.answers.list(user_context=other) == []
        with pytest.raises(AnswerNotFound) as missing:
            engine.answers.get(mine.id, user_context=other)
        assert (missing.value.code, missing.value.status) == ("ANSWER_NOT_FOUND", 404)
        with pytest.raises(AnswerNotFound):
            engine.answers.delete(mine.id, user_context=other)
        with pytest.raises(AnswerNotFound):
            engine.answers.run(mine.id, user_context=other)
    assert engine.answers.get(mine.id, user_context=ANALYST) == mine, "a refused delete deletes nothing"


def test_list_is_newest_first_and_filters_by_datasource(engine):
    first = save(engine, cid=ready(engine, "t1"), trace_id="t1").answer
    second = save(engine, cid=ready(engine, "t2"), trace_id="t2").answer

    assert [a.id for a in engine.answers.list(user_context=ANALYST)] == [second.id, first.id]
    assert len(engine.answers.list(user_context=ANALYST, datasource_id="shop")) == 2
    assert engine.answers.list(user_context=ANALYST, datasource_id="elsewhere") == []


def test_delete_removes_the_answer(engine):
    mine = save(engine, cid=ready(engine)).answer
    engine.answers.delete(mine.id, user_context=ANALYST)
    assert engine.answers.list(user_context=ANALYST) == []
    with pytest.raises(AnswerNotFound):
        engine.answers.get(mine.id, user_context=ANALYST)


def test_every_refusal_is_an_engine_error():
    assert all(issubclass(e, NL2SQLError) for e in (AnswerNotFound, AnswerLimitReached, NotSavable))


# ---------------------------------------------------------------- run

def test_run_replays_the_stored_plan_as_the_viewer(engine, monkeypatch):
    mine = save(engine, cid=ready(engine)).answer
    seen: List[Dict[str, Any]] = []

    def run_query(api: QueryAPI, question: str, **kwargs: Any) -> QueryResult:
        seen.append({"question": question, **kwargs})
        return QueryResult(status="success", trace_id="rerun")

    monkeypatch.setattr(QueryAPI, "run_query", run_query)
    override = object()

    result = engine.answers.run(mine.id, user_context=ANALYST, llm_override=override, approvals=["r1"])

    assert result.trace_id == "rerun"
    [call] = seen
    assert call["question"] == QUESTION and call["user_context"] == ANALYST
    assert call["llm_override"] is override and call["approvals"] == ["r1"]
    assert call["plan"].decomposition.sub_queries[0].id == "sq1" and set(call["plan"].plans) == {"sq1"}


def test_run_applies_the_viewers_scope(engine):
    # Ada saved a plan over salaries; the same person holding only the analyst role may not read that table.
    keep_trace(engine, "t1", ADMIN)
    converse(engine, ADMIN, result=answered("t1", table="salaries", column="amount"))
    mine = save(engine, ADMIN).answer

    result = engine.answers.run(mine.id, user_context=UserContext(user_id="ada", roles=["analyst"]))

    assert result.status == "error" and any(e.get("error_code") == "SECURITY_VIOLATION" for e in result.errors)
    assert not any(e.get("error_code") == "TILE_NEEDS_FIXING" for e in result.errors)


def test_a_plan_the_schema_has_outgrown_comes_back_needs_fixing(engine):
    keep_trace(engine, "t1")
    converse(engine, result=answered("t1", column="no_such_column"))
    mine = save(engine).answer

    result = engine.answers.run(mine.id, user_context=ANALYST)

    assert result.status == "error"
    assert result.errors[0]["error_code"] == "TILE_NEEDS_FIXING" and "no_such_column" in result.errors[0]["message"]
    assert engine.answers.get(mine.id, user_context=ANALYST) == mine, "the answer stays: its owner can ask again"


def test_a_plan_that_does_not_parse_comes_back_needs_fixing(engine):
    keep_trace(engine, "t1")
    converse(engine)
    mine = save(engine).answer
    broken = mine.model_copy(update={"plan": mine.plan.model_copy(update={"plans": {"sq1": {"tables": "orders"}}})})
    engine._runtime.state.put_saved_answer(broken.model_dump(mode="json"))

    result = engine.answers.run(mine.id, user_context=ANALYST)

    assert result.status == "error" and result.errors[0]["error_code"] == "TILE_NEEDS_FIXING"


def test_run_needs_no_model(engine):
    # A saved answer re-runs as a tile does (answer=False): rows come back with no model reachable.
    mine = save(engine, cid=ready(engine)).answer

    result = engine.answers.run(mine.id, user_context=ANALYST)

    assert result.status == "success", result.errors
    assert result.sub_queries[0].rows or result.result_manifest


def test_a_verified_step_without_a_datasource_is_not_savable(engine):
    keep_trace(engine, "t1")
    verified = answered("t1", verified=True).model_copy(
        update={"source": AnswerSource(type="verified", id="order_count")})
    converse(engine, result=verified)

    with pytest.raises(NotSavable):
        save(engine)


def test_a_save_holds_the_owners_lock_around_the_count_and_the_put(engine):
    cid = ready(engine)
    state = engine._runtime.state

    with state.lock(f"answers:{engine._runtime.tenant_id}:bob", ttl_s=30):
        with pytest.raises(LockHeld):
            save(engine, cid=cid)
    assert engine.answers.list(user_context=ANALYST) == []


def test_concurrent_saves_never_pass_the_cap(engine):
    from concurrent.futures import ThreadPoolExecutor

    cids = [ready(engine, f"t{n}") for n in range(6)]

    def attempt(n: int) -> None:
        try:
            save(engine, cid=cids[n], trace_id=f"t{n}")
        except (AnswerLimitReached, LockHeld):
            pass

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(attempt, range(6)))

    assert len(engine.answers.list(user_context=ANALYST)) <= 3


# ---------------------------------------------------------------- the store

def test_the_store_keeps_documents_by_owner(engine):
    state = engine._runtime.state
    doc = {"id": "a1", "tenant_id": "acme", "user_id": "ana", "created_at": NOW.isoformat(), "n": 1}
    state.put_saved_answer(doc)
    state.put_saved_answer({**doc, "id": "a2", "created_at": NOW.replace(hour=13).isoformat()})
    state.put_saved_answer({**doc, "id": "a3", "user_id": "bob"})

    assert state.get_saved_answer("a1") == doc and state.get_saved_answer("zz") is None
    assert [d["id"] for d in state.list_saved_answers("acme", "ana")] == ["a2", "a1"]
    assert state.delete_saved_answer("a1") is True and state.delete_saved_answer("a1") is False
    assert [d["id"] for d in state.list_saved_answers("acme", "bob")] == ["a3"]


def test_the_migration_is_registered_and_the_store_interface_is_version_6():
    from nl2sql.registry import INTERFACE_VERSIONS
    from nl2sql.storage.sqlite import MIGRATIONS, SqliteState

    assert MIGRATIONS[-1][0] == "0008_saved_answers"
    assert INTERFACE_VERSIONS["store"] == 6 == SqliteState.interface_version
