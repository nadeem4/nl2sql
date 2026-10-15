"""``stream_query``: one run, as bus ``Event``s from the caller-safe allow-list (D17).

The real ``run_with_graph`` runs a small graph with the pipeline's node names
(``build_graph`` is swapped for it), so the events come through the same
callbacks, scope, thread and telemetry a real run uses. The design is
docs/architecture/wave_a.md, section 3, "The run's stream".
"""
import threading
from types import SimpleNamespace

import pytest
from langgraph.graph import END, StateGraph

from nl2sql import NL2SQL, CancellationToken, Event
from nl2sql.api.query_api import QueryAPI
from nl2sql.events import publish
from nl2sql.events.catalog import CATALOG, STREAM_EVENTS
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.testing.runtime import fake_runtime

SUMMARY = ResultSummary(node_id="sq_1", columns=["genre"], row_count=25)
ANSWER = {"summary": "Rock leads.", "format_type": "text", "content": "Rock leads.", "warnings": []}
SQL_AGENT_NODES = ["ast_planner", "logical_validator", "generator", "executor"]
TERMINAL = ("run.finished", "run.failed")


def _graph(seen_config, hold=None, during=None):
    sub = StateGraph(SubgraphExecutionState)
    for name in SQL_AGENT_NODES:
        sub.add_node(name, lambda state: {})
    sub.set_entry_point(SQL_AGENT_NODES[0])
    for a, b in zip(SQL_AGENT_NODES, SQL_AGENT_NODES[1:]):
        sub.add_edge(a, b)
    sub.add_edge(SQL_AGENT_NODES[-1], END)
    sub = sub.compile()

    def decomposer(state, config):
        if during is not None:
            during()
        if hold is not None:
            hold.set()
            config["configurable"]["cancellation_token"].wait(timeout=5)
        return {}

    def answer_synthesizer(state, config):
        seen_config.update(config.get("configurable") or {})
        return {"answer_synthesizer_response": AnswerSynthesizerResponse(final_answer=ANSWER)}

    graph = StateGraph(GraphState)
    graph.add_node("datasource_resolver", lambda state: {})
    graph.add_node("decomposer", decomposer)
    graph.add_node("sql_agent",
                   lambda state, config: (sub.invoke({"trace_id": state.trace_id}, config=config), {})[1])
    graph.add_node("aggregator", lambda state: {"aggregator_response": AggregatorResponse(results=[SUMMARY])})
    graph.add_node("answer_synthesizer", answer_synthesizer)
    graph.set_entry_point("datasource_resolver")
    for a, b in [("datasource_resolver", "decomposer"), ("decomposer", "sql_agent"),
                 ("sql_agent", "aggregator"), ("aggregator", "answer_synthesizer")]:
        graph.add_edge(a, b)
    graph.add_edge("answer_synthesizer", END)
    return graph.compile()


@pytest.fixture
def api(monkeypatch):
    seen_config = {}
    holder = {"hold": None, "during": None}
    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph",
                        lambda ctx, execute=True: _graph(seen_config, holder["hold"], holder["during"]))
    query = QueryAPI(fake_runtime(artifact_store=None))
    query.seen_config = seen_config
    query.holder = holder
    return query


def _volatile_free(body):
    return {k: v for k, v in body.items() if k not in ("trace_id", "timings", "trace_path")}


def test_stream_yields_events_in_publish_order(api):
    seen_on_bus = []
    api._ctx.bus.subscribe(seen_on_bus.append)

    events = list(api.stream_query("Which genre sells the most tracks?"))

    assert all(isinstance(e, Event) for e in events)
    assert events[0].name == "run.started"
    steps = [(e.name, e.data["node"]) for e in events if e.name.startswith("node.")]
    expected = [("node.started", "datasource_resolver"), ("node.finished", "datasource_resolver"),
                ("node.started", "decomposer"), ("node.finished", "decomposer"),
                ("node.started", "sql_agent")]
    for name in SQL_AGENT_NODES:
        expected += [("node.started", name), ("node.finished", name)]
    expected += [("node.finished", "sql_agent"),
                 ("node.started", "aggregator"), ("node.finished", "aggregator"),
                 ("node.started", "answer_synthesizer"), ("node.finished", "answer_synthesizer")]
    assert steps == expected
    # The stream is the bus's own events for this run, in the order they were published.
    assert [(e.name, e.at) for e in events] == [(e.name, e.at) for e in seen_on_bus]
    assert {e.trace_id for e in events} == {events[-1].data["result"]["trace_id"]}

    names = [e.name for e in events]
    manifest = next(e for e in events if e.name == "result.manifest")
    # The manifest arrives as soon as the aggregator is done, before the answer is written.
    assert names.index("result.manifest") < [i for i, e in enumerate(events)
                                             if e.data.get("node") == "answer_synthesizer"][0]
    assert manifest.data == {"results": [SUMMARY.model_dump(mode="json")]}


def test_stream_ends_with_exactly_one_run_finished_or_failed(api):
    events = list(api.stream_query("q"))
    assert [e.name for e in events if e.name in TERMINAL] == ["run.finished"]
    assert events[-1].name == "run.finished"


def test_a_run_that_cannot_start_ends_in_one_run_failed(monkeypatch):
    def broken(ctx, execute=True):
        raise RuntimeError("no graph today")

    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph", broken)
    events = list(QueryAPI(fake_runtime(artifact_store=None)).stream_query("q"))

    assert [e.name for e in events] == ["run.started", "run.failed"]
    assert events[-1].data == {"message": "The run failed: no graph today", "error_code": "UNKNOWN_ERROR",
                               "user_id": None, "roles": []}
    assert events[-1].trace_id == events[0].trace_id


def test_stream_terminal_event_carries_the_query_result(api):
    seen_on_bus = []
    api._ctx.bus.subscribe(seen_on_bus.append, ["run.finished"])

    final = list(api.stream_query("Which genre sells the most tracks?"))[-1]
    plain = api.run_query("Which genre sells the most tracks?").model_dump(mode="json")

    result = final.data["result"]
    assert _volatile_free(result) == _volatile_free(plain)
    assert result["final_answer"]["summary"] == "Rock leads."
    assert set(result["timings"]) >= {"answer_synthesizer", "LangGraph"}
    # The summary is the bus event's; only the stream's copy holds the result (D15).
    assert final.data["status"] == result["status"]
    assert {k: v for k, v in final.data.items() if k != "result"} == seen_on_bus[0].data
    assert all("result" not in e.data for e in seen_on_bus)


def test_stream_never_yields_policy_or_hook_or_llm_call_events(api):
    def operator_events():
        publish("policy.decision", {"datasource_id": "shop", "schema": "main", "table": "salaries",
                                    "rule": "deny", "step": "logical_validator"})
        publish("hook.decision", {"hook": "guard", "point": "plan.after", "decision": "allow"})
        publish("llm.call", {"node": "decomposer", "model": "gpt-4o"})
        publish("plan_cache.lookup", {"datasource_id": "shop", "hit": False})

    api.holder["during"] = operator_events
    on_bus = []
    api._ctx.bus.subscribe(on_bus.append)

    names = {e.name for e in api.stream_query("q")}

    assert {"policy.decision", "hook.decision", "llm.call", "plan_cache.lookup"} <= {e.name for e in on_bus}
    assert names <= STREAM_EVENTS
    assert not names & {"policy.decision", "hook.decision", "llm.call", "plan_cache.lookup"}


def test_the_stream_allow_list_is_the_catalogs():
    assert STREAM_EVENTS == {"run.started", "node.started", "node.finished", "answer.delta", "result.manifest",
                             "sanity.flagged", "run.limit_reached", "run.finished", "run.failed"}
    assert STREAM_EVENTS == {name for name, spec in CATALOG.items() if spec.stream}


def test_an_open_scopes_earlier_events_reach_the_stream_with_extra_names(api):
    """Wave C's turn: run.started and its own event come before stream_query, in the turn's scope."""
    scope = api.open_scope("and by month?")
    with scope.entered():
        publish("plan_cache.lookup", {"datasource_id": "shop", "hit": True})

    events = list(api.stream_query("Revenue by month?", scope=scope,
                                   names=STREAM_EVENTS | {"plan_cache.lookup"}))

    assert [e.name for e in events[:2]] == ["run.started", "plan_cache.lookup"]
    assert events[0].data["question"] == "and by month?"
    assert events[-1].name == "run.finished" and {e.trace_id for e in events} == {scope.trace_id}


def test_only_a_streamed_run_asks_the_answer_model_to_stream(api):
    api.run_query("q")
    assert not api.seen_config.get("stream_answer")
    list(api.stream_query("q"))
    assert api.seen_config.get("stream_answer") is True


def test_the_callers_request_keys_go_with_the_run_to_its_thread(monkeypatch):
    # The hosted playground binds a visitor's key to the request; the graph is
    # built, and its clients with it, on the run's own thread.
    from nl2sql.llm.request_key import current_api_key, use_api_key

    seen = []

    def build(ctx, execute=True):
        seen.append(current_api_key())
        raise RuntimeError("stop here")

    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph", build)
    with use_api_key("sk-visitor-key"):
        list(QueryAPI(fake_runtime(artifact_store=None)).stream_query("q"))
    assert seen == ["sk-visitor-key"]


def test_a_consumer_that_stops_reading_cancels_the_run(api):
    api.holder["hold"] = threading.Event()
    token = CancellationToken()
    events = api.stream_query("q", cancellation_token=token)
    assert next(events).name == "run.started"
    assert next(events).name == "node.started"
    assert api.holder["hold"].wait(timeout=5)
    events.close()
    assert token.is_cancelled()


def test_the_facade_streams_through_the_query_api():
    engine = NL2SQL.__new__(NL2SQL)
    calls = []
    engine._queries = SimpleNamespace(
        stream_query=lambda question, **kw: calls.append({"question": question, **kw}) or iter(["e"]))
    assert list(engine.stream_query("q", datasource_id="chinook", execute=False)) == ["e"]
    assert calls[0]["question"] == "q" and calls[0]["datasource_id"] == "chinook"
    assert calls[0]["execute"] is False


# ---------------------------------------------------------------- the terminal event, whatever happens (review of #314)

def _ctx_with_timeout(timeout_s):
    ctx = fake_runtime(artifact_store=None)
    pipeline = ctx.config.settings.pipeline.model_copy(update={"timeout_s": timeout_s})
    settings = ctx.config.settings.model_copy(update={"pipeline": pipeline})
    ctx.config = ctx.config.model_copy(update={"settings": settings})
    return ctx


class _Graph:
    def __init__(self, on_invoke):
        self.invoke = on_invoke


def _terminals(events):
    return [e for e in events if e.name in TERMINAL]


def test_a_failure_after_the_scope_closed_still_ends_the_stream_in_run_failed(api, monkeypatch):
    original = QueryAPI.close_scope

    def close_then_break(self, scope, result):
        original(self, scope, result)
        raise RuntimeError("broke after closing")

    monkeypatch.setattr(QueryAPI, "close_scope", close_then_break)
    events = list(api.stream_query("q"))

    [terminal] = _terminals(events)
    assert events[-1] is terminal and terminal.name == "run.failed"
    assert terminal.trace_id == events[0].trace_id
    assert "broke after closing" in terminal.data["message"]


def test_a_collector_that_raises_on_the_terminal_event_still_ends_the_stream(api):
    scope = api.open_scope("q")

    def broken(event):
        raise RuntimeError("collector bug")

    scope.attach(broken, names=["run.finished"])
    events = list(api.stream_query("q", scope=scope))

    [terminal] = _terminals(events)
    assert events[-1] is terminal and terminal.name == "run.failed"
    assert terminal.trace_id == scope.trace_id


def test_an_event_published_while_the_stream_joins_an_open_scope_is_not_lost(api, monkeypatch):
    """Another thread publishes in the scope while the stream replays its events and subscribes."""
    from nl2sql.events import RunScope

    scope = api.open_scope("and by month?")
    original = RunScope.attach

    def attach_late(self, piece, *, names=None):
        if piece.__class__.__name__ != "RunEvents" and self is scope:
            other = threading.Thread(target=lambda: scope.publish(
                "node.started", {"node": "from_another_thread", "sub_query_id": None}))
            other.start()
            other.join(timeout=0.2)  # blocked on the scope's lock while it joins, if the join holds it
        return original(self, piece, names=names)

    monkeypatch.setattr(RunScope, "attach", attach_late)
    events = list(api.stream_query("Revenue by month?", scope=scope))

    assert "from_another_thread" in [e.data.get("node") for e in events]


def test_follow_replays_then_subscribes(api):
    scope = api.open_scope("q")
    seen = []
    scope.follow(seen.append, names={"run.started", "node.started"})
    scope.publish("node.started", {"node": "decomposer", "sub_query_id": None})
    scope.publish("plan_cache.lookup", {"datasource_id": "shop", "hit": True})

    assert [e.name for e in seen] == ["run.started", "node.started"]


def test_a_closed_scope_is_refused(api):
    scope = api.open_scope("q")
    api.close_scope(scope, api.run_query("q", scope=api.open_scope("q")))

    with pytest.raises(ValueError, match="closed"):
        api.stream_query("q", scope=scope)


def _slow(state, config):
    config["configurable"]["cancellation_token"].wait(timeout=2)
    return {}


def _interrupt(state, config):
    raise KeyboardInterrupt()


@pytest.mark.parametrize("graph, timeout_s, ending", [
    (_slow, 0.05, "run.finished"),        # the pipeline timed out: a result with PIPELINE_TIMEOUT
    (_interrupt, 60, "run.failed"),       # a KeyboardInterrupt on the stream's thread
], ids=["timeout", "keyboard_interrupt"])
def test_the_stream_ends_with_exactly_one_terminal_event_on_timeout_and_interrupt(monkeypatch, graph, timeout_s,
                                                                                  ending):
    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph", lambda ctx, execute=True: _Graph(graph))
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    events = list(QueryAPI(_ctx_with_timeout(timeout_s)).stream_query("q"))

    [terminal] = _terminals(events)
    assert events[-1] is terminal and terminal.name == ending
    if ending == "run.finished":
        assert [e["error_code"] for e in terminal.data["result"]["errors"]] == ["PIPELINE_TIMEOUT"]
