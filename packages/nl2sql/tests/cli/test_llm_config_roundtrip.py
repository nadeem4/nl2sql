from __future__ import annotations

from nl2sql.configs import AgentConfig, ConfigManager, LLMFileConfig
from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig as RegistryAgentConfig
from nl2sql.secrets import SecretManager
from nl2sql.cli.generators.llm import LLMGenerator


def test_openrouter_agent_survives_a_generate_then_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")

    agent = AgentConfig(
        provider="openrouter",
        model="anthropic/claude-sonnet-4.5",
        base_url="https://openrouter.ai/api/v1",
        api_key="${env:OPENROUTER_API_KEY}",
    )
    content = LLMGenerator.generate(LLMFileConfig(default=agent))

    # The secret reference is written literally, never the resolved value.
    assert "${env:OPENROUTER_API_KEY}" in content
    assert "sk-or-secret" not in content

    llm_path = tmp_path / "configs" / "llm.yaml"
    llm_path.parent.mkdir(parents=True, exist_ok=True)
    llm_path.write_text(content, encoding="utf-8")

    loaded = ConfigManager(tmp_path).load_llm(llm_path)

    assert loaded.default.provider == "openrouter"
    assert loaded.default.model == "anthropic/claude-sonnet-4.5"
    assert loaded.default.base_url == "https://openrouter.ai/api/v1"
    # The reference is carried through as-is; LLMRegistry resolves it at
    # registration time, so the secret never lands in the config file.
    assert loaded.default.api_key.get_secret_value() == "${env:OPENROUTER_API_KEY}"

    registry = LLMRegistry(SecretManager())
    registry.register_llms({"default": RegistryAgentConfig(**loaded.default.model_dump())})
    client = registry.get_llm("default")
    assert str(client.client._client.base_url).startswith("https://openrouter.ai/api/v1")
    assert client.openai_api_key.get_secret_value() == "sk-or-secret"
