"""The built-in LLM and embeddings providers the registry hands out.

The four LLM providers are made from ``PROVIDER_PRESETS``, which stays the one
table of key variables. See docs/architecture/configuration_v2.md,
"Interfaces and versions".
"""
import pytest

from nl2sql.config import Embeddings
from nl2sql.llm.providers import LLM_PROVIDERS, ResolvedAgent
from nl2sql.llm.registry import PROVIDER_PRESETS
from nl2sql.registry import Registry

FAKE_KEY = "-".join(["sk", "fake", "key", "for", "tests", "only"])


def test_one_llm_provider_per_preset():
    assert set(LLM_PROVIDERS) == set(PROVIDER_PRESETS)
    for name, provider in LLM_PROVIDERS.items():
        assert provider.api_key_env == PROVIDER_PRESETS[name].api_key_env
        assert provider.interface_version == 1
        assert provider.label


def test_the_registry_hands_out_the_preset_providers():
    assert Registry.default().load("llm", "anthropic") is LLM_PROVIDERS["anthropic"]


def test_anthropic_names_its_extra():
    anthropic = LLM_PROVIDERS["anthropic"]
    assert anthropic.requires == ("langchain_anthropic",)
    assert "nl2sql-engine[anthropic]" in anthropic.install_hint


def test_create_builds_a_client_with_the_given_key():
    client = LLM_PROVIDERS["openai"].create(ResolvedAgent(name="default", model="gpt-5.4", temperature=0.0,
                                                          api_key=FAKE_KEY))
    assert client.model_name == "gpt-5.4"
    assert client.openai_api_key.get_secret_value() == FAKE_KEY


def test_create_falls_back_to_the_provider_variable(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    client = LLM_PROVIDERS["openrouter"].create(ResolvedAgent(name="default", model="m", temperature=None))
    assert client.openai_api_key.get_secret_value() == FAKE_KEY
    assert client.openai_api_base == PROVIDER_PRESETS["openrouter"].base_url


def test_create_without_a_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        LLM_PROVIDERS["openai"].create(ResolvedAgent(name="default", model="gpt-5.4", temperature=0.0))


def test_ollama_needs_no_key(monkeypatch):
    client = LLM_PROVIDERS["ollama"].create(ResolvedAgent(name="default", model="llama3", temperature=0.0))
    assert client.openai_api_key.get_secret_value() == PROVIDER_PRESETS["ollama"].api_key_placeholder


def test_local_embeddings_need_no_key():
    from nl2sql.indexing.embeddings import LocalEmbeddings

    local = Registry.default().load("embeddings", "local")
    assert isinstance(local.create(Embeddings(provider="local")), LocalEmbeddings)


def test_local_embeddings_reject_another_model():
    local = Registry.default().load("embeddings", "local")
    with pytest.raises(ValueError, match=local.default_model):
        local.create(Embeddings(provider="local", model="text-embedding-3-small"))


def test_openai_embeddings_use_the_configured_key_and_default_model():
    openai = Registry.default().load("embeddings", "openai")
    embedder = openai.create(Embeddings(provider="openai", api_key=FAKE_KEY))
    assert embedder.model == openai.default_model
    assert embedder.openai_api_key.get_secret_value() == FAKE_KEY


def test_openai_embeddings_without_a_key_name_the_variable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    openai = Registry.default().load("embeddings", "openai")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        openai.create(Embeddings(provider="openai"))
