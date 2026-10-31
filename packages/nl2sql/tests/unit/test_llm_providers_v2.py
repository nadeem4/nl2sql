"""LLM providers, interface version 2: ``openai_compatible`` and its presets, and the agent fields
``structured_output``, ``max_tokens``, ``timeout_s``, ``max_retries`` and ``headers``.

See docs/architecture/phase2.md, section 5, "LLM and embeddings providers".
"""
import json
import tomllib
import types
from pathlib import Path

import pytest
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from typer.testing import CliRunner

from nl2sql.config import LLM, AgentLLM
from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.llm.providers import LLM_PROVIDERS, ResolvedAgent
from nl2sql.llm.registry import PROVIDER_PRESETS, agents_from_config
from nl2sql.llm.wires import structured
from nl2sql.registry import INTERFACE_VERSIONS, Registry, RegistryError
from nl2sql.secrets import SecretManager
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

FAKE_KEY = "-".join(["sk", "fake", "key", "for", "tests", "only"])
HEADER_SECRET = "-".join(["header", "secret", "value"])
PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
NEW_FIELDS = {"structured_output": "json_mode", "max_tokens": 4000, "timeout_s": 30.0, "max_retries": 5,
              "headers": {"X-Team": "analytics"}}


def _resolved(**fields):
    return ResolvedAgent(name="default", model="m", temperature=None, **fields)


def test_llm_interface_is_version_2():
    assert INTERFACE_VERSIONS["llm"] == 2
    for provider in LLM_PROVIDERS.values():
        assert provider.interface_version == 2
        assert provider.structured_output in ("tool_calling", "json_schema", "json_mode")


def test_built_in_llm_providers_are_openai_anthropic_openai_compatible_its_presets_and_replay():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["entry-points"]["nl2sql.llms"]

    assert set(declared) == {"openai", "anthropic", "openai_compatible", "openrouter", "ollama", "replay"}
    # `replay` is a provider of its own, not a preset: it has no key variable, no endpoint and no client library.
    assert set(LLM_PROVIDERS) == set(PROVIDER_PRESETS) == set(declared) - {"replay"}
    assert {name for name, p in PROVIDER_PRESETS.items() if p.preset_of} == {"openrouter", "ollama"}
    assert {p.preset_of for p in PROVIDER_PRESETS.values() if p.preset_of} == {"openai_compatible"}


def test_default_structured_output_follows_the_design():
    assert {name: p.structured_output for name, p in LLM_PROVIDERS.items()} == {
        "openai": "tool_calling", "anthropic": "tool_calling", "openai_compatible": "json_schema",
        "openrouter": "tool_calling", "ollama": "tool_calling"}


def test_openrouter_and_ollama_build_through_openai_compatible(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    compatible = LLM_PROVIDERS["openai_compatible"]

    for name in ("openrouter", "ollama"):
        assert type(LLM_PROVIDERS[name]) is type(compatible)
        client = LLM_PROVIDERS[name].create(_resolved())
        assert client.openai_api_base == PROVIDER_PRESETS[name].base_url

    ollama = LLM_PROVIDERS["ollama"].create(_resolved(base_url="http://gpu-box:11434/v1"))
    assert ollama.openai_api_base == "http://gpu-box:11434/v1"


def test_init_provider_openrouter_still_works(tmp_path):
    from nl2sql.cli.main import app
    from nl2sql.config import load_config

    result = CliRunner().invoke(app, ["init", str(tmp_path), "--provider", "openrouter"])

    assert result.exit_code == 0, result.output
    assert "OPENROUTER_API_KEY" in result.output
    assert load_config(tmp_path / "nl2sql.yaml").llm.provider == "openrouter"


def test_openai_compatible_requires_base_url():
    with pytest.raises(ValueError, match="base_url"):
        LLM_PROVIDERS["openai_compatible"].create(_resolved())

    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai_compatible", model="qwen3-32b"))
    with pytest.raises(ValueError, match="base_url"):
        registry.get_llm("default")


def test_openai_compatible_sends_a_placeholder_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)  # OpenAI's key never goes to another server
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai_compatible", model="qwen3-32b",
                                      base_url="http://vllm:8000/v1"))

    client = registry.get_llm("default")

    assert client.openai_api_base == "http://vllm:8000/v1"
    key = client.openai_api_key.get_secret_value()
    assert key and key != FAKE_KEY
    assert key == PROVIDER_PRESETS["openai_compatible"].api_key_placeholder


def test_agent_inherits_each_new_field():
    llm = LLM(provider="openai", model="gpt-5.4", **NEW_FIELDS,
              agents={"ast_planner": AgentLLM(model="gpt-4.1"),
                      "refiner": AgentLLM(structured_output="json_schema", max_tokens=100, timeout_s=5,
                                          max_retries=0, headers={"X-Team": "planning"})})

    agents = agents_from_config(llm)

    planner, refiner = agents["astplanner"], agents["refiner"]
    for field, value in NEW_FIELDS.items():
        got = getattr(planner, field)
        if field == "headers":
            got = {k: v.get_secret_value() for k, v in got.items()}
        assert got == value, field
    assert (refiner.structured_output, refiner.max_tokens, refiner.timeout_s, refiner.max_retries) == (
        "json_schema", 100, 5.0, 0)
    assert refiner.headers["X-Team"].get_secret_value() == "planning"


def test_new_fields_reach_the_openai_client():
    client = LLM_PROVIDERS["openai"].create(_resolved(api_key=FAKE_KEY, max_tokens=4000, timeout_s=30.0,
                                                      max_retries=5, headers={"X-Team": "analytics"}))

    assert client.max_tokens == 4000
    assert client.request_timeout == 30.0
    assert client.max_retries == 5
    assert client.default_headers == {"X-Team": "analytics"}


def test_new_fields_reach_the_anthropic_client():
    pytest.importorskip("langchain_anthropic")
    from nl2sql.llm.wires.anthropic_client import MAX_TOKENS

    default = LLM_PROVIDERS["anthropic"].create(_resolved(api_key=FAKE_KEY))
    client = LLM_PROVIDERS["anthropic"].create(_resolved(api_key=FAKE_KEY, max_tokens=8000, timeout_s=30.0,
                                                         max_retries=5, headers={"X-Team": "analytics"}))

    assert default.max_tokens == MAX_TOKENS
    assert client.max_tokens == 8000
    assert client.default_request_timeout == 30.0
    assert client.max_retries == 5
    assert client.default_headers == {"X-Team": "analytics"}


def test_header_references_are_resolved_when_the_client_is_built(monkeypatch):
    monkeypatch.setenv("NL2SQL_TEST_HEADER", HEADER_SECRET)
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai", model="gpt-5.4", api_key=FAKE_KEY,
                                      headers={"X-Token": "${env:NL2SQL_TEST_HEADER}"}))

    assert registry.get_llm("default").default_headers == {"X-Token": HEADER_SECRET}


def test_headers_never_print(monkeypatch):
    monkeypatch.setenv("NL2SQL_TEST_HEADER", HEADER_SECRET)
    llm = LLM(provider="openai", model="gpt-5.4", api_key=FAKE_KEY, headers={"X-Token": HEADER_SECRET},
              agents={"refiner": AgentLLM(headers={"X-Token": HEADER_SECRET})})
    agent = agents_from_config(llm)["default"]
    registry = LLMRegistry(SecretManager())
    registry.replace_llms(agents_from_config(llm))
    from nl2sql.llm.client_cache import ClientKey

    printed = [repr(llm), str(llm), repr(llm.agents["refiner"]), repr(agent), str(agent),
               agent.model_dump_json(), repr(_resolved(headers={"X-Token": HEADER_SECRET})),
               str(ClientKey.of(agent, FAKE_KEY)), json.dumps(registry.list_llms(), default=str)]

    for text in printed:
        assert HEADER_SECRET not in text, text
    assert "X-Token" in repr(llm)


class Pick(BaseModel):
    table: str


def test_json_mode_puts_the_schema_in_the_prompt():
    server = FakeLLMServer(rules=[Rule("plain", json.dumps({"table": "orders"}), when='"table"')]).start()
    try:
        registry = LLMRegistry(SecretManager())
        registry.register_llm(AgentConfig(provider="openai_compatible", model="qwen3-32b",
                                          base_url=server.base_url, structured_output="json_mode"))
        chain = ChatPromptTemplate.from_messages([("system", "Pick a table."), ("human", "{q}")]) | structured(
            registry.get_llm("default"), Pick)

        answer = chain.invoke({"q": "Where are orders kept?"})
    finally:
        server.stop()

    assert answer == Pick(table="orders")
    [call] = server.calls
    assert call["body"]["response_format"] == {"type": "json_object"}
    prompt = "\n".join(str(m["content"]) for m in call["body"]["messages"])
    assert json.dumps(Pick.model_json_schema()) in prompt
    assert "Where are orders kept?" in prompt


def test_structured_output_picks_the_method_on_the_wire():
    server = FakeLLMServer(rules=[Rule("Pick", {"table": "orders"})]).start()
    try:
        registry = LLMRegistry(SecretManager())
        registry.replace_llms({
            "default": AgentConfig(provider="openai_compatible", model="m", base_url=server.base_url),
            "refiner": AgentConfig(provider="openai_compatible", model="m", base_url=server.base_url,
                                   structured_output="tool_calling", name="refiner")})
        prompt = ChatPromptTemplate.from_template("{q}")
        (prompt | structured(registry.get_llm("default"), Pick)).invoke({"q": "x"})
        (prompt | structured(registry.get_llm("refiner"), Pick)).invoke({"q": "x"})
    finally:
        server.stop()

    first, second = (call["body"] for call in server.calls)
    assert first["response_format"]["type"] == "json_schema" and "tools" not in first
    assert second["tools"][0]["function"]["name"] == "Pick"


def test_provider_version_1_is_rejected():
    registry = Registry.empty()
    registry.add_llm("gemini", types.SimpleNamespace(interface_version=1, requires=(), install_hint="",
                                                     structured_output="tool_calling"))

    with pytest.raises(RegistryError, match="interface 1; this engine needs 2"):
        registry.load("llm", "gemini")
