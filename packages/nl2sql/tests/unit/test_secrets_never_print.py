"""Secrets held by config models must never print.

A failing test that printed 0.4's settings object once showed a live
``OPENAI_API_KEY`` in full: pytest had loaded the repo-root ``.env``. Any
``repr``, ``str``, log line or traceback holding a model with a key leaked
every configured secret. The values are ``SecretStr`` and are read only where
they are used, so these tests check both halves: nothing prints the value, and
everything that needs it still gets it.

Fake keys are assembled at run time so no key-shaped literal is committed.
"""
from __future__ import annotations

import pytest
import yaml
from pydantic import SecretStr

from nl2sql.llm.models import AgentConfig, LLMFileConfig
from nl2sql.datasources.models import ConnectionConfig

FAKE_KEY = "-".join(["sk", "proj", "neverprint" + "q" * 24 + "5e1d"])
FAKE_PASSWORD = "pw-" + "neverprint" + "p" * 12


def _all_renderings(obj) -> str:
    parts = [repr(obj), str(obj), f"{obj}", str(obj.model_dump()), repr(obj.model_dump()),
             str(obj.model_dump(mode="json")), obj.model_dump_json()]
    return "\n".join(parts)


def test_llm_config_models_never_print_the_key():
    agent = AgentConfig(provider="openai", model="gpt-5.4", api_key=FAKE_KEY)
    envelope = LLMFileConfig(default=agent, agents={"planner": agent})
    for obj in (agent, envelope):
        assert FAKE_KEY not in _all_renderings(obj)


def test_connection_config_never_prints_a_password():
    conn = ConnectionConfig(type="postgres", host="db", user="app", password=FAKE_PASSWORD)
    assert FAKE_PASSWORD not in repr(conn)
    assert FAKE_PASSWORD not in str(conn)
    assert "db" in repr(conn)  # non-secret fields still show
    # The registry hands model_dump() to the adapter, which needs the real value.
    assert conn.model_dump()["password"] == FAKE_PASSWORD


# --- the value still reaches every place that uses it ------------------------


def test_llm_client_is_built_with_the_real_key(monkeypatch):
    from nl2sql.llm.registry import LLMRegistry
    from nl2sql.secrets import SecretManager
    from nl2sql.testing.fake_llm import FakeLLMServer, Rule

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    server = FakeLLMServer([Rule(name="plain", payload="pong")]).start()
    try:
        registry = LLMRegistry(SecretManager())
        registry.register_llm(AgentConfig(provider="openai", model="gpt-5.4", api_key=FAKE_KEY,
                                          base_url=server.base_url, temperature=None))
        client = registry.get_llm("default")
        assert FAKE_KEY not in repr(registry.current.configs)
        client.invoke("ping")
    finally:
        server.stop()
    assert server.calls and server.calls[0]["authorization"] == f"Bearer {FAKE_KEY}"


def test_llm_client_resolves_an_env_reference(monkeypatch):
    from nl2sql.llm.registry import LLMRegistry
    from nl2sql.secrets import SecretManager

    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai", model="gpt-5.4",
                                      api_key="${env:OPENAI_API_KEY}"))
    assert registry.get_llm("default").openai_api_key.get_secret_value() == FAKE_KEY


def test_embeddings_get_the_real_key(monkeypatch):
    from nl2sql.config import Embeddings
    from nl2sql.registry import Registry

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = Registry.default().load("embeddings", "openai")
    embeddings = provider.create(Embeddings(provider="openai", api_key=FAKE_KEY))
    assert embeddings.openai_api_key.get_secret_value() == FAKE_KEY


def test_embeddings_refuse_an_empty_secret(monkeypatch):
    from nl2sql.config import Embeddings
    from nl2sql.registry import Registry

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = Registry.default().load("embeddings", "openai")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        provider.create(Embeddings(provider="openai", api_key=""))


def test_trace_redactor_collects_the_real_value_not_the_mask(monkeypatch):
    from types import SimpleNamespace

    from nl2sql.tracing.trace import collect_secrets

    client = SimpleNamespace(openai_api_key=SecretStr(FAKE_KEY))
    found = collect_secrets(SimpleNamespace(llm_registry=SimpleNamespace(llms={"default": client})))
    assert FAKE_KEY in found
    assert "**********" not in found


def _fake_secret_clients(monkeypatch) -> dict:
    """Stand-ins for hvac and the Azure SDK that record what they were given."""
    import sys
    import types

    seen = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setitem(sys.modules, "hvac", types.SimpleNamespace(Client=Recorder))
    monkeypatch.setitem(sys.modules, "azure.identity",
                        types.SimpleNamespace(ClientSecretCredential=Recorder, DefaultAzureCredential=Recorder))
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets",
                        types.SimpleNamespace(SecretClient=lambda **kwargs: None))
    return seen


@pytest.mark.parametrize("kind", ["azure", "hashicorp"])
def test_secret_provider_clients_get_the_plain_credential(kind, monkeypatch):
    from nl2sql.secrets import SecretManager, SecretProviderConfig

    seen = _fake_secret_clients(monkeypatch)

    if kind == "azure":
        config = SecretProviderConfig(id="kv", type="azure", vault_url="https://kv", client_id="c",
                                      tenant_id="t", client_secret=FAKE_PASSWORD)
        assert FAKE_PASSWORD not in repr(config)
        SecretManager().configure([config])
        assert seen["client_secret"] == FAKE_PASSWORD
    else:
        config = SecretProviderConfig(id="vault", type="hashicorp", url="https://vault", token=FAKE_PASSWORD)
        assert FAKE_PASSWORD not in repr(config)
        SecretManager().configure([config])
        assert seen["token"] == FAKE_PASSWORD


def test_secret_manager_resolves_an_env_reference_in_a_secret_field(monkeypatch):
    from nl2sql.secrets import SecretManager, SecretProviderConfig

    seen = _fake_secret_clients(monkeypatch)
    monkeypatch.setenv("NEVERPRINT_VAULT_TOKEN", FAKE_PASSWORD)

    manager = SecretManager()
    manager.configure([SecretProviderConfig(id="vault", type="hashicorp", url="https://vault",
                                            token="${env:NEVERPRINT_VAULT_TOKEN}")])
    assert seen["token"] == FAKE_PASSWORD
    assert "vault" in manager._providers


# --- the demo's .env keeps the real value; no YAML ever holds a key ----------------


def test_persisted_panel_key_is_the_real_value(tmp_path):
    from nl2sql.cli.demo.llm_config import persist_api_key

    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    persist_api_key(env_file, FAKE_KEY)
    assert env_file.read_text(encoding="utf-8") == f"OPENAI_API_KEY={FAKE_KEY}\n"


# --- the typed config (nl2sql.config) -----------------------------------------
#
# A value written as a literal (or given in code, as the demo's key is) is
# masked wherever the config prints; a ``${provider:key}`` reference is not a
# secret and stays visible. The engine still reads the real value.


def _config():
    from nl2sql.config import (LLM, AgentLLM, Config, Datasource, Embeddings, Policies, SecretProviderConfig,
                               Secrets, Storage)

    return Config(
        datasources=[Datasource(id="sales", type="postgres",
                                connection={"host": "db.example", "user": "app", "password": FAKE_PASSWORD,
                                            "token": "${env:SALES_TOKEN}"})],
        llm=LLM(provider="openai", model="gpt-5.4", api_key=FAKE_KEY,
                agents={"ast_planner": AgentLLM(provider="anthropic", api_key=FAKE_KEY + "-agent")}),
        embeddings=Embeddings(provider="openai", api_key="${env:OPENAI_API_KEY}"),
        policies=Policies(allow_all=True),
        storage=Storage(path="."),
        secrets=Secrets(providers=[SecretProviderConfig(id="vault", type="hashicorp", url="https://vault",
                                                        token=FAKE_PASSWORD)]),
    )


def _printed(obj) -> str:
    return "\n".join([repr(obj), str(obj), f"{obj}", f"{obj!r}"])


def test_the_config_never_prints_a_key_or_a_password():
    config = _config()
    for obj in (config, config.llm, config.llm.agents["ast_planner"], config.datasources[0], config.secrets):
        printed = _printed(obj)
        assert FAKE_KEY not in printed, type(obj).__name__
        assert FAKE_PASSWORD not in printed, type(obj).__name__


def test_the_config_shows_references_and_everything_that_is_not_a_secret():
    printed = _printed(_config())
    assert "${env:OPENAI_API_KEY}" in printed
    assert "${env:SALES_TOKEN}" in printed
    assert "db.example" in printed and "gpt-5.4" in printed and "https://vault" in printed


def test_the_config_still_holds_the_real_values():
    config = _config()
    assert config.llm.api_key == FAKE_KEY
    assert config.llm.agents["ast_planner"].api_key == FAKE_KEY + "-agent"
    assert config.datasources[0].connection["password"] == FAKE_PASSWORD
    assert config.model_dump()["llm"]["api_key"] == FAKE_KEY


# Short, so pydantic's error text cannot truncate it out of sight.
SHORT_SECRET = "np-" + "z" * 5


def _bad_llm():
    from nl2sql.config import LLM
    return LLM(api_key=SHORT_SECRET, model="")


def _bad_agent():
    from nl2sql.config import AgentLLM
    return AgentLLM(temperature={"api_key": SHORT_SECRET})


def _bad_datasource():
    from nl2sql.config import Datasource
    return Datasource(connection={"password": SHORT_SECRET}, id="sales")


def _bad_secret_provider():
    from nl2sql.config import SecretProviderConfig
    return SecretProviderConfig(token=SHORT_SECRET, id="")


@pytest.mark.parametrize("build", [_bad_llm, _bad_agent, _bad_datasource, _bad_secret_provider])
def test_a_config_error_never_prints_the_value(build):
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as caught:
        build()
    text = f"{caught.value}\n{caught.value!r}"
    assert SHORT_SECRET not in text


def test_a_demo_key_given_in_memory_never_prints_from_the_engine(tmp_path):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    from nl2sql import NL2SQL
    from nl2sql.cli.demo.llm_config import with_api_keys
    from nl2sql.config import Embeddings
    from nl2sql.testing.config import make_config

    config = make_config(tmp_path)
    config = config.model_copy(update={"llm": with_api_keys(config.llm, {"openai": FAKE_KEY}),
                                       "embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))})
    engine = NL2SQL(config)
    try:
        assert engine.config.llm.api_key == FAKE_KEY
        assert FAKE_KEY not in _printed(engine.config)
    finally:
        engine.close()

