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


def test_unsupported_provider_still_rejected():
    registry = _registry()
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        registry.register_llm(AgentConfig(provider="ollama", model="llama3"))


def test_get_llm_without_a_default_reports_the_missing_name():
    registry = _registry()
    with pytest.raises(ValueError) as exc:
        registry.get_llm("decomposer")

    message = str(exc.value)
    assert "decomposer" in message
    assert "default" in message
