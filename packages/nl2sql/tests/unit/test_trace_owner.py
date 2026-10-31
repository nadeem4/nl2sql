"""Traces and ratings are bound to the caller who asked the run (docs/architecture/wave_d.md, B13 and row G1).

The trace records its asker as ``owner``, exactly as the result manifest does (``ResultOwner``). ``traces.get``
with a ``user_context`` answers only that asker, and another caller's run is None, as a missing one. With no
context it is the trusted in-process operator and reads any run. ``feedback.record(trace_id, rating, ...)``
reads the run's own trace as the caller and stores what it finds there.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from nl2sql import NL2SQL, ResultNotFound, TraceNotFound, UserContext
from nl2sql.api.feedback_api import FeedbackAPI
from nl2sql.api.query_api import QueryAPI
from nl2sql.api.traces_api import TracesAPI
from nl2sql.execution.artifacts.results import ResultOwner
from nl2sql.pipeline import runtime
from nl2sql.storage.sqlite import SqliteState
from nl2sql.testing.runtime import fake_runtime


ANN = UserContext(user_id="ann", tenant_id="acme", roles=["analyst"])
BOB = UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])
ANON = UserContext(roles=["viewer"])


class _State(TypedDict, total=False):
    trace_id: str
    hits: Annotated[list, operator.add]


def _parallel_graph():
    builder = StateGraph(_State)
    builder.add_node("left", lambda state: {"hits": ["left"]})
    builder.add_edge(START, "left")
    builder.add_edge("left", END)
    return builder.compile()


def _trace(trace_id, owner, question="How many customers?"):
    return {"trace_format_version": 1, "trace_id": trace_id, "nodes": [], "owner": owner,
            "request": {"question": question, "roles": ["analyst"]},
            "result": {"trace_id": trace_id, "status": "success", "source": {"type": "generated"},
                       "sub_queries": [{"id": "sq1", "sql": "SELECT 1", "datasource_id": "shop"}]}}


def _state(tmp_path):
    state = SqliteState(tmp_path / 'state.db')
    state.migrate()
    return state


def _kept(ctx, trace_id, asker, **kwargs):
    ctx.traces.write(trace_id, _trace(trace_id, ResultOwner.of(asker, ctx.tenant_id).model_dump(), **kwargs))


def test_trace_records_the_asker(monkeypatch):
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _parallel_graph())
    ctx = fake_runtime(settings={"traces": {"mode": "always"}})

    named = QueryAPI(ctx).run_query("How many?", user_context=ANN)
    anonymous = QueryAPI(ctx).run_query("How many?", user_context=ANON)

    assert ctx.traces.get(named.trace_id)["owner"] == {"tenant_id": "acme", "user_id": "ann", "roles": None}
    assert ctx.traces.get(anonymous.trace_id)["owner"] == {"tenant_id": ctx.tenant_id, "user_id": None,
                                                           "roles": ["viewer"]}


def test_the_asker_reads_their_trace():
    ctx = fake_runtime()
    _kept(ctx, "run-1", ANN)

    assert TracesAPI(ctx).get("run-1", user_context=ANN)["trace_id"] == "run-1"


@pytest.mark.parametrize("other", [BOB, UserContext(user_id="ann", tenant_id="other", roles=["analyst"]), ANON])
def test_another_users_trace_is_not_found(other):
    ctx = fake_runtime()
    _kept(ctx, "run-1", ANN)

    assert TracesAPI(ctx).get("run-1", user_context=other) is None


def test_an_anonymous_trace_is_read_by_the_same_roles_only():
    ctx = fake_runtime()
    _kept(ctx, "run-1", ANON)

    assert TracesAPI(ctx).get("run-1", user_context=UserContext(roles=["viewer"])) is not None
    assert TracesAPI(ctx).get("run-1", user_context=UserContext(roles=["admin"])) is None


def test_cli_trace_reads_any_run():
    """No ``user_context`` is the trusted in-process operator (``nl2sql trace <id>``)."""
    ctx = fake_runtime()
    _kept(ctx, "run-1", ANN)
    ctx.traces.write("old", {"trace_format_version": 1, "trace_id": "old", "nodes": []})

    assert TracesAPI(ctx).get("run-1")["trace_id"] == "run-1"
    assert TracesAPI(ctx).get("old")["trace_id"] == "old"


def test_ownerless_trace_is_read_only_by_the_operator():
    ctx = fake_runtime()
    ctx.traces.write("old", {"trace_format_version": 1, "trace_id": "old", "nodes": []})

    assert TracesAPI(ctx).get("old", user_context=ANN) is None
    assert TracesAPI(ctx).get("old") is not None


def test_a_trace_read_needs_a_user_context_or_none():
    ctx = fake_runtime()
    with pytest.raises(TypeError):
        TracesAPI(ctx).get("run-1", user_context={"user_id": "ann"})


# ---------------------------------------------------------------- ratings

def test_record_reads_the_runs_own_trace(tmp_path):
    ctx = fake_runtime(state=_state(tmp_path))
    _kept(ctx, "run-1", ANN, question="How many customers?")

    stored = FeedbackAPI(ctx).record("run-1", "down", user_context=ANN, note="wrong")

    assert stored["trace_id"] == "run-1" and stored["rating"] == "down" and stored["note"] == "wrong"
    assert stored["question"] == "How many customers?" and stored["role"] == "analyst"
    assert stored["sql"] == ["SELECT 1"] and stored["datasource_ids"] == ["shop"]
    assert ctx.state.list_feedback()[0]["trace_id"] == "run-1"


def test_record_of_another_users_run_is_not_found(tmp_path):
    ctx = fake_runtime(state=_state(tmp_path))
    _kept(ctx, "run-1", ANN)

    with pytest.raises(TraceNotFound):
        FeedbackAPI(ctx).record("run-1", "up", user_context=BOB)
    with pytest.raises(TraceNotFound):
        FeedbackAPI(ctx).record("no-such-run", "up", user_context=ANN)
    assert ctx.state.list_feedback() == []


def test_trace_not_found_is_a_404_lookup_error():
    assert issubclass(TraceNotFound, LookupError) and TraceNotFound.status == 404


def test_record_requires_a_caller():
    ctx = fake_runtime()
    _kept(ctx, "run-1", ANN)

    with pytest.raises(TypeError):
        FeedbackAPI(ctx).record("run-1", "up")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        FeedbackAPI(ctx).record("run-1", "up", user_context=None)  # type: ignore[arg-type]


def test_record_rejects_an_unknown_rating():
    ctx = fake_runtime()
    _kept(ctx, "run-1", ANN)

    with pytest.raises(ValueError):
        FeedbackAPI(ctx).record("run-1", "meh", user_context=ANN)


# ---------------------------------------------------------------- charts

def test_chart_with_another_users_trace_is_not_found():
    from nl2sql.api.results_api import ResultsAPI

    ctx = fake_runtime()
    with pytest.raises(ResultNotFound):
        ResultsAPI(ctx).chart("run-1", user_context=BOB)
