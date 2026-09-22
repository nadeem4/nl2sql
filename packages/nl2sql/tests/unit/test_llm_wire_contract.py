"""One contract, run against every wire type, on the wire.

Each test is parametrized over the wire adapters in ``nl2sql.llm.wires.WIRES``
and talks to ``FakeLLMServer``, which answers ``/v1/chat/completions`` (the
openai wire) and ``/v1/messages`` (the anthropic wire). What is asserted is
the request each client actually sent and what the engine read back. No key,
no network. A new wire type is covered by adding one ``WireCase``.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.llm.registry import PROVIDER_PRESETS
from nl2sql.llm.wires import WIRES, structured, wire_of
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.datasource_resolver.schemas import AnswerabilityResponse
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse
from nl2sql.secrets import SecretManager
from nl2sql.services.callbacks.token_handler import TokenUsageCallback
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

pytest.importorskip("langchain_anthropic")


def _load_recordings():
    path = Path(__file__).resolve().parents[1] / "e2e" / "recordings_chinook.py"
    spec = importlib.util.spec_from_file_location("_contract_recordings_chinook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REC = _load_recordings()

# Built at run time: a literal key-shaped string would trip secret scanners.
FAKE_KEY = "-".join(["fake", "contract", "key", "0000"])

SYSTEM = "You turn questions into plans. " * 20
PROMPT = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", "{question}")])

# The five LLM calls the pipeline makes, as (schema, recorded answer). The
# refiner's is free text, so its schema is None.
CALLS = {
    "AnswerabilityResponse": (AnswerabilityResponse, REC.ANSWERABLE.payload),
    "DecomposerResponse": (DecomposerResponse, REC.COUNT_CUSTOMERS_DECOMPOSER),
    "PlanModel": (PlanModel, REC.COUNT_CUSTOMERS_PLAN),
    "refiner": (None, "Keep the same plan."),
    "AggregatedResponse": (AggregatedResponse, REC.count_customers_answer("")),
}


@dataclass
class WireCase:
    wire: str
    provider: str
    model: str
    base_url: Callable[[FakeLLMServer], str]
    sent_key: Callable[[Dict[str, Any]], Optional[str]]
    # A model on this wire that accepts temperature=0.
    temperature_model: str
    # The provider's usage object for a call that read 3,500 prompt tokens from
    # the cache, and the engine fields it must become.
    cache_read_usage: Dict[str, Any]
    # The same for a call that wrote them to the cache; None if the wire's
    # provider never reports cache writes.
    cache_write_usage: Optional[Dict[str, Any]]
    marks_system_block: bool


CASES = [
    WireCase(
        wire="openai", provider="openai", model="gpt-5.4",
        base_url=lambda s: s.base_url,
        sent_key=lambda call: (call.get("authorization") or "").removeprefix("Bearer "),
        temperature_model="gpt-5.4",
        cache_read_usage={"prompt_tokens": 3540, "completion_tokens": 120, "total_tokens": 3660,
                          "prompt_tokens_details": {"cached_tokens": 3500}},
        cache_write_usage=None,
        marks_system_block=False,
    ),
    WireCase(
        wire="anthropic", provider="anthropic", model="claude-opus-5",
        base_url=lambda s: s.anthropic_base_url,
        sent_key=lambda call: call.get("api_key"),
        temperature_model="claude-haiku-4-5",
        cache_read_usage={"input_tokens": 40, "cache_read_input_tokens": 3500, "cache_creation_input_tokens": 0,
                          "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
                          "output_tokens": 120},
        cache_write_usage={"input_tokens": 40, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 3500,
                           "cache_creation": {"ephemeral_5m_input_tokens": 3500, "ephemeral_1h_input_tokens": 0},
                           "output_tokens": 120},
        marks_system_block=True,
    ),
]


def test_every_wire_type_has_a_contract_case():
    assert {case.wire for case in CASES} == set(WIRES)
    assert {preset.wire for preset in PROVIDER_PRESETS.values()} <= set(WIRES)


@pytest.fixture(params=CASES, ids=lambda case: case.wire)
def case(request) -> WireCase:
    return request.param


def _rules(usage=None):
    return [Rule("plain" if name == "refiner" else name, answer, usage=usage)
            for name, (_schema, answer) in CALLS.items()]


@pytest.fixture
def server():
    srv = FakeLLMServer(_rules()).start()
    yield srv
    srv.stop()


def _llm(case: WireCase, srv: FakeLLMServer, **overrides):
    fields = {"provider": case.provider, "model": case.model, "temperature": None,
              "api_key": FAKE_KEY, "base_url": case.base_url(srv), "name": "default"}
    fields.update(overrides)
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(**fields))
    return registry.get_llm(fields["name"])


def _ask(llm, name="PlanModel", callbacks=None):
    schema, _answer = CALLS[name]
    tail = llm | StrOutputParser() if schema is None else structured(llm, schema)
    return (PROMPT | tail).invoke({"question": "How many customers?"}, config={"callbacks": callbacks or []})


def _expected(name):
    schema, answer = CALLS[name]
    return answer if schema is None else schema.model_validate(answer)


# --- the client --------------------------------------------------------------------------


def test_the_registry_builds_the_wire_adapters_client(case, server):
    llm = _llm(case, server)

    assert wire_of(llm) is WIRES[case.wire]
    assert server.calls == []  # built lazily, nothing sent yet


# --- structured output -------------------------------------------------------------------


@pytest.mark.parametrize("name", list(CALLS))
def test_each_pipeline_call_round_trips(case, server, name):
    assert _ask(_llm(case, server), name) == _expected(name)

    call = server.calls[-1]
    assert call["name"] == ("plain" if name == "refiner" else name)
    if case.wire == "anthropic":
        assert call["mode"] == "anthropic"
    else:
        assert call["mode"] == ("plain" if name == "refiner" else "tools")


def test_structured_output_is_the_wires_method(case, server):
    _ask(_llm(case, server), "PlanModel")

    body = server.calls[-1]["body"]
    assert WIRES[case.wire].structured_output_method == "function_calling"
    if case.wire == "anthropic":
        # Forced tool use: PlanModel is recursive, which native JSON outputs reject.
        assert body["tool_choice"] == {"type": "tool", "name": "PlanModel"}
        assert "output_config" not in body
    else:
        assert body["tool_choice"]["function"]["name"] == "PlanModel"
        assert "response_format" not in body


# --- cache marking -----------------------------------------------------------------------


def test_the_system_prompt_is_cache_marked_only_where_the_wire_needs_it(case, server):
    _ask(_llm(case, server))

    body = server.calls[-1]["body"]
    if case.marks_system_block:
        assert body["system"] == [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
        assert "cache_control" not in str(body["messages"])
    else:
        # OpenAI caches long prefixes by itself: nothing is marked.
        assert body["messages"][0] == {"role": "system", "content": SYSTEM}
        assert "cache_control" not in str(body)


def test_a_prompt_without_a_system_message_is_sent_unmarked(case, server):
    llm = _llm(case, server)
    (ChatPromptTemplate.from_template("{question}") | structured(llm, AggregatedResponse)).invoke(
        {"question": "How many customers?"})

    body = server.calls[-1]["body"]
    assert "cache_control" not in str(body)
    assert "system" not in body


# --- usage -------------------------------------------------------------------------------


def _usage_of(case, server_usage):
    srv = FakeLLMServer(_rules(server_usage)).start()
    try:
        callback = TokenUsageCallback()
        _ask(_llm(case, srv), callbacks=[callback])
    finally:
        srv.stop()
    [call] = callback.usage().calls
    return call


def test_a_cache_read_is_normalised_and_counted_once(case):
    call = _usage_of(case, case.cache_read_usage)

    assert call.usage_reported is True
    assert (call.input_tokens, call.cached_input_tokens, call.cache_write_input_tokens) == (3540, 3500, 0)
    assert (call.output_tokens, call.total_tokens) == (120, 3660)


def test_a_cache_write_is_normalised_and_counted_once(case):
    if case.cache_write_usage is None:
        pytest.skip(f"the {case.wire} wire's provider reports no cache writes")
    call = _usage_of(case, case.cache_write_usage)

    assert (call.input_tokens, call.cached_input_tokens, call.cache_write_input_tokens) == (3540, 0, 3500)
    assert call.total_tokens == 3660


# --- temperature -------------------------------------------------------------------------


def test_no_temperature_is_sent_when_the_config_says_null(case, server):
    _ask(_llm(case, server, temperature=None))

    assert "temperature" not in server.calls[-1]["body"]


def test_a_configured_temperature_is_sent(case, server):
    _ask(_llm(case, server, model=case.temperature_model, temperature=0.0))

    assert server.calls[-1]["body"]["temperature"] == 0.0


def test_a_rejected_temperature_tells_the_user_what_to_set(case):
    srv = FakeLLMServer(_rules(), reject_temperature=True).start()
    try:
        llm = _llm(case, srv, temperature=0.0, name="astplanner")
        with pytest.raises(ValueError) as info:
            _ask(llm)
    finally:
        srv.stop()

    message = str(info.value)
    assert f"Model '{case.model}'" in message
    assert "astplanner" in message
    assert "temperature: null" in message


# --- the key -----------------------------------------------------------------------------


def test_the_configured_key_is_the_one_sent(case, server):
    _ask(_llm(case, server))

    assert case.sent_key(server.calls[-1]) == FAKE_KEY


def test_the_key_falls_back_to_the_providers_variable(case, server, monkeypatch):
    monkeypatch.setenv(PROVIDER_PRESETS[case.provider].api_key_env, FAKE_KEY)

    _ask(_llm(case, server, api_key=None))

    assert case.sent_key(server.calls[-1]) == FAKE_KEY


def test_a_missing_key_names_the_providers_variable(case, server, monkeypatch):
    variable = PROVIDER_PRESETS[case.provider].api_key_env
    monkeypatch.delenv(variable, raising=False)

    with pytest.raises(ValueError, match=variable):
        _llm(case, server, api_key=None)


# --- wire-specific -----------------------------------------------------------------------


def test_claude_calls_are_bounded_for_non_streaming(server):
    _ask(_llm(CASES[1], server))

    assert server.calls[-1]["body"]["max_tokens"] == 16000


def test_a_missing_anthropic_extra_names_the_extra_to_install(server, monkeypatch):
    # None in sys.modules makes the import raise ImportError, as if the
    # package were not installed.
    monkeypatch.setitem(sys.modules, "langchain_anthropic", None)
    monkeypatch.delitem(sys.modules, "nl2sql.llm.wires.anthropic_client", raising=False)

    with pytest.raises(ValueError, match=r"nl2sql-engine\[anthropic\]"):
        _llm(CASES[1], server)
