"""A secret provider that is configured but cannot start fails startup.

Before, the failure was logged and swallowed, so the first sign of it was an
"Unknown secret provider ID" much later, when a datasource or an LLM key
referenced the provider. Now ``SecretManager.configure`` raises at once, naming
the provider and the reason.
"""
import sys
import types

import pytest

from nl2sql.secrets import SecretManager, SecretProviderConfig

FAKE_TOKEN = "-".join(["fake", "vault", "token"])


def _vault(token: str = FAKE_TOKEN) -> SecretProviderConfig:
    return SecretProviderConfig(id="vault", type="hashicorp", url="https://vault", token=token)


def _hvac_client(monkeypatch, client_cls):
    monkeypatch.setitem(sys.modules, "hvac", types.SimpleNamespace(Client=client_cls))


def test_a_provider_whose_package_is_missing_fails_with_the_install_hint(monkeypatch):
    # ``None`` in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "hvac", None)

    with pytest.raises(ValueError) as excinfo:
        SecretManager().configure([_vault()])

    message = str(excinfo.value)
    assert "'vault'" in message
    assert "nl2sql-engine[hashicorp]" in message


def test_a_provider_that_raises_on_init_fails_with_its_reason(monkeypatch):
    class Broken:
        def __init__(self, **_kwargs):
            raise RuntimeError("vault is sealed")

    _hvac_client(monkeypatch, Broken)

    with pytest.raises(ValueError) as excinfo:
        SecretManager().configure([_vault()])

    message = str(excinfo.value)
    assert "'vault'" in message
    assert "vault is sealed" in message


def test_a_provider_whose_credential_env_var_is_unset_names_the_variable(monkeypatch):
    class Recorder:
        def __init__(self, **_kwargs):
            pass

    _hvac_client(monkeypatch, Recorder)
    monkeypatch.delenv("NL2SQL_TEST_UNSET_VAULT_TOKEN", raising=False)

    with pytest.raises(ValueError) as excinfo:
        SecretManager().configure([_vault("${env:NL2SQL_TEST_UNSET_VAULT_TOKEN}")])

    message = str(excinfo.value)
    assert "'vault'" in message
    assert "NL2SQL_TEST_UNSET_VAULT_TOKEN" in message


def test_the_failure_message_never_carries_the_resolved_credential(monkeypatch):
    class Leaky:
        def __init__(self, token=None, **_kwargs):
            raise RuntimeError(f"bad token {token}")

    _hvac_client(monkeypatch, Leaky)
    monkeypatch.setenv("NL2SQL_TEST_VAULT_TOKEN", FAKE_TOKEN)

    with pytest.raises(ValueError) as excinfo:
        SecretManager().configure([_vault("${env:NL2SQL_TEST_VAULT_TOKEN}")])

    assert FAKE_TOKEN not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_the_env_provider_and_an_empty_secrets_file_still_start(monkeypatch):
    monkeypatch.setenv("NL2SQL_TEST_SECRET", "value")
    manager = SecretManager()

    manager.configure([])
    manager.configure([SecretProviderConfig(id="env", type="env")])

    assert manager.resolve("${env:NL2SQL_TEST_SECRET}") == "value"
