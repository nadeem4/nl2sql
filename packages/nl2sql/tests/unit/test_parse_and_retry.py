"""Parse-and-retry: a structured answer that does not parse is asked for again.

``structured()`` and ``streaming_structured()`` send the model's answer back
with the validation errors, up to the agent's ``parse_retries`` times, and then
raise ``StructuredOutputError``, which ``provider_error()`` reports as
``MODEL_OUTPUT_INVALID``. See docs/architecture/phase2.md, section 5,
"Structured output, and parse-and-retry".
"""
import pytest
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ValidationError

from nl2sql.common.errors import ErrorCode
from nl2sql.config import LLM, AgentLLM
from nl2sql.llm import LLMRegistry
from nl2sql.llm.failures import provider_error
from nl2sql.llm.models import AgentConfig
from nl2sql.llm.registry import agents_from_config
from nl2sql.llm.wires import StructuredOutputError, streaming_structured, structured
from nl2sql.secrets import SecretManager
from nl2sql.services.callbacks.token_handler import TokenUsageCallback
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

PROMPT = ChatPromptTemplate.from_template("Which table holds {q}?")
GOOD = {"table": "orders"}
BAD = {"tabel": "orders"}
# The retry carries this text, so a rule matching it answers only the retry.
RETRY = "did not match the schema"


class Pick(BaseModel):
    table: str


def _llm(server, name="default", **fields):
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai_compatible", model="m", base_url=server.base_url,
                                      structured_output="tool_calling", name=name, **fields))
    return registry.get_llm(name)


def _ask(chain, callbacks=()):
    return chain.invoke({"q": "orders"}, config={"callbacks": list(callbacks),
                                                 "metadata": {"langgraph_node": "ast_planner"}})


def test_parse_retries_defaults_to_one_and_is_inherited():
    llm = LLM(provider="openai", model="m", parse_retries=3, agents={"refiner": AgentLLM(parse_retries=0),
                                                                   "ast_planner": AgentLLM(model="x")})
    agents = agents_from_config(llm)
    assert (agents["default"].parse_retries, agents["astplanner"].parse_retries,
            agents["refiner"].parse_retries) == (3, 3, 0)
    assert LLM(provider="openai", model="m").parse_retries == 1
    with pytest.raises(ValidationError):
        LLM(provider="openai", model="m", parse_retries=-1)


def test_malformed_answer_is_retried_with_the_error():
    server = FakeLLMServer([Rule("Pick", GOOD, when=RETRY), Rule("Pick", BAD)]).start()
    try:
        answer = _ask(PROMPT | structured(_llm(server), Pick))
    finally:
        server.stop()

    assert answer == Pick(table="orders")
    first, second = (call["body"]["messages"] for call in server.calls)
    assert second[:len(first)] == first
    retry = "\n".join(str(m.get("content")) for m in second[len(first):])
    assert '"tabel": "orders"' in retry  # the model's own answer
    assert "`table`: Field required" in retry  # what was wrong with it
    assert "Answer again with only Pick" in retry


def test_an_answer_with_no_tool_call_is_retried():
    # A tool-call parser returns None, not an error, when the model wrote text instead.
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    seen = []

    def model(messages):
        seen.append(messages)
        return AIMessage(content="It is the orders table.")

    class Chat:
        def with_structured_output(self, schema, method):
            return RunnableLambda(model) | RunnableLambda(lambda _m: Pick(table="orders") if len(seen) > 1 else None)

    assert _ask(PROMPT | structured(Chat(), Pick)) == Pick(table="orders")
    retry = seen[1].to_messages() if hasattr(seen[1], "to_messages") else seen[1]
    assert "It is the orders table." in retry[-2].content
    assert "no structured answer" in retry[-1].content


def test_retries_stop_at_parse_retries():
    for retries in (0, 2):
        server = FakeLLMServer([Rule("Pick", BAD)]).start()
        try:
            with pytest.raises(StructuredOutputError) as raised:
                _ask(PROMPT | structured(_llm(server, parse_retries=retries), Pick))
        finally:
            server.stop()
        assert len(server.calls) == retries + 1
        assert raised.value.attempts == retries + 1
        assert raised.value.schema == "Pick"


def test_final_failure_names_the_agent_and_suggests_a_method():
    server = FakeLLMServer([Rule("Pick", BAD)]).start()
    try:
        with pytest.raises(StructuredOutputError) as raised:
            _ask(PROMPT | structured(_llm(server, parse_retries=0), Pick))
    finally:
        server.stop()
    assert raised.value.agent == "ast_planner"

    error = provider_error("ast_planner", raised.value)

    assert error.error_code == ErrorCode.MODEL_OUTPUT_INVALID
    assert "ast_planner" in error.message and "Pick" in error.message
    assert "structured_output" in error.message and "stronger model" in error.message


def test_the_agent_is_the_clients_when_no_node_runs_it():
    server = FakeLLMServer([Rule("Pick", BAD)]).start()
    try:
        with pytest.raises(StructuredOutputError) as raised:
            (PROMPT | structured(_llm(server, name="refiner", parse_retries=0), Pick)).invoke({"q": "orders"})
    finally:
        server.stop()
    assert raised.value.agent == "refiner"


def test_streamed_answer_is_retried_without_streaming():
    server = FakeLLMServer([Rule("Pick", GOOD, when=RETRY), Rule("Pick", BAD)]).start()
    try:
        answer = _ask(PROMPT | streaming_structured(_llm(server), Pick))
    finally:
        server.stop()

    assert answer == Pick(table="orders")
    assert [bool(call["body"].get("stream")) for call in server.calls] == [True, False]


def test_retries_are_counted_in_usage():
    server = FakeLLMServer([Rule("Pick", GOOD, when=RETRY), Rule("Pick", BAD)]).start()
    usage = TokenUsageCallback()
    try:
        _ask(PROMPT | structured(_llm(server), Pick), callbacks=[usage])
    finally:
        server.stop()

    totals = usage.usage()
    assert totals.total.calls == 2
    assert totals.nodes["ast_planner"].calls == 2
    assert totals.total.total_tokens == 4


def test_a_good_first_answer_makes_one_call():
    server = FakeLLMServer([Rule("Pick", GOOD)]).start()
    try:
        assert _ask(PROMPT | structured(_llm(server), Pick)) == Pick(table="orders")
    finally:
        server.stop()
    assert len(server.calls) == 1
