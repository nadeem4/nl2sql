"""Secrets held by settings and config models must never print.

A failing test that printed the settings object once showed a live
``OPENAI_API_KEY`` in full: pytest had loaded the repo-root ``.env``. Any
``repr``, ``str``, log line or traceback holding the settings object, or a
config model holding a key, leaked every configured secret. The values are now
``SecretStr`` and are read only where they are used, so these tests check both
halves: nothing prints the value, and everything that needs it still gets it.

Fake keys are assembled at run time so no key-shaped literal is committed.
"""
from __future__ import annotations

import pytest
import yaml
from pydantic import SecretStr

from nl2sql.common.settings import Settings
from nl2sql.configs.llm import AgentConfig, LLMFileConfig
from nl2sql.datasources.models import ConnectionConfig

FAKE_KEY = "-".join(["sk", "proj", "neverprint" + "q" * 24 + "5e1d"])
FAKE_PASSWORD = "pw-" + "neverprint" + "p" * 12


def _all_renderings(obj) -> str:
    parts = [repr(obj), str(obj), f"{obj}", str(obj.model_dump()), repr(obj.model_dump()),
             str(obj.model_dump(mode="json")), obj.model_dump_json()]
    return "\n".join(parts)


@pytest.fixture()
def hermetic_settings(monkeypatch):
    """Settings built from the environment only, never from any ``.env`` file."""
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    return Settings(_env_file=None)


def test_settings_never_print_their_secrets(hermetic_settings):
    text = _all_renderings(hermetic_settings)
    assert FAKE_KEY not in text


def test_settings_still_hold_the_real_values(hermetic_settings):
    assert hermetic_settings.openai_api_key.get_secret_value() == FAKE_KEY


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
        assert FAKE_KEY not in repr(registry._configs)
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
    from nl2sql.common.settings import settings
    from nl2sql.tracing.trace import collect_secrets, settings_snapshot

    monkeypatch.setattr(settings, "openai_api_key", SecretStr(FAKE_KEY))
    found = collect_secrets(object())
    assert FAKE_KEY in found
    assert "**********" not in found
    snapshot = str(settings_snapshot())
    assert FAKE_KEY not in snapshot


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


# --- config writers still write the real value or the reference --------------


def test_llm_yaml_writer_keeps_the_env_reference():
    from nl2sql.cli.demo.defaults import DEMO_LLM_CONFIG
    from nl2sql.cli.generators.llm.generator import LLMGenerator

    written = yaml.safe_load(LLMGenerator.generate(LLMFileConfig(**DEMO_LLM_CONFIG)))
    assert written["default"]["api_key"] == "${env:OPENAI_API_KEY}"


def test_llm_yaml_writer_keeps_a_literal_key():
    from nl2sql.cli.generators.llm.generator import LLMGenerator

    config = LLMFileConfig(default=AgentConfig(provider="openai", model="gpt-5.4", api_key=FAKE_KEY))
    written = yaml.safe_load(LLMGenerator.generate(config))
    assert written["default"]["api_key"] == FAKE_KEY
    assert "*" not in LLMGenerator.generate(config)


def test_demo_scaffold_writes_the_real_key_and_the_reference(tmp_path):
    from rich.console import Console

    from nl2sql.cli.demo.manager import DemoManager

    DemoManager(Console(quiet=True), tmp_path).setup_demo(api_key=FAKE_KEY)
    env_text = (tmp_path / ".env.demo").read_text(encoding="utf-8")
    assert f"OPENAI_API_KEY={FAKE_KEY}" in env_text
    llm = yaml.safe_load((tmp_path / "configs" / "llm.demo.yaml").read_text(encoding="utf-8"))
    assert llm["default"]["api_key"] == "${env:OPENAI_API_KEY}"
    assert "**********" not in env_text


def test_persisted_panel_key_is_the_real_value(tmp_path):
    from nl2sql.cli.demo.llm_config import persist_api_key

    env_file = tmp_path / ".env.demo"
    env_file.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    persist_api_key(env_file, FAKE_KEY)
    assert env_file.read_text(encoding="utf-8") == f"OPENAI_API_KEY={FAKE_KEY}\n"

