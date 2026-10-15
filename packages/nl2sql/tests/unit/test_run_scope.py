"""``RunScope``: the life of one question on the bus (docs/architecture/wave_a.md, section 3).

``QueryAPI`` opens it and closes it once, with ``run.finished`` (a summary,
never rows) or ``run.failed``. Its context is entered inside
``run_with_graph``'s worker, so code deep in a run, on LangGraph's own worker
threads too, publishes through ``nl2sql.events.publish`` and logs with the
run's ``trace_id``. A closed scope drops what comes after.
"""
from __future__ import annotations

import logging
import operator
import threading
import time
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from nl2sql.api.query_api import QueryAPI, QueryResult
from nl2sql.common.logger import TraceContextFilter
from nl2sql.events import EventBus, RunScope, current_scope, publish
from nl2sql.pipeline import runtime
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime

_USER = UserContext(user_id="ada", tenant_id="acme", roles=["analyst"])


def _ctx(timeout_s=60):
    ctx = fake_runtime(bus=EventBus())
    pipeline = ctx.config.settings.pipeline.model_copy(update={"timeout_s": timeout_s})
    settings = ctx.config.settings.model_copy(update={"pipeline": pipeline})
    ctx.config = ctx.config.model_copy(update={"settings": settings})
    return ctx


class _FakeGraph:
    def __init__(self, on_invoke):
        self._on_invoke = on_invoke

    def invoke(self, state, config=None):
        return self._on_invoke(state, config)


def _use_graph(monkeypatch, graph):
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: graph)


def _capture(bus):
    seen = []
    bus.subscribe(seen.append)
    return seen


# ---------------------------------------------------------------- the context

def test_module_publish_outside_a_run_scope_is_a_no_op():
    assert current_scope() is None
    assert publish("plan_cache.lookup", {"datasource_id": "shop", "hit": True}) is None


def test_trace_id_reaches_graph_worker_threads(monkeypatch):
    """Two parallel LangGraph branches run on its worker threads; both see the run's scope."""
    seen = {}

    class State(TypedDict):
        trace_id: str
        hits: Annotated[list, operator.add]

    def node(name):
        def run(state):
            record = logging.LogRecord("x", logging.INFO, "p", 1, "m", (), None)
            TraceContextFilter().filter(record)
            scope = current_scope()
            seen[name] = (threading.current_thread().name, record.trace_id, record.tenant_id,
                          scope.trace_id if scope else None)
            publish("plan_cache.lookup", {"datasource_id": name, "hit": False})
            return {"hits": [name]}
        return run

    builder = StateGraph(State)
    builder.add_node("left", node("left"))
    builder.add_node("right", node("right"))
    builder.add_edge(START, "left")
    builder.add_edge(START, "right")
    builder.add_edge("left", END)
    builder.add_edge("right", END)
    _use_graph(monkeypatch, builder.compile())
    ctx = _ctx()
    events = _capture(ctx.bus)

    result = QueryAPI(ctx).run_query("How many?", user_context=_USER)

    assert set(seen) == {"left", "right"}
    for thread, trace_id, tenant_id, scope_trace in seen.values():
        assert thread != threading.current_thread().name
        assert trace_id == result.trace_id and scope_trace == result.trace_id
        assert tenant_id == "acme"
    lookups = [e for e in events if e.name == "plan_cache.lookup"]
    assert len(lookups) == 2 and {e.trace_id for e in lookups} == {result.trace_id}


# ---------------------------------------------------------------- opening and closing

def test_run_finished_is_published_once_with_a_summary(monkeypatch):
    _use_graph(monkeypatch, _FakeGraph(lambda state, config: {
        "answer_synthesizer_response": {"final_answer": {"summary": "42", "content": "42"}}}))
    ctx = _ctx()
    events = _capture(ctx.bus)

    result = QueryAPI(ctx).run_query("How many?", datasource_id="shop", user_context=_USER)

    names = [e.name for e in events]
    assert names == ["run.started", "run.finished"]
    started, finished = events
    assert started.data["question"] == "How many?" and started.data["roles"] == ["analyst"]
    assert started.data["datasource_id"] == "shop" and started.data["execute"] is True
    assert started.trace_id == finished.trace_id == result.trace_id
    assert finished.tenant_id == "acme"
    summary = finished.data
    assert summary["status"] == result.status
    assert summary["source"] == "generated"
    assert summary["user_id"] == "ada" and summary["roles"] == ["analyst"]
    assert summary["duration_s"] >= 0
    assert "result" not in summary, "the bus never carries the QueryResult (D15)"


def test_run_failed_is_published_on_the_bus(monkeypatch):
    def broken(ctx, execute=True):
        raise RuntimeError("graph will not build")

    monkeypatch.setattr(runtime, "build_graph", broken)
    ctx = _ctx()
    events = _capture(ctx.bus)

    with pytest.raises(RuntimeError):
        QueryAPI(ctx).run_query("How many?", user_context=_USER)

    assert [e.name for e in events] == ["run.started", "run.failed"]
    assert "graph will not build" in events[1].data["message"]
    assert events[1].data["error_code"] == "UNKNOWN_ERROR"


def test_a_scope_closes_exactly_once():
    bus = EventBus()
    events = _capture(bus)
    scope = RunScope(bus, question="q", user_context=_USER, datasource_id=None, execute=True)
    scope.start()

    scope.fail("first", "UNKNOWN_ERROR")
    scope.finish(QueryResult(trace_id=scope.trace_id))
    scope.fail("again", "UNKNOWN_ERROR")

    assert [e.name for e in events] == ["run.started", "run.failed"]


def test_no_event_after_run_finished_on_timeout(monkeypatch):
    released = threading.Event()
    late_done = threading.Event()

    def slow(state, config):
        released.wait(5)
        publish("plan_cache.lookup", {"datasource_id": "shop", "hit": False})
        late_done.set()
        return {}

    _use_graph(monkeypatch, _FakeGraph(slow))
    ctx = _ctx(0.05)
    events = _capture(ctx.bus)
    scopes = []
    original = RunScope.start

    def keep(self):
        scopes.append(self)
        return original(self)

    monkeypatch.setattr(RunScope, "start", keep)

    QueryAPI(ctx).run_query("Slow?", user_context=_USER)
    released.set()
    assert late_done.wait(5)

    assert [e.name for e in events] == ["run.started", "run.finished"]
    assert scopes[0].late_events == 1


# ---------------------------------------------------------------- attaching run-scoped pieces

def test_attached_subscriber_sees_only_its_runs_events_and_is_detached_at_close():
    bus = EventBus()
    seen = []
    scope = RunScope(bus, question="q", user_context=_USER, datasource_id=None, execute=True)
    scope.attach(seen.append)
    scope.start()

    bus.publish("plan_cache.lookup", {"datasource_id": "x", "hit": True}, trace_id="another-run")
    scope.publish("plan_cache.lookup", {"datasource_id": "x", "hit": False})
    scope.finish(QueryResult(trace_id=scope.trace_id))
    bus.publish("plan_cache.lookup", {"datasource_id": "x", "hit": True}, trace_id=scope.trace_id)

    assert [e.name for e in seen] == ["run.started", "plan_cache.lookup", "run.finished"]


def test_attached_callback_reaches_the_graph_and_closable_pieces_are_closed(monkeypatch):
    from langchain_core.callbacks import BaseCallbackHandler

    class Budget(BaseCallbackHandler):
        closed = False

        def close(self):
            self.closed = True

    budget = Budget()
    passed = []

    def on_invoke(state, config):
        passed.extend(config["callbacks"])
        return {}

    _use_graph(monkeypatch, _FakeGraph(on_invoke))
    ctx = _ctx()
    scope = RunScope(ctx.bus, question="q", user_context=_USER, datasource_id=None, execute=True)
    scope.attach(budget)
    scope.start()

    state = runtime.run_with_graph(ctx, "q", user_context=_USER, scope=scope)
    scope.finish(QueryResult(trace_id=state["trace_id"]))

    assert budget in passed
    assert state["trace_id"] == scope.trace_id
    assert budget.closed


def test_two_scopes_in_flight_keep_their_own_trace_ids(monkeypatch):
    barrier = threading.Barrier(2, timeout=5)
    seen = []

    def on_invoke(state, config):
        barrier.wait()
        seen.append((state["trace_id"], current_scope().trace_id))
        time.sleep(0.01)
        return {}

    _use_graph(monkeypatch, _FakeGraph(on_invoke))
    ctx = _ctx()
    api = QueryAPI(ctx)
    threads = [threading.Thread(target=api.run_query, args=(f"q{i}",), kwargs={"user_context": _USER})
               for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert len(seen) == 2 and all(a == b for a, b in seen) and seen[0][0] != seen[1][0]


# ---------------------------------------------------------------- a scope opened before the run (Wave C's turn)

def _model_call(handlers, node="conversation_rewriter"):
    """What LangChain does around one chat-model call made with ``callbacks=handlers``."""
    import uuid

    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    run_id = uuid.uuid4()
    message = AIMessage(content="ok", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                        response_metadata={"model_name": "gpt-4o"})
    for handler in handlers:
        handler.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id=run_id,
                                    metadata={"langgraph_node": node, "ls_model_name": "gpt-4o"})
    for handler in handlers:
        handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id)


def test_open_scope_publishes_run_started_and_run_query_runs_in_it(monkeypatch):
    _use_graph(monkeypatch, _FakeGraph(lambda state, config: {}))
    ctx = _ctx()
    events = _capture(ctx.bus)
    api = QueryAPI(ctx)

    scope = api.open_scope("and by month?", user_context=_USER, datasource_id="shop")
    assert [e.name for e in events] == ["run.started"]
    assert events[0].data["question"] == "and by month?"

    result = api.run_query("Revenue by month?", datasource_id="shop", user_context=_USER, scope=scope)

    assert [e.name for e in events] == ["run.started", "run.finished"]
    assert result.trace_id == scope.trace_id == events[1].trace_id
    assert scope.closed


def test_stream_query_runs_in_an_open_scope(monkeypatch):
    _use_graph(monkeypatch, _FakeGraph(lambda state, config: {}))
    ctx = _ctx()
    events = _capture(ctx.bus)
    api = QueryAPI(ctx)

    scope = api.open_scope("and by month?", user_context=_USER)
    list(api.stream_query("Revenue by month?", user_context=_USER, scope=scope))

    assert [e.name for e in events] == ["run.started", "run.finished"]
    assert {e.trace_id for e in events} == {scope.trace_id}


def test_a_model_call_made_with_the_scopes_callbacks_counts_in_the_runs_usage(monkeypatch):
    _use_graph(monkeypatch, _FakeGraph(lambda state, config: {}))
    ctx = _ctx()
    api = QueryAPI(ctx)

    scope = api.open_scope("and by month?", user_context=_USER)
    _model_call(scope.callbacks())
    result = api.run_query("Revenue by month?", user_context=_USER, scope=scope)

    assert [call.node for call in result.usage.calls] == ["conversation_rewriter"]
    assert result.usage.total.input_tokens == 10


def test_scope_callbacks_hold_the_usage_collector_and_the_attached_handlers():
    from langchain_core.callbacks import BaseCallbackHandler

    from nl2sql.services.callbacks.token_handler import TokenUsageCallback

    class Budget(BaseCallbackHandler):
        pass

    budget = Budget()
    scope = QueryAPI(_ctx()).open_scope("q", user_context=_USER)
    scope.attach(budget)

    handlers = scope.callbacks()
    assert isinstance(handlers[0], TokenUsageCallback) and handlers[1:] == [budget]


def test_close_scope_with_a_result_built_without_a_graph_run():
    ctx = _ctx()
    events = _capture(ctx.bus)
    api = QueryAPI(ctx)

    scope = api.open_scope("what about that?", user_context=_USER)
    result = QueryResult(trace_id=scope.trace_id, status="error",
                         errors=[{"node": "conversation", "message": "Ask it as a full question.",
                                  "error_code": "UNKNOWN_ERROR", "severity": "ERROR"}])
    api.close_scope(scope, result)
    api.close_scope(scope, result)

    assert [e.name for e in events] == ["run.started", "run.finished"]
    assert events[1].data["status"] == "error"
    assert events[1].data["error_codes"] == ["UNKNOWN_ERROR"]
