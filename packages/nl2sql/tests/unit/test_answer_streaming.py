"""The answer model, streamed: its words reach a stream as the model writes them.

``streaming_structured`` is ``structured`` with the provider asked to stream.
On both wires the answer is a forced tool call, so what streams is the tool
call's arguments; :class:`RunEvents` reads ``summary`` and ``content`` out of
them as they grow. The answer that comes back, and the usage the call
reports, are the non-streaming call's. ``FakeLLMServer`` streams both wires,
which is what lets replay mode stream a recorded answer.
"""
import uuid

import pytest
from langchain_core.prompts import ChatPromptTemplate

from nl2sql.llm.wires import streaming_structured, structured
from nl2sql.pipeline.events import RunEvents
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse
from nl2sql.services.callbacks.token_handler import TokenUsageCallback
from nl2sql.testing.fake_llm import FakeLLMServer, Rule
from nl2sql.testing.runtime import fake_runtime

ANSWER = {"summary": "Rock sells the most tracks, with 1,297 of them.", "format_type": "text",
          "content": "Rock leads with 1,297 tracks, ahead of Latin with 579 and Metal with 374.",
          "warnings": []}
PROMPT = ChatPromptTemplate.from_template("User Query: {q}")
ANSWER_NODE = {"langgraph_node": "answer_synthesizer"}


def _openai(server):
    from nl2sql.llm.wires.openai import build_chat_client

    return build_chat_client("gpt-4o", 0.0, api_key="sk-fake", base_url=server.base_url,
                             tags=["answersynthesizer"])


def _claude(server):
    pytest.importorskip("langchain_anthropic")
    from nl2sql.llm.wires.anthropic_client import build_claude_client

    return build_claude_client("claude-opus-5", None, api_key="sk-ant-fake",
                               base_url=server.anthropic_base_url, tags=["answersynthesizer"])


def _ask(chain):
    seen, usage = [], TokenUsageCallback()
    run_events = RunEvents(lambda name, data: seen.append((name, data)))
    answer = chain.invoke({"q": "Which genre sells the most tracks?"},
                          config={"callbacks": [run_events, usage], "metadata": ANSWER_NODE})
    return answer, [data for name, data in seen if name == "answer.delta"], usage.usage()


@pytest.mark.parametrize("client", [_openai, _claude], ids=["openai", "anthropic"])
def test_the_answer_streams_word_by_word_and_ends_as_the_non_streaming_answer(client):
    server = FakeLLMServer([Rule("AggregatedResponse", ANSWER)]).start()
    try:
        llm = client(server)
        streamed, deltas, streamed_usage = _ask(PROMPT | streaming_structured(llm, AggregatedResponse))
        plain, plain_deltas, plain_usage = _ask(PROMPT | structured(llm, AggregatedResponse))
    finally:
        server.stop()

    assert streamed == plain == AggregatedResponse(**ANSWER)
    assert [bool(c["body"].get("stream")) for c in server.calls] == [True, False]
    summary = [d["text"] for d in deltas if d["field"] == "summary"]
    content = [d["text"] for d in deltas if d["field"] == "content"]
    assert len(summary) > 2 and "".join(summary) == ANSWER["summary"]
    assert len(content) > 2 and "".join(content) == ANSWER["content"]
    # A non-streaming call writes nothing as it goes.
    assert plain_deltas == []
    # The streamed call reports the same tokens as the non-streaming one.
    assert streamed_usage.total.input_tokens == plain_usage.total.input_tokens > 0
    assert streamed_usage.total.output_tokens == plain_usage.total.output_tokens > 0


def test_a_streamed_openai_call_asks_for_its_usage():
    server = FakeLLMServer([Rule("AggregatedResponse", ANSWER)]).start()
    try:
        _ask(PROMPT | streaming_structured(_openai(server), AggregatedResponse))
    finally:
        server.stop()
    assert server.calls[0]["body"]["stream_options"] == {"include_usage": True}


def test_a_streamed_call_refused_for_its_temperature_says_what_to_set():
    server = FakeLLMServer([Rule("AggregatedResponse", ANSWER)], reject_temperature=True).start()
    try:
        with pytest.raises(ValueError, match="temperature: null"):
            _ask(PROMPT | streaming_structured(_openai(server), AggregatedResponse))
    finally:
        server.stop()


def test_a_nested_claude_answer_is_unwrapped_when_streamed_too():
    # Claude now and then nests a tool call's arguments under a key of its own.
    server = FakeLLMServer([Rule("AggregatedResponse", {"input": ANSWER})]).start()
    try:
        answer, _, _ = _ask(PROMPT | streaming_structured(_claude(server), AggregatedResponse))
    finally:
        server.stop()
    assert answer == AggregatedResponse(**ANSWER)


def test_the_answer_synthesizer_streams_only_when_the_run_asks_it_to():
    from types import SimpleNamespace

    from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
    from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode
    from nl2sql.pipeline.state import GraphState

    server = FakeLLMServer([Rule("AggregatedResponse", ANSWER)]).start()
    try:
        llm = _openai(server)
        ctx = fake_runtime(llm_registry=SimpleNamespace(get_llm=lambda name: llm))
        node = AnswerSynthesizerNode(ctx)
        state = GraphState(user_query="q", aggregator_response=AggregatorResponse(
            terminal_results={"sq_1": [{"genre": "Rock", "tracks": 1297}]}))
        quiet = node(state)
        loud = node(state, {"configurable": {"stream_answer": True}})
    finally:
        server.stop()

    assert [bool(c["body"].get("stream")) for c in server.calls] == [False, True]
    assert quiet["answer_synthesizer_response"] == loud["answer_synthesizer_response"]
    assert loud["answer_synthesizer_response"].final_answer["summary"] == ANSWER["summary"]


def test_the_fake_server_paces_a_stream_when_asked(monkeypatch):
    import time

    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    server = FakeLLMServer([Rule("AggregatedResponse", ANSWER)], stream_delay=0.01).start()
    try:
        _ask(PROMPT | streaming_structured(_openai(server), AggregatedResponse))
    finally:
        server.stop()
    assert slept and set(slept) == {0.01}


def test_run_ids_of_other_nodes_are_left_alone():
    seen = []
    cb = RunEvents(seen.append)
    cb.on_llm_new_token("x", run_id=uuid.uuid4())
    assert seen == []
