"""``RunEvents``: the LangChain callback that publishes a run's steps on its scope.

A node's own run (its name is its ``langgraph_node``) becomes ``node.started``
and ``node.finished``, with the sub-query it belongs to; the runs inside it do
not. The aggregator's end publishes ``result.manifest``. The answer model's
tokens -- pieces of its forced tool call's arguments -- become ``answer.delta``
events, one per field. Each is validated against the event catalog.
"""
import uuid

import pytest

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from types import SimpleNamespace

from nl2sql.events.catalog import CATALOG
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.pipeline.events import RunEvents
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse


def _events():
    seen = []

    def publish(name, data):
        CATALOG[name].model.model_validate(data)  # what the bus would refuse
        seen.append(SimpleNamespace(type=name, data=dict(data)))

    return RunEvents(publish), seen


def _start_root(cb, trace_id="t-1"):
    root = uuid.uuid4()
    cb.on_chain_start({}, {"trace_id": trace_id, "user_query": "q"}, run_id=root, name="LangGraph")
    return root


def _start_node(cb, name, parent):
    run = uuid.uuid4()
    cb.on_chain_start({}, {}, run_id=run, parent_run_id=parent, name=name, metadata={"langgraph_node": name})
    return run


def test_a_node_run_is_started_then_finished_and_its_inner_runs_are_not_events():
    cb, seen = _events()
    root = _start_root(cb)
    node = _start_node(cb, "decomposer", root)
    inner = uuid.uuid4()
    cb.on_chain_start({}, {}, run_id=inner, parent_run_id=node, name="RunnableSequence",
                      metadata={"langgraph_node": "decomposer"})
    cb.on_chain_end({}, run_id=inner)
    cb.on_chain_end({}, run_id=node)
    cb.on_chain_end({}, run_id=root)

    assert [(e.type, e.data["node"]) for e in seen] == [("node.started", "decomposer"),
                                                         ("node.finished", "decomposer")]
    assert seen[1].data["ok"] is True and seen[1].data["seconds"] >= 0


def test_a_failed_node_finishes_not_ok():
    cb, seen = _events()
    node = _start_node(cb, "executor", _start_root(cb))
    cb.on_chain_error(RuntimeError("boom"), run_id=node)
    assert seen[-1].type == "node.finished" and seen[-1].data == {**seen[-1].data, "node": "executor", "ok": False}


def test_the_aggregator_end_carries_the_result_manifest_with_the_run_id():
    cb, seen = _events()
    node = _start_node(cb, "aggregator", _start_root(cb, trace_id="t-42"))
    summary = ResultSummary(node_id="sq_1", columns=["genre"], row_count=25)
    cb.on_chain_end({"aggregator_response": AggregatorResponse(results=[summary])}, run_id=node)

    assert [e.type for e in seen] == ["node.started", "node.finished", "result.manifest"]
    assert seen[-1].data == {"results": [summary.model_dump(mode="json")]}


def _tokens(cb, run, pieces):
    for piece in pieces:
        chunk = AIMessageChunk(content="", tool_call_chunks=[
            {"name": None, "args": piece, "id": None, "index": 0, "type": "tool_call_chunk"}])
        cb.on_llm_new_token("", chunk=ChatGenerationChunk(message=chunk), run_id=run)


def test_the_answer_models_tool_call_pieces_become_deltas_per_field():
    cb, seen = _events()
    run = uuid.uuid4()
    cb.on_chat_model_start({}, [[]], run_id=run, metadata={"langgraph_node": "answer_synthesizer"})
    _tokens(cb, run, ['{"summ', 'ary": "Rock ', 'leads', '.", "format_type": "te', 'xt", "content": "Ro',
                      'ck has 1,297', ' tracks."', ', "warnings": []}'])

    deltas = [e.data for e in seen if e.type == "answer.delta"]
    summary = [d["text"] for d in deltas if d["field"] == "summary"]
    content = [d["text"] for d in deltas if d["field"] == "content"]
    assert "".join(summary) == "Rock leads."
    assert "".join(content) == "Rock has 1,297 tracks."
    assert len(summary) > 1 and len(content) > 1
    assert {d["field"] for d in deltas} == {"summary", "content"}


def test_tokens_from_any_other_model_call_are_not_the_answer():
    cb, seen = _events()
    run = uuid.uuid4()
    cb.on_chat_model_start({}, [[]], run_id=run, metadata={"langgraph_node": "ast_planner"})
    _tokens(cb, run, ['{"summary": "not an answer"}'])
    assert seen == []


def test_an_escape_cut_in_half_is_held_until_the_rest_arrives():
    cb, seen = _events()
    run = uuid.uuid4()
    cb.on_chat_model_start({}, [[]], run_id=run, metadata={"langgraph_node": "answer_synthesizer"})
    _tokens(cb, run, ['{"summary": "say \\', '"hi\\" now"}'])
    assert "".join(e.data["text"] for e in seen if e.type == "answer.delta") == 'say "hi" now'


def test_a_sub_querys_steps_carry_its_id():
    cb, seen = _events()
    root = _start_root(cb)
    agent = uuid.uuid4()
    cb.on_chain_start({}, {"subgraph_id": "sql_agent:sq_2:t-1"}, run_id=agent, parent_run_id=root,
                      name="sql_agent", metadata={"langgraph_node": "sql_agent"})
    inner = uuid.uuid4()
    cb.on_chain_start({}, {}, run_id=inner, parent_run_id=agent, name="LangGraph")
    planner = _start_node(cb, "ast_planner", inner)
    cb.on_chain_end({}, run_id=planner)
    cb.on_chain_end({}, run_id=inner)
    cb.on_chain_end({}, run_id=agent)
    decomposer = _start_node(cb, "decomposer", root)
    cb.on_chain_end({}, run_id=decomposer)

    ids = [(e.type, e.data["node"], e.data["sub_query_id"]) for e in seen]
    assert ids == [("node.started", "sql_agent", "sq_2"), ("node.started", "ast_planner", "sq_2"),
                   ("node.finished", "ast_planner", "sq_2"), ("node.finished", "sql_agent", "sq_2"),
                   ("node.started", "decomposer", None), ("node.finished", "decomposer", None)]


def test_query_event_is_gone():
    import nl2sql
    import nl2sql.pipeline.events as run_events

    assert not hasattr(run_events, "QueryEvent")
    assert "QueryEvent" not in nl2sql.__all__
    with pytest.raises(AttributeError):
        nl2sql.QueryEvent


def test_a_failed_llm_call_clears_its_answer_even_when_langchain_passes_the_partial_response():
    # langchain-core 1.7 hands on_llm_error the partial result as response=;
    # a handler that aliases on_llm_end takes it twice and the error is swallowed.
    cb, _seen = _events()
    run = uuid.uuid4()
    cb._answers[run] = object()

    cb.on_llm_error(RuntimeError("boom"), run_id=run, response=None)

    assert run not in cb._answers
