"""``engine.events``, the trace's event list and log correlation (wave_a.md, sections 3 and 4, row E1).

- ``engine.events.subscribe`` is the operator's view: every run's events and
  the engine's own, failing open; ``catalog()`` lists every event.
- The trace document lists the run's events (all but ``answer.delta``).
- Every log line written during a run, on the caller's thread or a graph
  worker's, carries the run's ``trace_id`` (B14).
- The catalog marks which events the caller's stream carries (D17) and which
  the audit observer writes (E2): one definition each, extended by adding an
  event to the catalog.
"""
from __future__ import annotations

import logging
import operator
import threading
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from nl2sql.api.events_api import EventsAPI
from nl2sql.api.query_api import QueryAPI
from nl2sql.common.logger import TraceContextFilter
from nl2sql.events import EventInfo, Subscription
from nl2sql.events.catalog import AUDIT_EVENTS, CATALOG
from nl2sql.pipeline import runtime
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime

_USER = UserContext(user_id="ada", tenant_id="acme", roles=["analyst"])
log = logging.getLogger("nl2sql.test_run")


class _State(TypedDict, total=False):
    trace_id: str
    hits: Annotated[list, operator.add]


def _parallel_graph():
    """Two branches LangGraph runs on its own worker threads, each logging a line."""
    def node(name):
        def run(state):
            log.warning("step %s", name)
            return {"hits": [name]}
        return run

    builder = StateGraph(_State)
    builder.add_node("left", node("left"))
    builder.add_node("right", node("right"))
    builder.add_edge(START, "left")
    builder.add_edge(START, "right")
    builder.add_edge("left", END)
    builder.add_edge("right", END)
    return builder.compile()


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.addFilter(TraceContextFilter())
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capturing(monkeypatch):
    handler = _Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    monkeypatch.setattr(root, "level", logging.DEBUG)
    return handler


# ---------------------------------------------------------------- engine.events

def test_subscribe_receives_engine_and_run_events(monkeypatch):
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _parallel_graph())
    ctx = fake_runtime()
    events = EventsAPI(ctx)
    seen, finished = [], []

    subscription = events.subscribe(seen.append)
    only_finished = events.subscribe(finished.append, names=["run.finished"])
    ctx.bus.publish("index.started", {"datasource_id": "shop", "namespace": "shop"})
    result = QueryAPI(ctx).run_query("How many?", user_context=_USER)

    assert isinstance(subscription, Subscription)
    names = [e.name for e in seen]
    assert names[0] == "index.started" and seen[0].trace_id is None
    assert names[1] == "run.started" and names[-1] == "run.finished"
    assert {"node.started", "node.finished"} <= set(names)
    assert {e.trace_id for e in seen[1:]} == {result.trace_id}
    assert [e.name for e in finished] == ["run.finished"]

    subscription.close()
    only_finished.close()
    ctx.bus.publish("index.started", {"datasource_id": "shop", "namespace": "shop"})
    assert len(seen) == len(names)


def test_a_raising_subscriber_never_fails_the_run(monkeypatch):
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _parallel_graph())
    ctx = fake_runtime()

    def broken(event):
        raise RuntimeError("observer bug")

    EventsAPI(ctx).subscribe(broken)
    result = QueryAPI(ctx).run_query("How many?", user_context=_USER)
    assert result.trace_id and ctx.bus.failures > 0


def test_catalog_lists_every_event_with_its_schema_and_audiences():
    infos = EventsAPI(fake_runtime()).catalog()

    assert all(isinstance(info, EventInfo) for info in infos)
    by_name = {info.name: info for info in infos}
    assert set(by_name) == set(CATALOG)
    assert by_name["node.finished"].stream and not by_name["llm.call"].stream
    assert by_name["run.finished"].audit and not by_name["node.started"].audit
    assert "properties" in by_name["run.started"].data_schema


def test_the_audited_events_are_the_catalogs():
    assert AUDIT_EVENTS == {"run.finished", "run.failed", "llm.changed", "hook.decision", "policy.decision",
                            "run.limit_reached", "index.swapped", "retention.purged", "schema.drifted"}
    assert AUDIT_EVENTS == {name for name, spec in CATALOG.items() if spec.audit}


# ---------------------------------------------------------------- the trace

def test_trace_document_lists_the_run_events(monkeypatch):
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _parallel_graph())
    ctx = fake_runtime(settings={"traces": {"mode": "always"}})

    result = QueryAPI(ctx).run_query("How many?", user_context=_USER)
    doc = ctx.traces.get(result.trace_id)

    names = [event["name"] for event in doc["events"]]
    assert names[0] == "run.started"
    assert names.count("node.started") == 2 and names.count("node.finished") == 2
    assert "answer.delta" not in names
    assert {event["trace_id"] for event in doc["events"]} == {result.trace_id}


# ---------------------------------------------------------------- log correlation (B14)

def test_every_log_line_in_a_run_has_its_trace_id(monkeypatch):
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _parallel_graph())
    handler = _capturing(monkeypatch)
    try:
        result = QueryAPI(fake_runtime()).run_query("How many?", user_context=_USER)
    finally:
        logging.getLogger().removeHandler(handler)

    ours = [r for r in handler.records if r.name.startswith("nl2sql")]
    assert any(r.name == "nl2sql.test_run" for r in ours)
    assert [r.getMessage() for r in ours if r.trace_id != result.trace_id] == []
    assert {r.tenant_id for r in ours} == {"acme"}


def test_log_lines_on_worker_threads_have_the_trace_id(monkeypatch):
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _parallel_graph())
    handler = _capturing(monkeypatch)
    try:
        events = list(QueryAPI(fake_runtime()).stream_query("How many?", user_context=_USER))
    finally:
        logging.getLogger().removeHandler(handler)

    steps = [r for r in handler.records if r.name == "nl2sql.test_run"]
    assert len(steps) == 2
    assert all(r.threadName != threading.current_thread().name for r in steps)
    assert {r.trace_id for r in steps} == {events[-1].trace_id}
