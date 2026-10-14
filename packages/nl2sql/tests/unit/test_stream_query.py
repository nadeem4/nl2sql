"""``stream_query``: one run, as events, ending in what ``run_query`` returns.

The real ``run_with_graph`` runs a small graph with the pipeline's node names
(``build_graph`` is swapped for it), so the events come through the same
callbacks, thread and telemetry a real run uses.
"""
import threading
from types import SimpleNamespace

import pytest
from langgraph.graph import END, StateGraph

from nl2sql import NL2SQL, CancellationToken, QueryEvent
from nl2sql.api.query_api import QueryAPI
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.testing.runtime import fake_runtime

SUMMARY = ResultSummary(node_id="sq_1", columns=["genre"], row_count=25)
ANSWER = {"summary": "Rock leads.", "format_type": "text", "content": "Rock leads.", "warnings": []}
SQL_AGENT_NODES = ["ast_planner", "logical_validator", "generator", "executor"]


def _graph(seen_config, hold=None):
    sub = StateGraph(SubgraphExecutionState)
    for name in SQL_AGENT_NODES:
        sub.add_node(name, lambda state: {})
    sub.set_entry_point(SQL_AGENT_NODES[0])
    for a, b in zip(SQL_AGENT_NODES, SQL_AGENT_NODES[1:]):
        sub.add_edge(a, b)
    sub.add_edge(SQL_AGENT_NODES[-1], END)
    sub = sub.compile()

    def decomposer(state, config):
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
    holder = {"hold": None}
    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph",
                        lambda ctx, execute=True: _graph(seen_config, holder["hold"]))
    query = QueryAPI(fake_runtime(artifact_store=None))
    query.seen_config = seen_config
    query.holder = holder
    return query


def _volatile_free(body):
    return {k: v for k, v in body.items() if k not in ("trace_id", "timings", "trace_path")}


def test_events_come_in_run_order_and_end_with_the_result(api):
    events = list(api.stream_query("Which genre sells the most tracks?"))

    assert all(isinstance(e, QueryEvent) for e in events)
    steps = [(e.type, e.data.get("node")) for e in events if e.type.startswith("node_")]
    expected = [("node_started", "datasource_resolver"), ("node_finished", "datasource_resolver"),
                ("node_started", "decomposer"), ("node_finished", "decomposer"),
                ("node_started", "sql_agent")]
    for name in SQL_AGENT_NODES:
        expected += [("node_started", name), ("node_finished", name)]
    expected += [("node_finished", "sql_agent"),
                 ("node_started", "aggregator"), ("node_finished", "aggregator"),
                 ("node_started", "answer_synthesizer"), ("node_finished", "answer_synthesizer")]
    assert steps == expected

    types = [e.type for e in events]
    manifest = next(e for e in events if e.type == "result_manifest")
    # The manifest arrives as soon as the aggregator is done, before the answer is written.
    assert types.index("result_manifest") < [i for i, e in enumerate(events)
                                             if e.data.get("node") == "answer_synthesizer"][0]
    assert manifest.data["results"] == [SUMMARY.model_dump(mode="json")]
    final = events[-1]
    assert final.type == "final" and types.count("final") == 1 and "error" not in types
    assert manifest.data["trace_id"] == final.data["trace_id"] != ""


def test_the_final_event_is_what_run_query_returns(api):
    final = list(api.stream_query("Which genre sells the most tracks?"))[-1]
    plain = api.run_query("Which genre sells the most tracks?").model_dump(mode="json")

    assert _volatile_free(final.data) == _volatile_free(plain)
    assert final.data["final_answer"]["summary"] == "Rock leads."
    assert final.data["result_manifest"] == [SUMMARY.model_dump(mode="json")]
    assert set(final.data["timings"]) >= {"answer_synthesizer", "LangGraph"}


def test_only_a_streamed_run_asks_the_answer_model_to_stream(api):
    api.run_query("q")
    assert not api.seen_config.get("stream_answer")
    list(api.stream_query("q"))
    assert api.seen_config.get("stream_answer") is True


def test_a_run_that_cannot_start_ends_in_one_error_event(monkeypatch):
    def broken(ctx, execute=True):
        raise RuntimeError("no graph today")

    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph", broken)
    events = list(QueryAPI(fake_runtime(artifact_store=None)).stream_query("q"))

    assert [e.type for e in events] == ["error"]
    assert events[0].data == {"message": "The run failed: no graph today", "error_code": "UNKNOWN_ERROR"}


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
    first = next(events)
    assert first.type == "node_started"
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
