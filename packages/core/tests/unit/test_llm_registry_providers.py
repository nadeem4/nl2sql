from __future__ import annotations

import pytest

from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.secrets import SecretManager


def _registry() -> LLMRegistry:
    return LLMRegistry(SecretManager())


def _base_url(client) -> str:
    """The base URL the constructed OpenAI client will actually talk to."""
    return str(client.client._client.base_url)


def test_openrouter_agent_points_at_the_openrouter_gateway():
    registry = _registry()
    registry.register_llm(
        AgentConfig(
            provider="openrouter",
            model="anthropic/claude-sonnet-4.5",
            api_key="sk-or-test",
            name="default",
        )
    )

    llm = registry.get_llm("default")
    assert _base_url(llm).startswith("https://openrouter.ai/api/v1")
    assert llm.model_name == "anthropic/claude-sonnet-4.5"


def test_configured_base_url_overrides_the_openrouter_default():
    registry = _registry()
    registry.register_llm(
        AgentConfig(
            provider="openrouter",
            model="local/model",
            api_key="sk-test",
            base_url="http://localhost:8000/v1",
            name="default",
        )
    )

    assert _base_url(registry.get_llm("default")).startswith("http://localhost:8000/v1")


def test_openai_agent_keeps_the_stock_openai_endpoint():
    registry = _registry()
    registry.register_llm(
        AgentConfig(provider="openai", model="gpt-4o", api_key="sk-test", name="default")
    )

    assert "openrouter" not in _base_url(registry.get_llm("default"))
    assert "api.openai.com" in _base_url(registry.get_llm("default"))


def test_ollama_agent_needs_no_key_and_points_at_the_local_daemon():
    registry = _registry()
    registry.register_llm(
        AgentConfig(provider="ollama", model="llama3", name="default")
    )

    llm = registry.get_llm("default")
    assert _base_url(llm).startswith("http://localhost:11434/v1")
    assert llm.model_name == "llama3"
    # The pipeline reaches every model through structured output.
    assert hasattr(llm, "with_structured_output")


def test_configured_base_url_overrides_the_ollama_default():
    registry = _registry()
    registry.register_llm(
        AgentConfig(
            provider="ollama",
            model="llama3",
            base_url="http://remote-box:11434/v1",
            name="default",
        )
    )

    assert _base_url(registry.get_llm("default")).startswith("http://remote-box:11434/v1")


def test_unknown_provider_is_rejected_at_registration_and_names_the_valid_ones():
    registry = _registry()
    with pytest.raises(ValueError) as exc:
        registry.register_llm(AgentConfig(provider="anthropic", model="claude"))

    message = str(exc.value)
    assert "anthropic" in message
    for provider in ("openai", "openrouter", "ollama"):
        assert provider in message


def test_empty_model_is_rejected_at_registration():
    registry = _registry()
    with pytest.raises(ValueError, match="model"):
        registry.register_llm(AgentConfig(provider="openai", model="   "))


def test_clients_are_built_lazily_and_cached():
    registry = _registry()
    registry.register_llm(
        AgentConfig(provider="openai", model="gpt-4o", api_key="sk-test", name="default")
    )

    assert registry.llms == {}

    first = registry.get_llm("default")
    assert registry.get_llm("default") is first


def test_missing_key_registers_but_fails_with_an_actionable_error_on_first_use(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    registry = _registry()

    # Registration succeeds: a key that is not set yet is not a misconfiguration.
    registry.register_llm(
        AgentConfig(
            provider="openai",
            model="gpt-4o",
            api_key="${env:OPENAI_API_KEY}",
            name="decomposer",
        )
    )

    with pytest.raises(ValueError) as exc:
        registry.get_llm("decomposer")

    message = str(exc.value)
    assert "decomposer" in message
    assert "openai" in message
    assert "OPENAI_API_KEY" in message
    # Not the raw upstream failure, which tells the user nothing actionable.
    assert "Missing credentials" not in message


def test_missing_key_error_is_not_the_raw_openai_error(monkeypatch):
    import openai

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    registry = _registry()
    registry.register_llm(
        AgentConfig(provider="openrouter", model="anthropic/claude-sonnet-4.5")
    )

    with pytest.raises(ValueError) as exc:
        registry.get_llm("default")

    assert not isinstance(exc.value, openai.OpenAIError)
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_provider_key_can_still_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    registry = _registry()
    registry.register_llm(AgentConfig(provider="openai", model="gpt-4o", name="default"))

    assert "api.openai.com" in _base_url(registry.get_llm("default"))


def test_get_llm_falls_back_to_the_default_agent():
    registry = _registry()
    registry.register_llm(
        AgentConfig(provider="openai", model="gpt-4o", api_key="sk-test", name="default")
    )

    assert registry.get_llm("decomposer") is registry.get_llm("default")


def test_get_llm_without_a_default_reports_the_missing_name():
    registry = _registry()
    with pytest.raises(ValueError) as exc:
        registry.get_llm("decomposer")

    message = str(exc.value)
    assert "decomposer" in message
    assert "default" in message


def test_listed_configs_never_expose_the_api_key():
    registry = _registry()
    registry.register_llm(
        AgentConfig(provider="openai", model="gpt-4o", api_key="sk-secret", name="default")
    )

    assert "api_key" not in registry.get_llm_config("default")
    assert "api_key" not in registry.list_llms()["default"]
