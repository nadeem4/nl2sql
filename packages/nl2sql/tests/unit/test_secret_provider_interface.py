"""The built-in secret providers implement ``nl2sql_sdk.SecretProvider``.

Each one declares ``interface_version = 1`` and a strict ``config_model`` for
its block in the secrets config, and is built with ``from_config``. A provider
reads its settings from that block only: the 0.4 fallbacks to ``VAULT_ADDR``,
``VAULT_TOKEN`` and ``AZURE_KEYVAULT_URL`` are gone. See
docs/architecture/configuration_v2.md, "Interfaces and versions".
"""
import sys
import types

import pytest
from pydantic import ValidationError

from nl2sql_sdk import SecretProvider
from nl2sql.secrets import SecretManager, SecretProviderConfig
from nl2sql.secrets.factory import BUILTIN_PROVIDERS, provider_class

FAKE_TOKEN = "-".join(["fake", "vault", "token"])


def test_the_built_in_provider_types():
    assert set(BUILTIN_PROVIDERS) == {"env", "aws", "azure", "hashicorp"}


@pytest.mark.parametrize("type_name", sorted(BUILTIN_PROVIDERS))
def test_built_in_provider_declares_interface_version_1(type_name):
    assert provider_class(type_name).interface_version == 1


@pytest.mark.parametrize("type_name", sorted(BUILTIN_PROVIDERS))
def test_built_in_provider_rejects_an_unknown_key(type_name):
    with pytest.raises(ValidationError, match="bogus"):
        provider_class(type_name).config_model.model_validate({"bogus": 1})


def test_env_provider_satisfies_the_sdk_protocol(monkeypatch):
    cls = provider_class("env")
    provider = cls.from_config(cls.config_model())
    monkeypatch.setenv("NL2SQL_TEST_SECRET", "value")

    assert isinstance(provider, SecretProvider)
    assert provider.get_secret("NL2SQL_TEST_SECRET") == "value"


def test_the_0_4_name_hashi_is_not_a_provider_type():
    with pytest.raises(ValidationError, match="hashicorp"):
        SecretProviderConfig(id="vault", type="hashi", url="https://vault", token=FAKE_TOKEN)


def test_an_unknown_key_in_a_provider_block_fails_when_the_config_loads():
    with pytest.raises(ValidationError, match="tokn"):
        SecretProviderConfig(id="vault", type="hashicorp", url="https://vault", tokn=FAKE_TOKEN)


def _fake_hvac(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setitem(sys.modules, "hvac", types.SimpleNamespace(Client=Client))
    return seen


def test_hashicorp_needs_url_and_token_in_its_block(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://from-env")
    monkeypatch.setenv("VAULT_TOKEN", FAKE_TOKEN)

    with pytest.raises(ValidationError) as excinfo:
        SecretProviderConfig(id="vault", type="hashicorp")

    message = str(excinfo.value)
    assert "url" in message and "token" in message


def test_hashicorp_uses_its_block_and_never_the_environment(monkeypatch):
    seen = _fake_hvac(monkeypatch)
    monkeypatch.setenv("VAULT_ADDR", "https://from-env")

    SecretManager().configure([
        SecretProviderConfig(id="vault", type="hashicorp", url="https://vault", token=FAKE_TOKEN)
    ])

    assert seen == {"url": "https://vault", "token": FAKE_TOKEN}


def test_azure_needs_vault_url_in_its_block(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://from-env")

    with pytest.raises(ValidationError, match="vault_url"):
        SecretProviderConfig(id="kv", type="azure")


def test_a_missing_client_library_fails_with_the_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "hvac", None)  # None makes the import raise ImportError

    with pytest.raises(ValueError) as excinfo:
        SecretManager().configure([
            SecretProviderConfig(id="vault", type="hashicorp", url="https://vault", token=FAKE_TOKEN)
        ])

    message = str(excinfo.value)
    assert "'vault'" in message
    assert "nl2sql-engine[hashicorp]" in message
