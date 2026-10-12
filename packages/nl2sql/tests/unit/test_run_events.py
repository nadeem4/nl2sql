"""``RunEvents``: the LangChain callback that turns a run into stream events.

A node's own run (its name is its ``langgraph_node``) becomes ``node_started``
and ``node_finished``; the runs inside it do not. The aggregator's end carries
the run's result manifest. The answer model's tokens -- pieces of its forced
tool call's arguments -- become ``answer_delta`` events, one per field.
"""
import uuid

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from nl2sql import QueryEvent
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.pipeline.events import RunEvents
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse


def _events():
    seen = []
    return RunEvents(seen.append), seen


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

    assert [(e.type, e.data["node"]) for e in seen] == [("node_started", "decomposer"),
                                                         ("node_finished", "decomposer")]
    assert seen[1].data["ok"] is True and seen[1].data["seconds"] >= 0
    assert all(isinstance(e, QueryEvent) for e in seen)


def test_a_failed_node_finishes_not_ok():
    cb, seen = _events()
    node = _start_node(cb, "executor", _start_root(cb))
    cb.on_chain_error(RuntimeError("boom"), run_id=node)
    assert seen[-1].type == "node_finished" and seen[-1].data == {**seen[-1].data, "node": "executor", "ok": False}


def test_the_aggregator_end_carries_the_result_manifest_with_the_run_id():
    cb, seen = _events()
    node = _start_node(cb, "aggregator", _start_root(cb, trace_id="t-42"))
    summary = ResultSummary(node_id="sq_1", columns=["genre"], row_count=25)
    cb.on_chain_end({"aggregator_response": AggregatorResponse(results=[summary])}, run_id=node)

    assert [e.type for e in seen] == ["node_started", "node_finished", "result_manifest"]
    assert seen[-1].data == {"trace_id": "t-42", "results": [summary.model_dump(mode="json")]}


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

    deltas = [e.data for e in seen if e.type == "answer_delta"]
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
    assert "".join(e.data["text"] for e in seen if e.type == "answer_delta") == 'say "hi" now'


def test_an_event_is_one_server_sent_event():
    event = QueryEvent(type="node_started", data={"node": "decomposer"})
    assert event.sse() == 'event: node_started\ndata: {"node": "decomposer"}\n\n'
