"""Tier 2 config choices: `--model MODEL` and the built-in `--llm` presets."""
import pathlib

import pytest

from nl2sql.evaluation import presets
from nl2sql.evaluation.tier2 import LLM_NODES, node_agents


def test_a_verified_model_goes_on_every_llm_node_with_its_provider_key_and_temperature():
    cfg = presets.model_config("gpt-5.4")
    agents = node_agents(cfg)
    assert set(agents) == set(LLM_NODES)
    for agent in agents.values():
        assert (agent.provider, agent.model, agent.temperature) == ("openai", "gpt-5.4", 0.0)
        assert agent.api_key.get_secret_value() == "${env:OPENAI_API_KEY}"
        assert agent.base_url is None
    assert set(cfg.agents) == set(LLM_NODES.values())
    assert cfg.agents["astplanner"].name == "astplanner"


def test_a_claude_model_is_anthropic_and_sends_no_temperature():
    agent = presets.model_config("claude-opus-5").default
    assert (agent.provider, agent.temperature) == ("anthropic", None)
    assert agent.api_key.get_secret_value() == "${env:ANTHROPIC_API_KEY}"
    assert presets.model_config("claude-haiku-4-5").default.temperature == 0.0
    assert presets.model_config("gpt-5.5").default.temperature is None


def test_an_unverified_model_needs_provider_slash_model():
    agent = presets.model_config("openrouter/meta-llama/llama-3.3-70b-instruct").default
    assert (agent.provider, agent.model) == ("openrouter", "meta-llama/llama-3.3-70b-instruct")
    assert agent.api_key.get_secret_value() == "${env:OPENROUTER_API_KEY}" and agent.temperature == 0.0
    ollama = presets.model_config("ollama/llama3").default
    assert (ollama.provider, ollama.model, ollama.api_key) == ("ollama", "llama3", None)
    with pytest.raises(ValueError, match="provider/model"):
        presets.model_config("gpt-9-turbo")
    with pytest.raises(ValueError, match="Unknown provider 'acme'"):
        presets.model_config("acme/model-x")


def test_presets_resolve_by_name_or_suffix():
    assert presets.resolve_llm_spec("gpt-5.4") == ("gpt-5.4", presets.PRESETS_DIR / "gpt-5.4.yaml")
    full = ("gpt-5.4-mini-helpers", presets.PRESETS_DIR / "gpt-5.4-mini-helpers.yaml")
    assert presets.resolve_llm_spec("mini-helpers") == full
    assert presets.resolve_llm_spec("gpt-5.4-mini-helpers") == full
    assert presets.resolve_llm_spec("claude-planner")[0] == "claude-planner"
    with pytest.raises(ValueError, match="No preset named 'nope'"):
        presets.resolve_llm_spec("nope")


def test_a_bare_yaml_path_and_name_equals_path():
    assert presets.resolve_llm_spec("cfg/my-run.yaml") == ("my-run", pathlib.Path("cfg/my-run.yaml"))
    assert presets.resolve_llm_spec("a=cfg/a.yml") == ("a", pathlib.Path("cfg/a.yml"))
    with pytest.raises(ValueError, match="NAME=PATH"):
        presets.resolve_llm_spec("=cfg/a.yaml")


def test_every_preset_is_a_valid_config_on_priced_models():
    from nl2sql.configs.manager import ConfigManager
    from nl2sql.evaluation.tier2 import check_prices

    listed = presets.list_presets()
    assert [name for name, _ in listed] == ["claude-planner", "gpt-5.4", "gpt-5.4-mini-helpers"]
    check_prices({name: cfg for name, cfg in listed})
    for name, cfg in listed:
        assert cfg == ConfigManager().load_llm(presets.PRESETS_DIR / f"{name}.yaml")
