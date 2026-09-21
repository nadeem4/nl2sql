"""What the engine sends as ``temperature``, and what it says when a model refuses it.

On 2026-09-20 the owner's OpenAI account accepted ``temperature=0`` for gpt-5.4
and rejected it for gpt-5.5 and gpt-5-mini with HTTP 400 ("Only the default (1)
value is supported"). The config therefore decides, per agent, whether the
parameter is sent at all: ``0.0`` by default, nothing when it is ``null``.

These tests read the request bodies the fake OpenAI server received, so they
check the wire, not the client's attributes.
"""
from __future__ import annotations

import asyncio

import pytest
import yaml
from pydantic import BaseModel

from nl2sql.configs import LLMFileConfig
from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.secrets import SecretManager
from nl2sql.testing.fake_llm import TEMPERATURE_REJECTED, FakeLLMServer, Rule

# Built at run time: the fake server never checks it, and a literal key-shaped
# string would trip secret scanners.
FAKE_KEY = "-".join(["fake", "key", "for", "tests"])


class Answer(BaseModel):
    text: str


@pytest.fixture
def server():
    srv = FakeLLMServer([Rule("plain", "hi"), Rule("Answer", {"text": "ok"})]).start()
    yield srv
    srv.stop()


def _registry(*agents: AgentConfig) -> LLMRegistry:
    registry = LLMRegistry(SecretManager())
    for agent in agents:
        registry.register_llm(agent)
    return registry


def _agent(server, name="default", **overrides) -> AgentConfig:
    fields = {"provider": "openai", "model": "gpt-5.4", "api_key": FAKE_KEY,
              "base_url": server.base_url, "name": name}
    fields.update(overrides)
    return AgentConfig(**fields)


def _last_body(server) -> dict:
    return server.calls[-1]["body"]


def test_temperature_defaults_to_zero():
    assert AgentConfig(provider="openai", model="gpt-5.4").temperature == 0.0


def test_temperature_null_in_yaml_loads_as_none():
    data = yaml.safe_load(
        "version: 1\ndefault:\n  provider: openai\n  model: gpt-5.5\n  temperature: null\n"
    )
    assert LLMFileConfig.model_validate(data).default.temperature is None


def test_default_temperature_zero_is_sent_even_for_a_gpt5_model(server):
    """langchain-openai drops a non-1 temperature for any ``gpt-5*`` name on its own.

    gpt-5.4 accepts 0, and the determinism work relies on it, so the engine
    sends what the config says rather than what the model name suggests.
    """
    llm = _registry(_agent(server)).get_llm("default")
    llm.invoke("hello")

    body = _last_body(server)
    assert body["temperature"] == 0.0
    assert body["seed"] == 42
    assert body["model"] == "gpt-5.4"


def test_explicit_temperature_is_sent(server):
    llm = _registry(_agent(server, model="gpt-4.1", temperature=0.2)).get_llm("default")
    llm.invoke("hello")
    assert _last_body(server)["temperature"] == 0.2


def test_null_temperature_is_not_sent_at_all(server):
    llm = _registry(_agent(server, model="gpt-5.5", temperature=None)).get_llm("default")
    llm.invoke("hello")

    body = _last_body(server)
    assert "temperature" not in body
    assert body["seed"] == 42  # the rejecting models accept seed; only temperature goes


def test_null_temperature_is_not_sent_through_structured_output(server):
    """The pipeline nodes all call the model through ``with_structured_output``."""
    llm = _registry(_agent(server, model="gpt-5.5", temperature=None)).get_llm("default")
    assert llm.with_structured_output(Answer).invoke("hello") == Answer(text="ok")
    assert "temperature" not in _last_body(server)


def test_temperature_is_configured_per_agent(server):
    registry = LLMRegistry(SecretManager())
    registry.register_llms({
        "default": _agent(server),
        # No ``name`` given: the key under ``agents`` is the agent's name.
        "refiner": AgentConfig(provider="openai", model="gpt-5.5", temperature=None,
                               api_key=FAKE_KEY, base_url=server.base_url),
    })

    registry.get_llm("refiner").invoke("hello")
    refiner_body = _last_body(server)
    registry.get_llm("decomposer").invoke("hello")  # not configured: falls back to default
    decomposer_body = _last_body(server)

    assert refiner_body["model"] == "gpt-5.5" and "temperature" not in refiner_body
    assert decomposer_body["model"] == "gpt-5.4" and decomposer_body["temperature"] == 0.0


def test_an_agent_without_a_name_is_registered_under_its_key(server):
    """Before this, every entry under ``agents`` without ``name:`` registered as
    'default' and was then overwritten by the real default, so per-node models
    were silently ignored."""
    registry = LLMRegistry(SecretManager())
    registry.register_llms({
        "astplanner": AgentConfig(provider="openai", model="gpt-5.4-mini", api_key=FAKE_KEY,
                                  base_url=server.base_url),
        "default": _agent(server),
    })

    assert registry.get_llm("astplanner").model_name == "gpt-5.4-mini"
    assert registry.get_llm("default").model_name == "gpt-5.4"
    assert registry.get_llm_config("astplanner")["name"] == "astplanner"


def test_llm_config_reports_a_null_temperature(server):
    registry = _registry(_agent(server, model="gpt-5.5", temperature=None))
    assert registry.get_llm_config("default")["temperature"] is None


def test_fake_server_rejects_temperature_like_openai_does():
    srv = FakeLLMServer([Rule("plain", "hi")], reject_temperature=True).start()
    try:
        registry = _registry(AgentConfig(provider="openai", model="gpt-5.5", api_key=FAKE_KEY,
                                         base_url=srv.base_url, temperature=None))
        assert registry.get_llm("default").invoke("hello").content == "hi"
    finally:
        srv.stop()
    assert "does not support 0.0 with this model" in TEMPERATURE_REJECTED


def test_a_rejected_temperature_raises_an_actionable_error():
    srv = FakeLLMServer([Rule("Answer", {"text": "ok"})], reject_temperature=True).start()
    try:
        registry = LLMRegistry(SecretManager())
        registry.register_llms({
            "default": AgentConfig(provider="openai", model="gpt-5.5", api_key=FAKE_KEY,
                                   base_url=srv.base_url),
        })
        llm = registry.get_llm("refiner")
        with pytest.raises(ValueError) as excinfo:
            llm.with_structured_output(Answer).invoke("hello")
    finally:
        srv.stop()

    message = str(excinfo.value)
    assert "'gpt-5.5'" in message
    assert "temperature: null" in message
    assert "'default'" in message  # the agent whose config needs the change
    assert "Only the default (1) value is supported" in message  # the provider's own words


def test_a_rejected_temperature_raises_an_actionable_error_async():
    srv = FakeLLMServer([Rule("plain", "hi")], reject_temperature=True).start()
    try:
        llm = _registry(AgentConfig(provider="openai", model="gpt-5-mini", api_key=FAKE_KEY,
                                    base_url=srv.base_url)).get_llm("default")
        with pytest.raises(ValueError, match="temperature: null"):
            asyncio.run(llm.ainvoke("hello"))
    finally:
        srv.stop()


def test_other_bad_requests_are_not_rewritten():
    srv = FakeLLMServer([]).start()  # no rule: every call is a 400 about something else
    try:
        llm = _registry(AgentConfig(provider="openai", model="gpt-5.4", api_key=FAKE_KEY,
                                    base_url=srv.base_url)).get_llm("default")
        with pytest.raises(Exception) as excinfo:
            llm.invoke("hello")
    finally:
        srv.stop()
    assert "temperature: null" not in str(excinfo.value)
