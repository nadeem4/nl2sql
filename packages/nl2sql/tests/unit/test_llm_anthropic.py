"""Claude through Anthropic's own Messages API, checked on the wire.

Every test here talks to ``FakeLLMServer``'s Anthropic ``/v1/messages`` mode, so
what is asserted is the request body ``ChatAnthropic`` actually sent (the
``system`` blocks, ``cache_control``, ``tool_choice``, ``temperature``) and the
usage it read back. No key, no network.
"""
from __future__ import annotations

import sys

import pytest
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.secrets import SecretManager
from nl2sql.services.callbacks.token_handler import TokenUsageCallback
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

langchain_anthropic = pytest.importorskip("langchain_anthropic")

# Built at run time: a literal key-shaped string would trip secret scanners.
FAKE_KEY = "-".join(["sk", "ant", "api03", "not", "a", "real", "key", "0000"])

SYSTEM = "You turn questions into plans. " * 20
PROMPT = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", "{question}")])


class Answer(BaseModel):
    text: str


@pytest.fixture
def server():
    srv = FakeLLMServer([Rule("Answer", {"text": "ok"})]).start()
    yield srv
    srv.stop()


def _agent(server, **overrides) -> AgentConfig:
    fields = {"provider": "anthropic", "model": "claude-opus-5", "temperature": None,
              "api_key": FAKE_KEY, "base_url": server.anthropic_base_url, "name": "default"}
    fields.update(overrides)
    return AgentConfig(**fields)


def _llm(agent: AgentConfig):
    registry = LLMRegistry(SecretManager())
    registry.register_llm(agent)
    return registry.get_llm(agent.name)


def _ask(llm, callbacks=None):
    chain = PROMPT | llm.with_structured_output(Answer)
    return chain.invoke({"question": "How many customers?"}, config={"callbacks": callbacks or []})


def test_the_anthropic_provider_builds_a_native_chat_anthropic(server):
    llm = _llm(_agent(server))

    assert isinstance(llm, langchain_anthropic.ChatAnthropic)
    assert llm.model == "claude-opus-5"


def test_the_configured_key_is_the_one_sent(server):
    assert _ask(_llm(_agent(server))) == Answer(text="ok")

    assert server.calls[-1]["api_key"] == FAKE_KEY


def test_the_key_falls_back_to_anthropic_api_key(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    _ask(_llm(_agent(server, api_key=None)))

    assert server.calls[-1]["api_key"] == FAKE_KEY


def test_a_missing_key_names_anthropic_api_key(server, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        _llm(_agent(server, api_key=None))


def test_the_system_message_carries_an_ephemeral_cache_breakpoint(server):
    _ask(_llm(_agent(server)))

    body = server.calls[-1]["body"]
    assert body["system"] == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]
    # The question varies per call, so it stays after the breakpoint, unmarked.
    assert "cache_control" not in str(body["messages"])


def test_a_prompt_without_a_system_message_gets_no_breakpoint(server):
    llm = _llm(_agent(server))
    chain = ChatPromptTemplate.from_template("{question}") | llm.with_structured_output(Answer)

    chain.invoke({"question": "How many customers?"})

    body = server.calls[-1]["body"]
    assert "system" not in body
    assert "cache_control" not in str(body)


def test_structured_output_is_a_forced_tool_call(server):
    # Tool use, not output_config.format: the planner's PlanModel is recursive,
    # which Anthropic's native structured outputs do not accept.
    _ask(_llm(_agent(server)))

    body = server.calls[-1]["body"]
    assert [tool["name"] for tool in body["tools"]] == ["Answer"]
    assert body["tool_choice"] == {"type": "tool", "name": "Answer"}
    assert "output_config" not in body


def test_no_temperature_is_sent_when_the_config_says_null(server):
    _ask(_llm(_agent(server, temperature=None)))

    assert "temperature" not in server.calls[-1]["body"]


def test_a_configured_temperature_is_sent(server):
    _ask(_llm(_agent(server, model="claude-haiku-4-5", temperature=0.0)))

    assert server.calls[-1]["body"]["temperature"] == 0.0


def test_max_tokens_is_bounded_for_a_non_streaming_call(server):
    _ask(_llm(_agent(server)))

    assert server.calls[-1]["body"]["max_tokens"] == 16000


def test_a_rejected_temperature_tells_the_user_what_to_set():
    srv = FakeLLMServer([Rule("Answer", {"text": "ok"})], reject_temperature=True).start()
    try:
        llm = _llm(_agent(srv, temperature=0.0, name="astplanner"))
        with pytest.raises(ValueError) as info:
            _ask(llm)
    finally:
        srv.stop()

    message = str(info.value)
    assert "claude-opus-5" in message
    assert "astplanner" in message
    assert "temperature: null" in message


def _usage_of(server_usage):
    srv = FakeLLMServer([Rule("Answer", {"text": "ok"}, usage=server_usage)]).start()
    try:
        callback = TokenUsageCallback()
        _ask(_llm(_agent(srv)), callbacks=[callback])
    finally:
        srv.stop()
    [call] = callback.usage().calls
    return call


def test_a_cache_read_is_reported_as_cached_input_and_counted_once():
    call = _usage_of({
        "input_tokens": 40,
        "cache_read_input_tokens": 3500,
        "cache_creation_input_tokens": 0,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
        "output_tokens": 120,
    })

    # Anthropic's input_tokens excludes the cached prefix; the total includes it once.
    assert call.input_tokens == 3540
    assert call.cached_input_tokens == 3500
    assert call.cache_write_input_tokens == 0
    assert call.output_tokens == 120
    assert call.total_tokens == 3660
    assert call.usage_reported is True


def test_a_cache_write_is_reported_as_cache_write_input_and_counted_once():
    call = _usage_of({
        "input_tokens": 40,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 3500,
        "cache_creation": {"ephemeral_5m_input_tokens": 3500, "ephemeral_1h_input_tokens": 0},
        "output_tokens": 120,
    })

    assert call.input_tokens == 3540
    assert call.cached_input_tokens == 0
    assert call.cache_write_input_tokens == 3500
    assert call.total_tokens == 3660


def test_the_openai_provider_sends_no_cache_control():
    srv = FakeLLMServer([Rule("Answer", {"text": "ok"})]).start()
    try:
        llm = _llm(AgentConfig(provider="openai", model="gpt-5.4", api_key="-".join(["fake", "key"]),
                               base_url=srv.base_url, name="default"))
        _ask(llm)
    finally:
        srv.stop()

    body = srv.calls[-1]["body"]
    assert body["messages"][0] == {"role": "system", "content": SYSTEM}
    assert "cache_control" not in str(body)


def test_a_missing_extra_names_the_extra_to_install(server, monkeypatch):
    # None in sys.modules makes the import raise ImportError, as if the
    # package were not installed.
    monkeypatch.setitem(sys.modules, "langchain_anthropic", None)
    monkeypatch.delitem(sys.modules, "nl2sql.llm.claude", raising=False)

    with pytest.raises(ValueError, match=r"nl2sql-engine\[anthropic\]"):
        _llm(_agent(server))
